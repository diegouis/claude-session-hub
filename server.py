"""Claude Session Hub — FastAPI application."""

import asyncio
import json
import logging
import os
import platform
import shlex
import shutil
import sqlite3
import subprocess
import time
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from indexer import DB_PATH, get_db, parse_jsonl, reindex_incremental, reindex_all
from detector import get_active_sessions, get_session_status, get_all_session_statuses

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ARCHIVE_BASE = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude"))) / "archive"
TRASH_DIR = BASE_DIR / "data" / "trash"

# ---------------------------------------------------------------------------
# Background file watcher
# ---------------------------------------------------------------------------

_watcher_task: Optional[asyncio.Task] = None
_sse_subscribers: list[asyncio.Queue] = []


async def _poll_for_changes():
    """Periodically check for file changes and reindex."""
    while True:
        await asyncio.sleep(30)
        try:
            loop = asyncio.get_event_loop()
            count = await loop.run_in_executor(None, reindex_incremental)
            if count > 0:
                event = {"event": "reindex_complete", "data": json.dumps({"changes": count})}
                for q in list(_sse_subscribers):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
        except Exception as e:
            logger.error("Background reindex error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: reindex, start watcher. Shutdown: stop watcher."""
    global _watcher_task
    logger.info("Running startup reindex...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, reindex_incremental)
    _watcher_task = asyncio.create_task(_poll_for_changes())
    logger.info("Background watcher started")
    yield
    if _watcher_task:
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Claude Session Hub", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    if not request.url.path.startswith("/static") and request.url.path != "/api/events":
        logger.info("%s %s → %d (%.0fms)", request.method, request.url.path, response.status_code, duration)
    return response

# Static files must be mounted AFTER API routes to avoid shadowing them,
# but FastAPI handles this correctly since we mount on /static prefix.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    return get_db()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _project_short_name(path: str) -> str:
    """Extract last path component as a short project name."""
    if not path:
        return "Unknown Project"
    parts = path.rstrip("/").split("/")
    return parts[-1] if parts else path


def _normalize_session(row_dict: dict, status: str = "idle") -> dict:
    """Transform a DB row dict into the shape the frontend expects.

    Frontend expects: id, title, project, status, message_count, model,
    total_tokens, file_size, updated_at, created_at, is_subagent,
    parent_session_id, source, git_branch, version, cwd, project_path.
    """
    total_in = row_dict.get("total_input_tokens") or 0
    total_out = row_dict.get("total_output_tokens") or 0
    label = row_dict.get("label")
    title = label if label else (row_dict.get("first_user_message") or "")
    return {
        "id": row_dict["session_id"],
        "title": title,
        "starred": bool(row_dict.get("starred")),
        "label": label,
        "project": _project_short_name(row_dict.get("project_path", "")),
        "project_path": row_dict.get("project_path", ""),
        "status": status,
        "message_count": row_dict.get("message_count", 0),
        "user_message_count": row_dict.get("user_message_count", 0),
        "assistant_message_count": row_dict.get("assistant_message_count", 0),
        "model": row_dict.get("model"),
        "total_tokens": total_in + total_out,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "file_size": row_dict.get("file_size_bytes", 0),
        "updated_at": row_dict.get("ended_at"),
        "created_at": row_dict.get("started_at"),
        "is_subagent": bool(row_dict.get("is_subagent")),
        "parent_session_id": row_dict.get("parent_session_id"),
        "source": row_dict.get("source", "live"),
        "git_branch": row_dict.get("git_branch"),
        "version": row_dict.get("version"),
        "cwd": row_dict.get("cwd"),
        "is_empty": bool(row_dict.get("is_empty")),
        "is_tiny": bool(row_dict.get("is_tiny")),
        "last_message_preview": row_dict.get("last_message_preview"),
    }


def _extract_text_from_content(content) -> str:
    """Pull plain text out of a message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _extract_tool_calls(content) -> list[dict]:
    """Extract tool_use blocks from content array."""
    if not isinstance(content, list):
        return []
    calls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append({
                "name": block.get("name", "unknown"),
                "input": block.get("input", {}),
                "id": block.get("id"),
            })
    return calls


def _extract_tool_result(content) -> Optional[str]:
    """Extract tool_result from content array. Returns combined text or None."""
    if not isinstance(content, list):
        return None
    results = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            inner = block.get("content", "")
            if isinstance(inner, str):
                results.append(inner)
            elif isinstance(inner, list):
                for rb in inner:
                    if isinstance(rb, dict) and rb.get("type") == "text":
                        results.append(rb.get("text", ""))
    return "\n".join(results) if results else None


def _get_resume_command(cwd: str, session_id: str) -> tuple[list[str], str]:
    """Build a platform-appropriate command to open a terminal with claude -r.

    Returns (command_args, description).
    """
    resume_cmd = f"cd {shlex.quote(cwd)} && echo 'Resuming session {session_id}...' && claude -r {session_id}"
    system = platform.system()

    if system == "Darwin":
        # macOS — use osascript to open Terminal.app
        return (
            ["osascript", "-e", f'tell app "Terminal" to do script "{resume_cmd}"'],
            "Terminal.app"
        )
    elif system == "Linux":
        # Try common Linux terminal emulators in order
        for term_cmd in [
            ["gnome-terminal", "--", "bash", "-c", resume_cmd + "; exec bash"],
            ["xterm", "-e", f"bash -c '{resume_cmd}; exec bash'"],
            ["konsole", "-e", "bash", "-c", resume_cmd + "; exec bash"],
        ]:
            if shutil.which(term_cmd[0]):
                return (term_cmd, term_cmd[0])
        # Fallback: just return the command, user copies it
        return (None, "no terminal found")
    elif system == "Windows":
        # Windows — use start cmd
        win_cmd = f'start cmd /k "cd /d {cwd} && claude -r {session_id}"'
        return (["cmd", "/c", win_cmd], "cmd.exe")
    else:
        return (None, f"unsupported platform: {system}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/sessions")
async def list_sessions(
    q: Optional[str] = None,
    project: Optional[str] = None,
    status: Optional[str] = Query(default="all"),
    sort: Optional[str] = Query(default="date"),
    order: Optional[str] = Query(default="desc"),
):
    conn = _get_conn()
    try:
        if q:
            fts_q = _sanitize_fts_query(q)
            rows = conn.execute("""
                SELECT s.* FROM sessions s
                JOIN session_fts f ON s.session_id = f.session_id
                WHERE session_fts MATCH ?
                ORDER BY rank
            """, (fts_q,)).fetchall()
        else:
            conditions = []
            params = []
            if project:
                conditions.append("project_path = ?")
                params.append(project)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            sort_col = {
                "date": "ended_at",
                "size": "file_size_bytes",
                "messages": "message_count",
            }.get(sort, "ended_at")
            order_dir = "ASC" if order == "asc" else "DESC"

            rows = conn.execute(
                f"SELECT * FROM sessions {where} ORDER BY {sort_col} {order_dir}",
                params,
            ).fetchall()

        raw_sessions = _rows_to_list(rows)

        # Compute statuses in bulk
        statuses = get_all_session_statuses(raw_sessions)

        sessions = []
        for s in raw_sessions:
            st = statuses.get(s["session_id"], "idle")
            sessions.append(_normalize_session(s, st))

        # Filter by status if requested
        if status and status != "all":
            sessions = [s for s in sessions if s["status"] == status]

        # Compute aggregate stats for response
        stats = {
            "total": len(sessions),
            "active": sum(1 for s in sessions if s["status"] == "active"),
            "tokens": sum(s["total_tokens"] for s in sessions),
            "projects": len(set(s["project"] for s in sessions)),
        }

        return {"sessions": sessions, "stats": stats, "total": len(sessions)}
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        session = _row_to_dict(row)
        active = get_active_sessions()
        st = get_session_status(
            session_id,
            file_mtime=session.get("file_mtime"),
            is_tiny=bool(session.get("is_tiny")),
            active_sessions=active,
        )

        return _normalize_session(session, st)
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT file_path FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        messages = _load_messages(row["file_path"], page, limit)
        return messages
    finally:
        conn.close()


def _load_messages(file_path: str, page: int, limit: int) -> dict:
    """Load paginated messages from a JSONL file.

    Returns messages in the format the frontend expects:
    {role, text, tool_calls: [{name, input}], tool_result}
    Uses 0-based page indexing.
    """
    all_messages = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_type = entry.get("type")
                if entry_type in ("file-history-snapshot", "last-prompt", "progress"):
                    continue

                message = entry.get("message", {})
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue

                content = message.get("content", "")
                text = _extract_text_from_content(content)
                tool_calls = _extract_tool_calls(content)
                tool_result = _extract_tool_result(content)

                msg = {
                    "role": role,
                    "text": text,
                    "timestamp": entry.get("timestamp"),
                    "uuid": entry.get("uuid"),
                }

                if tool_calls:
                    msg["tool_calls"] = tool_calls
                if tool_result is not None:
                    msg["tool_result"] = tool_result

                # Include model/usage for assistant messages
                if role == "assistant":
                    if message.get("model"):
                        msg["model"] = message["model"]
                    if message.get("usage"):
                        msg["usage"] = message["usage"]

                all_messages.append(msg)

    except (OSError, IOError) as e:
        logger.error("Failed to read %s: %s", file_path, e)

    total = len(all_messages)
    start = page * limit
    end = start + limit

    return {
        "messages": all_messages[start:end],
        "total": total,
        "page": page,
        "limit": limit,
    }


@app.get("/api/projects")
async def list_projects():
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT project_path,
                   COUNT(*) as session_count,
                   SUM(message_count) as total_messages,
                   SUM(total_input_tokens) as total_input_tokens,
                   SUM(total_output_tokens) as total_output_tokens,
                   MAX(ended_at) as last_activity
            FROM sessions
            GROUP BY project_path
            ORDER BY last_activity DESC
        """).fetchall()
        return {"projects": _rows_to_list(rows)}
    finally:
        conn.close()


def _sanitize_fts_query(raw: str) -> str:
    """Sanitize user input for FTS5 MATCH by quoting each token.

    FTS5 treats characters like -, ., :, *, (, ) as query syntax.
    We split on whitespace, wrap each token in double quotes, and
    join with spaces (implicit AND).
    """
    tokens = raw.split()
    if not tokens:
        return '""'
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " ".join(quoted)


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1)):
    conn = _get_conn()
    try:
        fts_query = _sanitize_fts_query(q)
        rows = conn.execute("""
            SELECT s.*,
                   snippet(session_fts, 1, '<mark>', '</mark>', '...', 40) as snippet,
                   rank
            FROM session_fts f
            JOIN sessions s ON s.session_id = f.session_id
            WHERE session_fts MATCH ?
            ORDER BY rank
            LIMIT 100
        """, (fts_query,)).fetchall()

        raw = _rows_to_list(rows)
        statuses = get_all_session_statuses(raw)

        results = []
        for r in raw:
            snippet = r.pop("snippet", "")
            r.pop("rank", None)
            st = statuses.get(r["session_id"], "idle")
            session = _normalize_session(r, st)
            session["snippet"] = snippet
            results.append(session)

        return {"results": results, "total": len(results), "query": q}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT cwd, is_subagent, parent_session_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        # Subagent sessions can't be resumed independently — resume the parent
        resume_id = session_id
        cwd = row["cwd"] or "~"
        if row["is_subagent"] and row["parent_session_id"]:
            resume_id = row["parent_session_id"]
            # Use the parent's cwd, not the subagent's
            parent_row = conn.execute(
                "SELECT cwd FROM sessions WHERE session_id = ?",
                (resume_id,),
            ).fetchone()
            if parent_row and parent_row["cwd"]:
                cwd = parent_row["cwd"]

        resume_raw_cmd = f"cd {shlex.quote(cwd)} && claude -r {resume_id}"

        # In Docker, we can't open a terminal — return the command for the user to copy
        if os.environ.get("DOCKER"):
            return {"status": "copy", "command": resume_raw_cmd, "message": "Running in Docker — copy this command to your terminal"}

        command_args, description = _get_resume_command(cwd, resume_id)

        if command_args is None:
            # No terminal available — return command for manual copy
            return {
                "status": "copy",
                "command": resume_raw_cmd,
                "message": f"Could not open a terminal ({description}). Copy and run this command manually.",
                "resumed_id": resume_id,
            }

        try:
            subprocess.run(
                command_args,
                capture_output=True, text=True, timeout=10,
            )
            msg = f"Opened {description} with session {resume_id}"
            if row["is_subagent"]:
                msg += f" (parent of subagent {session_id})"
            return {"status": "ok", "message": msg, "resumed_id": resume_id}
        except Exception as e:
            raise HTTPException(500, f"Failed to open terminal ({description}): {e}")
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT file_path, source FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        if row["source"] == "archive":
            return {"status": "already_archived"}

        src = Path(row["file_path"])
        if not src.exists():
            raise HTTPException(404, "Session file not found on disk")

        today = date.today().isoformat()
        parts = src.parts
        try:
            proj_idx = parts.index("projects")
            project_dirname = parts[proj_idx + 1]
        except (ValueError, IndexError):
            project_dirname = "unknown"

        dest_dir = ARCHIVE_BASE / today / project_dirname
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        shutil.copy2(str(src), str(dest))
        return {"status": "ok", "archived_to": str(dest)}
    finally:
        conn.close()


@app.post("/api/reindex")
async def trigger_reindex():
    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, reindex_incremental)
    return {"status": "ok", "changes": count}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT file_path FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        src = Path(row["file_path"])
        if not src.exists():
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
            conn.commit()
            return {"status": "ok", "message": "Removed from index (file already gone)"}

        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dest = TRASH_DIR / f"{session_id}_{src.name}"
        shutil.move(str(src), str(dest))

        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
        conn.commit()

        return {"status": "ok", "message": f"Moved to trash: {dest}"}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/star")
async def star_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        conn.execute("UPDATE sessions SET starred = 1 WHERE session_id = ?", (session_id,))
        conn.commit()
        session = _row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone())
        return _normalize_session(session)
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/unstar")
async def unstar_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        conn.execute("UPDATE sessions SET starred = 0 WHERE session_id = ?", (session_id,))
        conn.commit()
        session = _row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone())
        return _normalize_session(session)
    finally:
        conn.close()


@app.patch("/api/sessions/{session_id}/label")
async def set_label(session_id: str, request: Request):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        body = await request.json()
        label = body.get("label") or None  # treat empty string as None
        conn.execute("UPDATE sessions SET label = ? WHERE session_id = ?", (label, session_id))
        conn.commit()
        session = _row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone())
        return _normalize_session(session)
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")

        session = _row_to_dict(row)
        file_path = session["file_path"]
        label = session.get("label")
        title = label if label else (session.get("first_user_message") or session_id)

        lines = []
        lines.append(f"# Session: {title}")
        lines.append("")
        lines.append(f"- **Date**: {session.get('started_at', 'N/A')} — {session.get('ended_at', 'N/A')}")
        lines.append(f"- **Model**: {session.get('model', 'N/A')}")
        lines.append(f"- **Project**: {session.get('project_path', 'N/A')}")
        lines.append(f"- **Messages**: {session.get('message_count', 0)}")
        total_tokens = (session.get("total_input_tokens") or 0) + (session.get("total_output_tokens") or 0)
        lines.append(f"- **Tokens**: {total_tokens}")
        lines.append("")
        lines.append("---")

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    entry_type = entry.get("type")
                    if entry_type in ("file-history-snapshot", "last-prompt", "progress"):
                        continue
                    message = entry.get("message", {})
                    if not isinstance(message, dict):
                        continue
                    role = message.get("role")
                    if role not in ("user", "assistant"):
                        continue

                    content = message.get("content", "")
                    text = _extract_text_from_content(content)
                    tool_calls = _extract_tool_calls(content)
                    tool_result = _extract_tool_result(content)

                    lines.append("")
                    lines.append(f"## {role.capitalize()}")
                    if text:
                        lines.append(text)

                    for tc in tool_calls:
                        lines.append("")
                        lines.append(f"### Tool: {tc['name']}")
                        lines.append("```json")
                        lines.append(json.dumps(tc["input"], indent=2))
                        lines.append("```")

                    if tool_result is not None:
                        lines.append("")
                        lines.append("### Result")
                        lines.append("```")
                        lines.append(tool_result)
                        lines.append("```")

                    lines.append("")
                    lines.append("---")

        except (OSError, IOError) as e:
            logger.error("Failed to read session file %s: %s", file_path, e)
            raise HTTPException(500, f"Failed to read session file: {e}")

        md_content = "\n".join(lines)
        safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:60]
        filename = f"session-{safe_title}.md"

        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


@app.post("/api/sessions/bulk/archive")
async def bulk_archive(request: Request):
    body = await request.json()
    session_ids = body.get("session_ids", [])
    count = 0
    for sid in session_ids:
        try:
            result = await archive_session(sid)
            if result.get("status") in ("ok", "already_archived"):
                count += 1
        except HTTPException:
            pass
    return {"status": "ok", "count": count}


@app.post("/api/sessions/bulk/delete")
async def bulk_delete(request: Request):
    body = await request.json()
    session_ids = body.get("session_ids", [])
    count = 0
    for sid in session_ids:
        try:
            await delete_session(sid)
            count += 1
        except HTTPException:
            pass
    return {"status": "ok", "count": count}


@app.post("/api/sessions/bulk/star")
async def bulk_star(request: Request):
    body = await request.json()
    session_ids = body.get("session_ids", [])
    count = 0
    for sid in session_ids:
        try:
            await star_session(sid)
            count += 1
        except HTTPException:
            pass
    return {"status": "ok", "count": count}


@app.post("/api/sessions/bulk/unstar")
async def bulk_unstar(request: Request):
    body = await request.json()
    session_ids = body.get("session_ids", [])
    count = 0
    for sid in session_ids:
        try:
            await unstar_session(sid)
            count += 1
        except HTTPException:
            pass
    return {"status": "ok", "count": count}


@app.post("/api/sessions/cleanup")
async def cleanup_sessions(request: Request):
    body = await request.json()
    types = body.get("types", [])
    conn = _get_conn()
    try:
        deleted = 0
        session_ids_to_delete = set()

        if "empty" in types:
            rows = conn.execute("SELECT session_id FROM sessions WHERE is_empty = 1").fetchall()
            session_ids_to_delete.update(r["session_id"] for r in rows)

        if "tiny" in types:
            rows = conn.execute("SELECT session_id FROM sessions WHERE is_tiny = 1").fetchall()
            session_ids_to_delete.update(r["session_id"] for r in rows)

        if "stale" in types:
            rows = conn.execute("""
                SELECT session_id FROM sessions
                WHERE ended_at < datetime('now', '-30 days')
                  AND (starred = 0 OR starred IS NULL)
                  AND source != 'archive'
            """).fetchall()
            session_ids_to_delete.update(r["session_id"] for r in rows)

        for sid in session_ids_to_delete:
            try:
                await delete_session(sid)
                deleted += 1
            except HTTPException:
                pass

        return {"status": "ok", "deleted": deleted}
    finally:
        conn.close()


@app.get("/api/trash")
async def list_trash():
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in TRASH_DIR.iterdir():
        if not f.is_file():
            continue
        stat = f.stat()
        items.append({
            "filename": f.name,
            "size": stat.st_size,
            "date_moved": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        })
    items.sort(key=lambda x: x["date_moved"], reverse=True)
    return {"items": items, "total": len(items)}


@app.post("/api/trash/{filename}/restore")
async def restore_from_trash(filename: str):
    src = TRASH_DIR / filename
    if not src.exists():
        raise HTTPException(404, "File not found in trash")

    # Try to reconstruct the original path from the filename pattern:
    # <session_id>_<original_name>.jsonl
    # The original location is in ~/.claude/projects/<encoded_project>/
    # We can't perfectly reconstruct, so we look for the project dir
    # by checking if any project dir in ~/.claude/projects/ matches.
    projects_dir = Path.home() / ".claude" / "projects"
    # Fallback: put it back in a "restored" directory under projects
    dest_dir = projects_dir / "restored"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    shutil.move(str(src), str(dest))
    return {"status": "ok", "restored_to": str(dest)}


@app.delete("/api/trash/{filename}")
async def delete_from_trash(filename: str):
    target = TRASH_DIR / filename
    if not target.exists():
        raise HTTPException(404, "File not found in trash")
    target.unlink()
    return {"status": "ok"}


@app.post("/api/trash/empty")
async def empty_trash():
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in TRASH_DIR.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    return {"status": "ok", "deleted": count}


@app.get("/api/stats")
async def get_stats():
    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_sessions,
                COALESCE(SUM(total_input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(total_output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(message_count), 0) as total_messages,
                COALESCE(SUM(file_size_bytes), 0) as total_size_bytes,
                COUNT(DISTINCT project_path) as project_count
            FROM sessions
        """).fetchone()

        stats = _row_to_dict(row)
        active = get_active_sessions()
        stats["active_count"] = len(active)
        stats["active_session_ids"] = list(active)
        return stats
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Cost estimation rates per 1M tokens
# ---------------------------------------------------------------------------
_COST_RATES = {
    "opus":   {"input": 15.0,  "output": 75.0},
    "sonnet": {"input": 3.0,   "output": 15.0},
    "haiku":  {"input": 0.25,  "output": 1.25},
}


def _model_tier(model_name: str) -> str:
    """Determine pricing tier from a model name string."""
    if not model_name:
        return "sonnet"  # default fallback
    lower = model_name.lower()
    for tier in ("opus", "haiku", "sonnet"):
        if tier in lower:
            return tier
    return "sonnet"


@app.get("/api/analytics")
async def get_analytics(days: int = Query(default=30, ge=1, le=365)):
    conn = _get_conn()
    try:
        today = date.today()
        start_date = today - timedelta(days=days)
        period = {
            "start": start_date.isoformat(),
            "end": today.isoformat(),
            "days": days,
        }

        # --- Summary (all sessions, no time filter) ---
        sum_row = conn.execute("""
            SELECT
                COUNT(*) as total_sessions,
                COALESCE(SUM(message_count), 0) as total_messages,
                COALESCE(SUM(total_input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(total_output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(file_size_bytes), 0) as total_size_bytes,
                COALESCE(SUM(starred), 0) as starred_sessions,
                COUNT(DISTINCT project_path) as projects_count
            FROM sessions
        """).fetchone()
        s = dict(sum_row)
        total_sessions = s["total_sessions"] or 0
        s["total_tokens"] = s["total_input_tokens"] + s["total_output_tokens"]
        s["avg_messages_per_session"] = round(s["total_messages"] / total_sessions, 1) if total_sessions else 0
        s["avg_tokens_per_session"] = round(s["total_tokens"] / total_sessions) if total_sessions else 0
        active = get_active_sessions()
        s["active_sessions"] = len(active)
        summary = s

        # --- Daily activity (within period, every day filled) ---
        daily_rows = conn.execute("""
            SELECT
                date(started_at) as day,
                COUNT(*) as sessions,
                COALESCE(SUM(message_count), 0) as messages,
                COALESCE(SUM(total_input_tokens), 0) as input_tokens,
                COALESCE(SUM(total_output_tokens), 0) as output_tokens
            FROM sessions
            WHERE started_at >= date('now', ?)
            GROUP BY date(started_at)
            ORDER BY day DESC
        """, (f"-{days} days",)).fetchall()
        daily_map = {dict(r)["day"]: dict(r) for r in daily_rows}

        daily_activity = []
        for i in range(days + 1):
            d = (today - timedelta(days=i)).isoformat()
            if d in daily_map:
                entry = daily_map[d]
                entry["date"] = entry.pop("day")
                daily_activity.append(entry)
            else:
                daily_activity.append({
                    "date": d, "sessions": 0, "messages": 0,
                    "input_tokens": 0, "output_tokens": 0,
                })

        # --- Model distribution (within period) ---
        model_rows = conn.execute("""
            SELECT
                model,
                COUNT(*) as sessions,
                COALESCE(SUM(total_input_tokens), 0) + COALESCE(SUM(total_output_tokens), 0) as tokens
            FROM sessions
            WHERE started_at >= date('now', ?)
              AND model IS NOT NULL AND model != ''
            GROUP BY model
            ORDER BY tokens DESC
        """, (f"-{days} days",)).fetchall()
        total_model_tokens = sum(dict(r)["tokens"] for r in model_rows) or 1
        model_distribution = []
        for r in model_rows:
            d = dict(r)
            d["percentage"] = round(d["tokens"] / total_model_tokens * 100, 1)
            model_distribution.append(d)

        # --- Project breakdown (within period, top 10 by tokens) ---
        project_rows = conn.execute("""
            SELECT
                project_path,
                COUNT(*) as sessions,
                COALESCE(SUM(total_input_tokens), 0) + COALESCE(SUM(total_output_tokens), 0) as tokens,
                COALESCE(SUM(message_count), 0) as messages
            FROM sessions
            WHERE started_at >= date('now', ?)
            GROUP BY project_path
            ORDER BY tokens DESC
            LIMIT 10
        """, (f"-{days} days",)).fetchall()
        project_breakdown = []
        for r in project_rows:
            d = dict(r)
            d["project"] = _project_short_name(d["project_path"])
            project_breakdown.append(d)

        # --- Session length distribution (within period) ---
        len_rows = conn.execute("""
            SELECT
                SUM(CASE WHEN message_count BETWEEN 1 AND 5 THEN 1 ELSE 0 END) as tiny,
                SUM(CASE WHEN message_count BETWEEN 6 AND 50 THEN 1 ELSE 0 END) as short,
                SUM(CASE WHEN message_count BETWEEN 51 AND 200 THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN message_count > 200 THEN 1 ELSE 0 END) as long
            FROM sessions
            WHERE started_at >= date('now', ?)
        """, (f"-{days} days",)).fetchone()
        ld = dict(len_rows)
        session_length_distribution = {
            "tiny":   {"label": "1-5 msgs",   "count": ld["tiny"] or 0},
            "short":  {"label": "6-50 msgs",  "count": ld["short"] or 0},
            "medium": {"label": "51-200 msgs", "count": ld["medium"] or 0},
            "long":   {"label": "200+ msgs",  "count": ld["long"] or 0},
        }

        # --- Hourly activity (within period) ---
        hour_rows = conn.execute("""
            SELECT
                CAST(strftime('%H', started_at) AS INTEGER) as hour,
                COUNT(*) as sessions
            FROM sessions
            WHERE started_at >= date('now', ?)
              AND started_at IS NOT NULL
            GROUP BY hour
            ORDER BY hour
        """, (f"-{days} days",)).fetchall()
        hour_map = {dict(r)["hour"]: dict(r)["sessions"] for r in hour_rows}
        hourly_activity = [{"hour": h, "sessions": hour_map.get(h, 0)} for h in range(24)]

        # --- Cost estimate (within period) ---
        cost_rows = conn.execute("""
            SELECT
                model,
                COALESCE(SUM(total_input_tokens), 0) as input_tokens,
                COALESCE(SUM(total_output_tokens), 0) as output_tokens
            FROM sessions
            WHERE started_at >= date('now', ?)
              AND model IS NOT NULL AND model != ''
            GROUP BY model
            ORDER BY output_tokens DESC
        """, (f"-{days} days",)).fetchall()
        by_model = []
        total_cost = 0.0
        for r in cost_rows:
            d = dict(r)
            tier = _model_tier(d["model"])
            rates = _COST_RATES[tier]
            cost = (d["input_tokens"] / 1_000_000 * rates["input"]
                    + d["output_tokens"] / 1_000_000 * rates["output"])
            cost = round(cost, 2)
            total_cost += cost
            by_model.append({
                "model": d["model"],
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "cost_usd": cost,
            })
        cost_estimate = {
            "total_usd": round(total_cost, 2),
            "by_model": by_model,
            "note": "Estimated based on published API pricing. Actual costs may differ.",
        }

        return {
            "period": period,
            "summary": summary,
            "daily_activity": daily_activity,
            "model_distribution": model_distribution,
            "project_breakdown": project_breakdown,
            "session_length_distribution": session_length_distribution,
            "hourly_activity": hourly_activity,
            "cost_estimate": cost_estimate,
        }
    finally:
        conn.close()


@app.get("/api/events")
async def sse_events(request: Request):
    """Server-Sent Events endpoint for live updates."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.append(queue)

    async def event_stream():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if isinstance(msg, dict):
                        event_name = msg.get("event", "message")
                        data = msg.get("data", "{}")
                        yield f"event: {event_name}\ndata: {data}\n\n"
                    else:
                        yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in _sse_subscribers:
                _sse_subscribers.remove(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
