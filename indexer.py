"""Session indexer — walks ~/.claude/ and builds a SQLite index of all sessions."""

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
PROJECTS_DIR = CLAUDE_DIR / "projects"
ARCHIVE_DIR = CLAUDE_DIR / "archive"
DB_PATH = Path(__file__).parent / "data" / "sessions.db"

# ---------------------------------------------------------------------------
# Path decoding: encoded dir name -> real filesystem path
# ---------------------------------------------------------------------------

def decode_project_path(encoded: str) -> str:
    """Decode a URL-encoded directory name back to a readable path.

    Pattern: leading `-` becomes `/`, every `-` becomes `/`,
    but `--` becomes a literal `-`.
    """
    if not encoded:
        return encoded
    # First, replace `--` with a placeholder
    result = encoded.replace("--", "\x00")
    # Replace remaining `-` with `/`
    result = result.replace("-", "/")
    # Restore literal dashes
    result = result.replace("\x00", "-")
    return result


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    project_path      TEXT,
    cwd               TEXT,
    first_user_message TEXT,
    last_message_preview TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    message_count     INTEGER DEFAULT 0,
    user_message_count INTEGER DEFAULT 0,
    assistant_message_count INTEGER DEFAULT 0,
    model             TEXT,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    file_size_bytes   INTEGER DEFAULT 0,
    file_mtime        REAL DEFAULT 0,
    file_path         TEXT,
    is_subagent       INTEGER DEFAULT 0,
    parent_session_id TEXT,
    source            TEXT DEFAULT 'live',
    git_branch        TEXT,
    version           TEXT,
    is_empty          INTEGER DEFAULT 0,
    is_tiny           INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
    session_id UNINDEXED,
    content,
    tokenize='porter unicode61'
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_ended ON sessions(ended_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
"""


def migrate_db(conn: sqlite3.Connection):
    """Add columns that may not exist in older databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "starred" not in existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN starred INTEGER DEFAULT 0")
    if "label" not in existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN label TEXT")
    if "capabilities" not in existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN capabilities TEXT")
    if "cache_read_tokens" not in existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
    if "cache_create_tokens" not in existing:
        conn.execute("ALTER TABLE sessions ADD COLUMN cache_create_tokens INTEGER DEFAULT 0")
    conn.commit()


def get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and optionally create) the sessions database."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    migrate_db(conn)
    return conn


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
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
        return " ".join(parts)
    return ""


_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_ANY_XML_TAG_RE = re.compile(r"<[a-z-]+>.*?</[a-z-]+>", re.DOTALL)


def _clean_first_message(text: str) -> str:
    """Clean XML-wrapped slash command messages into a readable form.

    "<command-name>/prime</command-name><command-args>foo bar</command-args>"
    → "/prime foo bar"
    """
    if not text:
        return text
    if "<command-name>" not in text:
        return text
    name_match = _CMD_NAME_RE.search(text)
    if not name_match:
        return text
    name = name_match.group(1).strip()
    args_match = _CMD_ARGS_RE.search(text)
    args = args_match.group(1).strip() if args_match else ""
    # Strip any remaining XML-like command blocks
    remainder = _ANY_XML_TAG_RE.sub("", text).strip()
    cleaned = f"{name} {args}".strip()
    if remainder:
        cleaned = f"{cleaned} — {remainder[:100]}"
    return cleaned


def parse_jsonl(file_path: Path) -> dict:
    """Parse a single JSONL session file and return extracted metadata."""
    meta = {
        "session_id": None,
        "cwd": None,
        "first_user_message": None,
        "last_message_preview": None,
        "started_at": None,
        "ended_at": None,
        "message_count": 0,
        "user_message_count": 0,
        "assistant_message_count": 0,
        "model": None,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
        "git_branch": None,
        "version": None,
        "fts_texts": [],
        "capabilities": None,
    }

    # Capability tracking
    tools_count = {}
    skills_count = {}
    agents_count = {}
    mcp_count = {}
    slash_count = {}
    tool_uuids = {}  # {tool_name: [uuid, ...]} — which messages used each tool

    line_count = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            for line_num, raw_line in enumerate(fh, 1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                line_count += 1
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    logger.warning("Bad JSON at %s:%d", file_path, line_num)
                    continue

                entry_type = entry.get("type")
                timestamp = entry.get("timestamp")

                # Skip internal types for content extraction
                if entry_type in ("file-history-snapshot",):
                    continue

                # Track timestamps
                if timestamp:
                    if meta["started_at"] is None:
                        meta["started_at"] = timestamp
                    meta["ended_at"] = timestamp

                # Extract session-level fields from any entry that has them
                if not meta["session_id"] and entry.get("sessionId"):
                    meta["session_id"] = entry["sessionId"]
                if not meta["cwd"] and entry.get("cwd"):
                    meta["cwd"] = entry["cwd"]
                if not meta["git_branch"] and entry.get("gitBranch"):
                    meta["git_branch"] = entry["gitBranch"]
                if not meta["version"] and entry.get("version"):
                    meta["version"] = entry["version"]

                # Process user/assistant messages
                message = entry.get("message", {})
                role = message.get("role") if isinstance(message, dict) else None

                if entry_type == "user" or role == "user":
                    meta["user_message_count"] += 1
                    meta["message_count"] += 1
                    text = _extract_text(message.get("content", ""))
                    if text:
                        display_text = _clean_first_message(text)
                        if meta["first_user_message"] is None:
                            meta["first_user_message"] = display_text[:200]
                        meta["last_message_preview"] = display_text[:200]
                        meta["fts_texts"].append(text)
                        # Detect slash commands — check cleaned display_text so
                        # command-wrapped forms are captured too
                        stripped = display_text.lstrip()
                        if stripped.startswith("/") and len(stripped) > 1:
                            first_token = stripped.split(None, 1)[0]
                            if len(first_token) < 40 and not first_token.startswith("//"):
                                cmd = first_token.lstrip("/")
                                if cmd and all(c.isalnum() or c in "-_:" for c in cmd):
                                    slash_count[cmd] = slash_count.get(cmd, 0) + 1

                elif entry_type == "assistant" or role == "assistant":
                    meta["assistant_message_count"] += 1
                    meta["message_count"] += 1
                    text = _extract_text(message.get("content", ""))
                    if text:
                        meta["last_message_preview"] = text[:200]
                        meta["fts_texts"].append(text)

                    # Model and tokens
                    if isinstance(message, dict):
                        if not meta["model"] and message.get("model"):
                            meta["model"] = message["model"]
                        usage = message.get("usage", {})
                        if isinstance(usage, dict):
                            meta["total_input_tokens"] += usage.get("input_tokens", 0) or 0
                            meta["total_output_tokens"] += usage.get("output_tokens", 0) or 0
                            meta["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
                            meta["cache_create_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0

                    # Extract tool_use blocks → capabilities
                    entry_uuid = entry.get("uuid", "")
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict) or block.get("type") != "tool_use":
                                continue
                            name = block.get("name", "")
                            if not name:
                                continue
                            tools_count[name] = tools_count.get(name, 0) + 1
                            if entry_uuid:
                                tool_uuids.setdefault(name, []).append(entry_uuid)
                            # MCP tool: mcp__<server>__<tool>
                            if name.startswith("mcp__"):
                                parts = name.split("__")
                                if len(parts) >= 2:
                                    server = parts[1]
                                    mcp_count[server] = mcp_count.get(server, 0) + 1
                            # Skill invocation
                            elif name == "Skill":
                                skill = ""
                                inp = block.get("input", {})
                                if isinstance(inp, dict):
                                    skill = inp.get("skill", "")
                                if skill:
                                    skills_count[skill] = skills_count.get(skill, 0) + 1
                            # Agent invocation
                            elif name == "Agent":
                                inp = block.get("input", {})
                                sub = "general-purpose"
                                if isinstance(inp, dict):
                                    sub = inp.get("subagent_type") or "general-purpose"
                                agents_count[sub] = agents_count.get(sub, 0) + 1

    except Exception as e:
        logger.error("Failed to parse %s: %s", file_path, e)

    # Derive plugins from MCP server names and agent prefixes
    plugins = set()
    for server in mcp_count:
        # e.g. "plugin_telegram_telegram" → "telegram"
        # e.g. "claude_ai_Gmail" → "Gmail"
        base = server
        if base.startswith("plugin_"):
            base = base[len("plugin_"):]
        parts = base.split("_")
        if parts:
            plugins.add(parts[-1] if len(parts) > 1 else parts[0])
    for agent_name in agents_count:
        if ":" in agent_name:
            plugins.add(agent_name.split(":", 1)[0])

    # Only store capabilities if we found anything
    has_caps = bool(tools_count or skills_count or agents_count or
                    mcp_count or slash_count or plugins)
    if has_caps:
        meta["capabilities"] = {
            "tools": tools_count,
            "skills": skills_count,
            "agents": agents_count,
            "mcp_servers": mcp_count,
            "slash_commands": slash_count,
            "plugins": sorted(plugins),
            "tool_uuids": tool_uuids,
        }

    meta["_line_count"] = line_count
    return meta


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_session_files() -> list[dict]:
    """Find all JSONL session files and return metadata about each."""
    files = []

    # Live sessions in ~/.claude/projects/
    if PROJECTS_DIR.is_dir():
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            project_path = decode_project_path(project_dir.name)

            # Top-level session files
            for jsonl in project_dir.glob("*.jsonl"):
                files.append({
                    "file_path": jsonl,
                    "project_path": project_path,
                    "source": "live",
                    "is_subagent": False,
                    "parent_session_id": None,
                })

            # Subagent sessions: <session-uuid>/subagents/<agent>.jsonl
            for session_dir in project_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                subagents_dir = session_dir / "subagents"
                if subagents_dir.is_dir():
                    parent_sid = session_dir.name
                    for jsonl in subagents_dir.glob("*.jsonl"):
                        files.append({
                            "file_path": jsonl,
                            "project_path": project_path,
                            "source": "live",
                            "is_subagent": True,
                            "parent_session_id": parent_sid,
                        })

    # Archived sessions
    if ARCHIVE_DIR.is_dir():
        for date_dir in ARCHIVE_DIR.iterdir():
            if not date_dir.is_dir():
                continue
            for project_dir in date_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                project_path = decode_project_path(project_dir.name)

                for jsonl in project_dir.glob("*.jsonl"):
                    files.append({
                        "file_path": jsonl,
                        "project_path": project_path,
                        "source": "archive",
                        "is_subagent": False,
                        "parent_session_id": None,
                    })

                # Subagents in archive
                for session_dir in project_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    subagents_dir = session_dir / "subagents"
                    if subagents_dir.is_dir():
                        parent_sid = session_dir.name
                        for jsonl in subagents_dir.glob("*.jsonl"):
                            files.append({
                                "file_path": jsonl,
                                "project_path": project_path,
                                "source": "archive",
                                "is_subagent": True,
                                "parent_session_id": parent_sid,
                            })

    return files


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _session_id_from_file(file_info: dict, meta: dict) -> str:
    """Derive session_id from parsed metadata or filename.

    For subagents, we create a composite key to avoid collisions since
    multiple subagent files share the parent's sessionId.
    """
    base_id = meta.get("session_id") or file_info["file_path"].stem
    if file_info["is_subagent"]:
        agent_name = file_info["file_path"].stem  # e.g. "agent-aa2ed987f3e32c0cb"
        return f"{base_id}:{agent_name}"
    return base_id


def index_file(conn: sqlite3.Connection, file_info: dict) -> bool:
    """Index a single JSONL file. Returns True if indexed."""
    fpath: Path = file_info["file_path"]
    try:
        stat = fpath.stat()
    except OSError:
        return False

    file_size = stat.st_size
    file_mtime = stat.st_mtime

    meta = parse_jsonl(fpath)
    session_id = _session_id_from_file(file_info, meta)
    line_count = meta.pop("_line_count", 0)
    fts_texts = meta.pop("fts_texts", [])

    is_empty = 1 if line_count == 0 else 0
    is_tiny = 1 if 0 < line_count <= 5 else 0

    capabilities = meta.pop("capabilities", None)
    caps_json = json.dumps(capabilities) if capabilities else None

    # Check if session already exists to preserve user-set fields (starred, label)
    existing = conn.execute(
        "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()

    if existing:
        # UPDATE only machine-derived fields, preserve starred and label
        conn.execute("""
            UPDATE sessions SET
                project_path = ?, cwd = ?, first_user_message = ?,
                last_message_preview = ?, started_at = ?, ended_at = ?,
                message_count = ?, user_message_count = ?, assistant_message_count = ?,
                model = ?, total_input_tokens = ?, total_output_tokens = ?,
                cache_read_tokens = ?, cache_create_tokens = ?,
                file_size_bytes = ?, file_mtime = ?, file_path = ?,
                is_subagent = ?, parent_session_id = ?, source = ?,
                git_branch = ?, version = ?, is_empty = ?, is_tiny = ?,
                capabilities = ?
            WHERE session_id = ?
        """, (
            meta["cwd"] or file_info["project_path"], meta["cwd"],
            meta["first_user_message"], meta["last_message_preview"],
            meta["started_at"], meta["ended_at"],
            meta["message_count"], meta["user_message_count"],
            meta["assistant_message_count"],
            meta["model"], meta["total_input_tokens"], meta["total_output_tokens"],
            meta["cache_read_tokens"], meta["cache_create_tokens"],
            file_size, file_mtime, str(fpath),
            1 if file_info["is_subagent"] else 0,
            file_info["parent_session_id"], file_info["source"],
            meta["git_branch"], meta["version"], is_empty, is_tiny,
            caps_json,
            session_id,
        ))
    else:
        # INSERT new session with defaults for starred/label
        conn.execute("""
            INSERT INTO sessions (
                session_id, project_path, cwd, first_user_message,
                last_message_preview, started_at, ended_at,
                message_count, user_message_count, assistant_message_count,
                model, total_input_tokens, total_output_tokens,
                cache_read_tokens, cache_create_tokens,
                file_size_bytes, file_mtime, file_path,
                is_subagent, parent_session_id, source,
                git_branch, version, is_empty, is_tiny,
                capabilities, starred, label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
        """, (
            session_id, meta["cwd"] or file_info["project_path"], meta["cwd"],
            meta["first_user_message"], meta["last_message_preview"],
            meta["started_at"], meta["ended_at"],
            meta["message_count"], meta["user_message_count"],
            meta["assistant_message_count"],
            meta["model"], meta["total_input_tokens"], meta["total_output_tokens"],
            meta["cache_read_tokens"], meta["cache_create_tokens"],
            file_size, file_mtime, str(fpath),
            1 if file_info["is_subagent"] else 0,
            file_info["parent_session_id"], file_info["source"],
            meta["git_branch"], meta["version"], is_empty, is_tiny,
            caps_json,
        ))

    # Update FTS
    conn.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
    if fts_texts:
        # Combine all text, limit to ~100KB for FTS
        combined = "\n".join(fts_texts)[:100_000]
        conn.execute(
            "INSERT INTO session_fts (session_id, content) VALUES (?, ?)",
            (session_id, combined),
        )

    return True


def reindex_all(db_path: Optional[Path] = None) -> int:
    """Drop and rebuild the entire index. Returns count of indexed sessions."""
    start = time.time()
    conn = get_db(db_path)
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM session_fts")
    conn.commit()

    files = discover_session_files()
    count = 0
    for fi in files:
        try:
            if index_file(conn, fi):
                count += 1
        except Exception as e:
            logger.error("Error indexing %s: %s", fi["file_path"], e)

        # Commit in batches
        if count % 50 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    elapsed = time.time() - start
    logger.info("Full reindex complete: %d sessions in %.1fs", count, elapsed)
    return count


def reindex_incremental(db_path: Optional[Path] = None) -> int:
    """Only re-index files whose mtime has changed. Returns count of updated sessions."""
    start = time.time()
    conn = get_db(db_path)

    # Build lookup of existing mtimes
    existing = {}
    for row in conn.execute("SELECT session_id, file_mtime, file_path FROM sessions"):
        existing[row["file_path"]] = (row["session_id"], row["file_mtime"])

    files = discover_session_files()
    existing_paths = set()
    count = 0

    for fi in files:
        fpath = str(fi["file_path"])
        existing_paths.add(fpath)

        try:
            current_mtime = fi["file_path"].stat().st_mtime
        except OSError:
            continue

        prev = existing.get(fpath)
        if prev and abs(prev[1] - current_mtime) < 0.001:
            continue  # unchanged

        try:
            if index_file(conn, fi):
                count += 1
        except Exception as e:
            logger.error("Error indexing %s: %s", fi["file_path"], e)

        if count % 50 == 0:
            conn.commit()

    # Remove sessions whose files no longer exist
    for fpath, (sid, _) in existing.items():
        if fpath not in existing_paths:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM session_fts WHERE session_id = ?", (sid,))
            count += 1

    conn.commit()
    conn.close()
    elapsed = time.time() - start
    logger.info("Incremental reindex: %d changes in %.1fs", count, elapsed)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    if mode == "full":
        n = reindex_all()
    else:
        n = reindex_incremental()
    print(f"Indexed {n} sessions")
