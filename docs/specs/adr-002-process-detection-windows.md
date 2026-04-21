# ADR-002: Process Detection via psutil

Status: Proposed
Date: 2026-04-21

## Context

`detector.py` currently detects running Claude sessions by shelling out:

- `pgrep -x claude` (line 34) — list PIDs
- `ps -p <pid> -o args=` (line 51) — get cmdline
- `lsof -p <pid>` (lines 73, 88) — get cwd and open `/tasks/<uuid>/` dirs

None of these exist on Windows. `pgrep` isn't installed on some minimal Linux containers (Alpine, distroless). `lsof` is often missing too. The subprocess overhead is also measurable: three spawns per PID × N PIDs on every poll.

We need a single API that works on all three target OSes and doesn't require the user to install extra system packages.

## Decision

Adopt `psutil` (≥5.9) as a hard runtime dependency. Rewrite `detector.py:_get_claude_processes` to use `psutil.process_iter(['pid', 'name', 'cmdline', 'cwd', 'open_files'])`. Remove all `subprocess.run` calls from `detector.py`.

Add to `requirements.txt`:
```
psutil>=5.9
```

`psutil` ships manylinux/macOS/Windows wheels for every CPython ≥3.10, so `pip install` remains a single command with no compiler requirement.

## Alternatives Considered

**A. WMI on Windows, `/proc` on Linux, `sysctl`/`libproc` on macOS.** All reachable via `ctypes` with zero dependencies. Rejected: three separate implementations, hundreds of lines of platform code, error-prone (`/proc/<pid>/cwd` requires permission dance, `open_files` enumeration is non-trivial on Windows). `psutil` already does this correctly — reinventing is not justified.

**B. Leave Windows without active detection.** Ship Windows with the badge permanently grey. Rejected: active detection is a headline feature in the README, and the infrastructure (`task_session_ids`, resume-via-open-file) is the main differentiator vs a plain directory listing.

**C. Keep `pgrep`/`lsof` on POSIX, add a `_windows.py` branch.** Rejected: now we have two code paths to maintain, plus the `lsof`-missing-on-minimal-Linux problem still exists. `psutil` is strictly better on Linux too — no timeout-on-stuck-lsof failure mode, no parsing fragile whitespace output.

## Consequences

Positive:
- One code path, three platforms. ~60 lines of shell-out parsing deleted from `detector.py`.
- No more `subprocess.TimeoutExpired` handling for process enumeration — `psutil` raises typed `NoSuchProcess`/`AccessDenied`/`ZombieProcess` exceptions, easy to skip per-process.
- Works in Docker containers that don't ship `procps`/`lsof`.
- Detects Claude processes owned by other users (where permitted by the OS) — same behaviour as `pgrep -x`, possibly slightly broader on Windows.
- Easier to unit-test: monkeypatch `psutil.process_iter` instead of stubbing `subprocess.run`.

Negative:
- Adds a C-extension dep. Mitigated by the availability of prebuilt wheels.
- `psutil.Process.open_files()` on Windows requires the process to be queryable; Admin-owned claude processes may be invisible to a non-admin server. Acceptable: we already can't touch those sessions' files either.
- `open_files()` is noticeably slower than parsing `lsof` text when many files are open. Mitigation: poll cadence is 30s (see `server.py:47`); negligible cost.

## Implementation Notes

- Regex `detector.py:21` (`/\.claude/tasks/<uuid>/`) needs a Windows-aware variant. Use `pathlib.PurePath` checks on each `open_files()` entry instead of regexing a stringified path.
- Preserve the existing `ProcessInfo` shape so downstream logic in `_get_claude_processes`, `get_active_session_map` is unchanged.
- Catch `psutil.Error` broadly in the enumeration loop and skip — mirrors the current `subprocess.TimeoutExpired, FileNotFoundError` catch.
