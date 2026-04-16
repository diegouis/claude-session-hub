"""Active session detector — finds running Claude processes and session status."""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
PROJECTS_DIR = CLAUDE_DIR / "projects"
ACTIVE_SESSIONS_LOG = CLAUDE_DIR / "active-sessions.log"
STALE_DAYS = 30
# Sessions modified within this window AND matching a running process's cwd are "active"
ACTIVE_MTIME_WINDOW = 300  # 5 minutes


_TASK_DIR_RE = re.compile(r"/\.claude/tasks/([a-f0-9-]{36})")


def _get_claude_processes() -> list[dict]:
    """Get info about running claude processes.

    Returns a list of dicts with:
      pid, cwd, args, session_id (from --resume flag),
      task_session_ids (session IDs found in /tasks/ dirs held open by lsof),
      is_telegram
    """
    processes = []
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return processes

    for pid in pids:
        info = {
            "pid": pid, "cwd": None, "args": "",
            "session_id": None, "task_session_ids": set(),
            "is_telegram": False,
        }

        # Get command args
        try:
            args_result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True, text=True, timeout=3,
            )
            info["args"] = args_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Check if this is a telegram bot
        if "--channels" in info["args"] and "telegram" in info["args"]:
            info["is_telegram"] = True

        # Extract session_id from --resume flag
        resume_match = re.search(r'--resume\s+(\S+)', info["args"])
        if resume_match:
            info["session_id"] = resume_match.group(1)
        r_match = re.search(r'\s-r\s+(\S+)', info["args"])
        if r_match and not info["session_id"]:
            info["session_id"] = r_match.group(1)

        # lsof: cwd + any /tasks/<uuid>/ dirs held open (running tool strong signal)
        try:
            lsof_result = subprocess.run(
                ["lsof", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            # Get cwd
            for line in lsof_result.stdout.splitlines():
                if " cwd " in line or "\tcwd\t" in line:
                    # lsof cwd line format: COMMAND PID USER fcwd DIR ... /path
                    parts = line.split(None, 8)
                    if len(parts) >= 9:
                        info["cwd"] = parts[8]
                        break
                    # Fallback: parse with -Fn
            if not info["cwd"]:
                try:
                    f_result = subprocess.run(
                        ["lsof", "-p", str(pid), "-Fd", "-Fn"],
                        capture_output=True, text=True, timeout=5,
                    )
                    lines = f_result.stdout.splitlines()
                    for i, line in enumerate(lines):
                        if line == "fcwd" and i + 1 < len(lines) and lines[i + 1].startswith("n"):
                            info["cwd"] = lines[i + 1][1:]
                            break
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            # Find all /tasks/<session-id>/ dirs in open files
            for match in _TASK_DIR_RE.findall(lsof_result.stdout):
                info["task_session_ids"].add(match)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        processes.append(info)

    return processes


def _find_recently_modified_sessions(db_path: Optional[Path] = None) -> dict[str, str]:
    """Find sessions modified within the active window and return their cwds.

    Uses the SQLite index to get the real cwd (from JSONL data) rather than
    decoding the URL-encoded directory name, which fails for paths with
    spaces, parentheses, and special characters.

    Returns {session_id: cwd}
    """
    recent = {}
    now = time.time()

    if not PROJECTS_DIR.is_dir():
        return recent

    # Collect recently-modified session IDs
    recent_ids = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
                if now - mtime <= ACTIVE_MTIME_WINDOW:
                    recent_ids.append(jsonl.stem)
            except OSError:
                continue

    if not recent_ids:
        return recent

    # Look up cwds from the database
    try:
        from indexer import get_db
        conn = get_db(db_path)
        placeholders = ",".join("?" * len(recent_ids))
        rows = conn.execute(
            f"SELECT session_id, cwd FROM sessions WHERE session_id IN ({placeholders})",
            recent_ids,
        ).fetchall()
        conn.close()
        for row in rows:
            if row["cwd"]:
                recent[row["session_id"]] = row["cwd"]
    except Exception as e:
        logger.warning("Failed to query DB for cwds: %s", e)

    return recent


def get_active_sessions() -> set[str]:
    """Return session_ids that currently have a live Claude process.

    Kept for backwards compatibility. Uses `get_active_session_map` under
    the hood and returns the set of all active (running OR open) sessions.
    """
    return set(get_active_session_map().keys())


def get_active_session_map() -> dict[str, str]:
    """Return {session_id: confidence} where confidence is:

      "running"  — process currently executing a tool for this session
                   (we see /tasks/<session-id>/ held open via lsof, OR
                    --resume <id> in process args)
      "open"     — terminal/process is alive, session file was modified
                   recently, and the process cwd matches the session cwd
                   (likely "you have the terminal open but claude is idle")

    Both are "active" from the user's perspective; the distinction gives
    us tooltip detail. When claude exits (/exit or terminal close), the
    process dies and the session drops out of this map on next poll.
    """
    processes = _get_claude_processes()
    recent_sessions = _find_recently_modified_sessions()
    active = {}  # session_id -> confidence

    active_cwds = set()
    running_task_ids = set()
    resumed_ids = set()

    for proc in processes:
        if proc["is_telegram"]:
            continue
        # Strong signal: /tasks/<session-id>/ held open
        for sid in proc.get("task_session_ids", ()):  # set
            running_task_ids.add(sid)
        # Strong signal: --resume <id> in command args
        if proc["session_id"]:
            resumed_ids.add(proc["session_id"])
        # Weak signal: cwd of bare `claude` processes
        if not proc["session_id"] and proc["cwd"]:
            active_cwds.add(proc["cwd"])

    for sid in running_task_ids:
        active[sid] = "running"
    for sid in resumed_ids:
        active.setdefault(sid, "running")

    # Recently-modified sessions whose cwd matches a live non-telegram process
    for session_id, cwd in recent_sessions.items():
        if cwd in active_cwds or session_id in resumed_ids:
            # Don't downgrade a "running" session to "open"
            active.setdefault(session_id, "open")

    return active


def get_session_status(
    session_id: str,
    file_mtime: Optional[float] = None,
    is_tiny: bool = False,
    active_sessions: Optional[set[str]] = None,
) -> str:
    """Return broad status bucket (backwards-compatible)."""
    if active_sessions is None:
        active_sessions = get_active_sessions()

    if session_id in active_sessions:
        return "active"

    if is_tiny:
        return "stale"

    if file_mtime is not None:
        age_days = (time.time() - file_mtime) / 86400
        if age_days > STALE_DAYS:
            return "stale"

    return "idle"


def get_all_session_statuses(sessions: list[dict]) -> dict[str, dict]:
    """Bulk-compute statuses for a list of session dicts.

    Returns {session_id: {"status": "active"|"idle"|"stale",
                          "confidence": "running"|"open"|None,
                          "reason": human-readable str}}
    """
    active_map = get_active_session_map()
    active_set = set(active_map.keys())
    now = time.time()
    result = {}
    for s in sessions:
        sid = s["session_id"]
        file_mtime = s.get("file_mtime")
        is_tiny = s.get("is_tiny", False)
        if sid in active_set:
            confidence = active_map[sid]
            if confidence == "running":
                reason = "A Claude process is currently working on this session (tool active or resumed with -r)."
            else:
                reason = "Claude is running in this session's working directory and the session file was modified recently."
            result[sid] = {
                "status": "active",
                "confidence": confidence,
                "reason": reason,
            }
            continue
        if is_tiny:
            result[sid] = {
                "status": "stale", "confidence": None,
                "reason": "Session has 5 or fewer messages — likely abandoned.",
            }
            continue
        if file_mtime is not None and (now - file_mtime) / 86400 > STALE_DAYS:
            result[sid] = {
                "status": "stale", "confidence": None,
                "reason": f"Session hasn't been touched in over {STALE_DAYS} days.",
            }
            continue
        result[sid] = {
            "status": "idle", "confidence": None,
            "reason": "No live Claude process found for this session. Resumable via `claude -r`.",
        }
    return result
