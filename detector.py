"""Active session detector — finds running Claude processes and session status."""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
ACTIVE_SESSIONS_LOG = CLAUDE_DIR / "active-sessions.log"
STALE_DAYS = 30
# Sessions modified within this window AND matching a running process's cwd are "active"
ACTIVE_MTIME_WINDOW = 300  # 5 minutes


def _get_claude_processes() -> list[dict]:
    """Get info about running claude processes: pid, cwd, args, session_id (if --resume)."""
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
        info = {"pid": pid, "cwd": None, "args": "", "session_id": None, "is_telegram": False}

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
        # Also check -r flag
        r_match = re.search(r'\s-r\s+(\S+)', info["args"])
        if r_match and not info["session_id"]:
            info["session_id"] = r_match.group(1)

        # Get cwd via lsof
        try:
            lsof_result = subprocess.run(
                ["lsof", "-p", str(pid), "-Fd", "-Fn"],
                capture_output=True, text=True, timeout=5,
            )
            lines = lsof_result.stdout.splitlines()
            for i, line in enumerate(lines):
                if line == "fcwd" and i + 1 < len(lines) and lines[i + 1].startswith("n"):
                    info["cwd"] = lines[i + 1][1:]
                    break
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

    Strategy:
    1. For processes with --resume/-r flags: direct session_id match
    2. For bare `claude` processes: match cwd against recently-modified session files
    3. Exclude telegram bot processes from session matching
    """
    processes = _get_claude_processes()
    recent_sessions = _find_recently_modified_sessions()
    active = set()

    # Build a set of cwds that have active non-telegram claude processes
    active_cwds = set()
    for proc in processes:
        if proc["is_telegram"]:
            continue

        # Direct match via --resume flag
        if proc["session_id"]:
            active.add(proc["session_id"])
            continue

        if proc["cwd"]:
            active_cwds.add(proc["cwd"])

    # Match recently-modified sessions to active cwds (using real cwds from DB)
    for session_id, cwd in recent_sessions.items():
        if cwd in active_cwds:
            active.add(session_id)

    return active


def get_session_status(
    session_id: str,
    file_mtime: Optional[float] = None,
    is_tiny: bool = False,
    active_sessions: Optional[set[str]] = None,
) -> str:
    """Determine session status.

    Returns:
        "active" — has a running process
        "idle"   — no process, mtime within 30 days
        "stale"  — no process, mtime older than 30 days or file is tiny
    """
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


def get_all_session_statuses(sessions: list[dict]) -> dict[str, str]:
    """Bulk-compute statuses for a list of session dicts."""
    active = get_active_sessions()
    result = {}
    for s in sessions:
        result[s["session_id"]] = get_session_status(
            s["session_id"],
            file_mtime=s.get("file_mtime"),
            is_tiny=s.get("is_tiny", False),
            active_sessions=active,
        )
    return result
