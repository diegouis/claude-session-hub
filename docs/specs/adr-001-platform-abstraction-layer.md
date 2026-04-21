# ADR-001: Platform Abstraction Layer

Status: Proposed
Date: 2026-04-21
Supersedes: —

## Context

The codebase currently has platform branches inline in three places:

- `detector.py:24–107` — shells out to `pgrep`/`ps`/`lsof` with no branch for Windows.
- `server.py:257–287` — a single `_get_resume_command` function with `if system == "Darwin" / Linux / Windows` arms, only the Darwin arm exercised.
- `server.py:977` — duplicates `Path.home() / ".claude"` instead of reusing the constant from `indexer.py:14` / `detector.py:13`.

Adding Linux and Windows as first-class targets will multiply these branches. Keeping the logic inline risks inconsistency: e.g. `server.py` will know about Windows Terminal but `detector.py` won't.

## Decision

Introduce a `platform_compat` package (module name chosen to avoid shadowing stdlib `platform`) with four public functions:

```
claude_data_dir() -> Path
find_claude_processes() -> list[ProcessInfo]
open_terminal_with_command(cwd: str, command: str) -> tuple[bool, str]
quote_for_shell(arg: str) -> str
```

Implementation files:
- `platform_compat/__init__.py` — dispatches on `sys.platform` at import time.
- `platform_compat/_macos.py`, `_linux.py`, `_windows.py` — one per platform.
- `platform_compat/_common.py` — shared types (`ProcessInfo` dataclass).

Callers (`detector.py`, `server.py`) import from `platform_compat` and never inspect `platform.system()` themselves.

## Alternatives Considered

**A. Inline branches.** Keep `if platform.system() == "..."` in each call site. Rejected: already hard to test, duplicates dispatch logic, and the two existing sites diverge (detector has no Windows branch; server does). Three platforms × N call sites scales poorly.

**B. Third-party abstraction lib** (e.g. `plumbum`, `sh`, `appdirs`). Rejected: `appdirs` helps only with data-dir resolution and Claude Code's layout doesn't follow XDG anyway. `plumbum`/`sh` are POSIX-centric. None solve the terminal-launcher problem, which is the hardest part.

**C. Split into per-platform packages** (`claude-session-hub-macos`, `-linux`, `-windows`). Rejected: massive overkill for a local single-user dashboard. Pip install UX suffers. Monorepo with runtime dispatch is strictly simpler.

## Consequences

Positive:
- Single place to look when a platform bug appears.
- Per-platform unit tests are trivial — mock `sys.platform` at `platform_compat` import, assert the dispatched impl is the right one.
- `detector.py` and `server.py` lose ~80 lines of shell-out / branching code.
- Enables the Phase 3 "user-configurable preferred terminal" feature cleanly (one env var read in one place).

Negative:
- One more module to navigate.
- Import-time dispatch means changing `$HUB_FORCE_COPY` at runtime requires a re-import — acceptable, tests use monkeypatch.

Neutral:
- `psutil` becomes a hard dep (see ADR-002). Module-level import failure would crash the server; we'll fail loudly in `run.py` with a helpful message.

## Implementation Notes

- `ProcessInfo` fields: `pid: int`, `cwd: str | None`, `cmdline: list[str]`, `open_files: list[str]`, `is_telegram: bool`. Matches what `detector.py` currently builds.
- `open_terminal_with_command` returns `(launched, description)`; `False` triggers the existing copy-command UX in `server.py:606–613`. No new frontend work needed.
- `quote_for_shell` centralises the `shlex.quote` vs `subprocess.list2cmdline` choice so `server.py:262` and `:598` stop assuming POSIX.
