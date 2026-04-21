# ADR-003: Cross-Platform Terminal Launch Strategy

Status: Proposed
Date: 2026-04-21

## Context

Resume-in-terminal is the core interactive feature: user clicks a session → a fresh terminal opens in the session's cwd running `claude -r <uuid>`.

Current state (`server.py:257–287`):
- macOS: `osascript -e 'tell app "Terminal" to do script "..."'` — works.
- Linux: probes `gnome-terminal`, `xterm`, `konsole` via `shutil.which`. Stubbed, never tested. Misses Alacritty, kitty, wezterm, tilix, Terminator, xfce4-terminal, Windows Terminal under WSLg, etc.
- Windows: `start cmd /k "cd /d <cwd> && claude -r ..."` via `cmd /c`. Stubbed, no quoting of `<cwd>`, ignores Windows Terminal (`wt.exe`) and PowerShell.

The frontend already handles "no terminal available" gracefully: `server.py:606–613` returns `{"status": "copy", "command": ...}` and the UI shows the command with a copy button (this is the Docker path). We should reuse that fallback aggressively.

## Decision

Per-platform probe lists, in descending order of niceness. First match wins. If none match, return `None` from `open_terminal_with_command` and let the existing copy-command UX handle it.

**macOS** (unchanged):
1. Terminal.app via `osascript`.

**Linux:**
1. `wt.exe` — special-case: if we detect WSLg and `wt.exe` is on PATH, prefer it.
2. `gnome-terminal` — `gnome-terminal -- bash -c "<cmd>; exec bash"`
3. `konsole` — `konsole -e bash -c "<cmd>; exec bash"`
4. `xfce4-terminal` — same pattern
5. `alacritty`, `kitty`, `wezterm`, `tilix`, `terminator` — each with its own `-e` invocation
6. `xterm` — last-resort GUI
7. `$TERMINAL` env var if set, before the above list
8. Return `None` → copy-command fallback.

**Windows:**
1. `wt.exe` (Windows Terminal) — `wt.exe -d <cwd> powershell -NoExit -Command "claude -r <uuid>"`
2. `powershell.exe` — `start powershell -NoExit -Command "Set-Location '<cwd>'; claude -r <uuid>"` via `cmd /c`
3. `cmd.exe` — `start "" cmd /k "cd /d <cwd> && claude -r <uuid>"` via `cmd /c`
4. Return `None` → copy-command fallback.

Quoting: use `subprocess.list2cmdline` for Windows, `shlex.quote` for POSIX. Spawn with `subprocess.Popen` + `CREATE_NEW_CONSOLE` on Windows and `start_new_session=True` on POSIX so the child terminal outlives the server request.

## Alternatives Considered

**A. User-configured preferred terminal only.** Remove probing entirely; require users to set `HUB_TERMINAL=...`. Rejected: raises the out-of-box friction from zero to "read docs, set env var". The probe list handles 95% of desktops with zero configuration. Keep configurability as an **override** (Phase 3), not a requirement.

**B. Web-based copy-only flow for everyone.** Delete the terminal launcher, always return the command. Rejected: the "one click and a terminal opens" moment is a headline feature. On macOS it already works beautifully; removing it would be a regression.

**C. xdg-terminal-exec on Linux only.** Use the freedesktop standard launcher. Tempting but its install base is thin (2024+ GNOME only). Keep as an optional first probe once it's more universal.

**D. `x-terminal-emulator` (Debian alternatives).** Rejected as primary: Debian-only. Kept as a mid-priority probe entry.

## Consequences

Positive:
- Users on any mainstream desktop get one-click resume with no config.
- The copy-command fallback guarantees that even exotic setups (i3 with no configured terminal, headless SSH, etc.) don't block the feature — they just get one extra click.
- `subprocess.list2cmdline` + `shlex.quote` behind `quote_for_shell` eliminates the current unquoted-path bug at `server.py:284` on Windows.

Negative:
- Probe list will need occasional maintenance (new terminals appear).
- Some terminal emulators refuse to run unless DBus-activated (some Wayland compositors). Users hit the copy-command fallback in that case — acceptable, and we can document the `$HUB_TERMINAL` override.

Neutral:
- Resume latency on Linux may be slightly higher than macOS because `shutil.which` is called up to ~8 times per resume. Bounded and not user-perceptible (sub-millisecond each).

## Implementation Notes

- Probe results should be cached for the life of the server process. Users don't install terminals mid-session.
- Log which terminal was selected at `INFO` level so users can diagnose when probing picks the "wrong" one and set `$HUB_TERMINAL` to override (Phase 3).
- The `claude -r <uuid>` literal assumes `claude` is on `PATH` in the spawned terminal. This is already an assumption on macOS; document it in README.
- Preserve the subagent → parent-session resume logic currently in `server.py:585–596`. It's orthogonal to platform.
