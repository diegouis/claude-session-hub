# Cross-Platform Support Specification

Status: Draft
Owner: TBD
Target: `claude-session-hub` v0.x (current: macOS-only)

## Goals

- First-class support for Linux (X11 + Wayland desktop, headless-with-copy fallback) and Windows 10/11, alongside macOS.
- Identical feature set where the OS permits it: indexing, search, star/label/archive/delete, bulk ops, export, theme toggle.
- Active-session detection (green badge) working on all three platforms without shelling out to `pgrep`/`lsof`.
- Resume-in-terminal working on all three platforms with a graceful "copy command" fallback when no supported terminal is found.
- Stay `pip install -r requirements.txt && python3 run.py` — no platform-specific install steps beyond Python itself.

## Non-Goals

- WSL is **not** a substitute for native Windows support. WSL users get the Linux experience; `cmd.exe`/PowerShell users get the Windows experience.
- No mobile platforms.
- No system-tray/menu-bar apps in Phase 1–3 (future work).
- No auto-start-on-login (future work).
- Not targeting Python <3.10 (already stated in README).

## Inventory of Platform-Dependent Code

Severity: **blocker** = feature broken on target platform; **workaround** = degraded but usable; **nit** = cosmetic.

| Location | What it does | Platforms affected | Severity |
|---|---|---|---|
| `detector.py:34` — `pgrep -x claude` | List running Claude PIDs | Windows (no `pgrep`); some minimal Linux containers | blocker on Windows |
| `detector.py:51` — `ps -p <pid> -o args=` | Get command line | Windows (no `ps`); BusyBox `ps` differs | blocker on Windows |
| `detector.py:73,88` — `lsof -p <pid>` (twice) | Get cwd + open `/tasks/<uuid>/` dirs | Windows (no `lsof`); many Linux containers don't ship it | blocker on Windows; workaround on bare Linux |
| `detector.py:21` — `re.compile(r"/\.claude/tasks/...")` | Matches POSIX path segment inside `lsof` output | Windows uses `\` | blocker on Windows |
| `server.py:262` — `shlex.quote(cwd)` | Quote cwd for POSIX `sh` | Not valid for `cmd.exe`/PowerShell | blocker on Windows |
| `server.py:268` — `osascript -e 'tell app "Terminal"...'` | Launch Terminal.app | macOS only — correct | n/a |
| `server.py:271–281` — gnome-terminal/xterm/konsole probe | Launch Linux terminal | Stubbed, never exercised; missing Alacritty, kitty, Terminator, tilix, wezterm; wayland specifics | blocker on Linux (feature exists but untested) |
| `server.py:282–285` — `start cmd /k "cd /d <cwd> && claude -r ..."` | Launch `cmd.exe` | Stubbed; no quoting of `cwd`; no Windows Terminal (`wt.exe`) or PowerShell fallback; `cd /d` correct only when path exists | blocker on Windows |
| `indexer.py:14`, `detector.py:13`, `server.py:33` — `Path.home() / ".claude"` | Data dir | `Path.home()` works cross-platform (maps to `C:\Users\<u>` / `$HOME`), but Claude Code's actual Windows location is unconfirmed — see Open Questions | workaround |
| `indexer.py:23–37` — `decode_project_path` | URL-encoded dir name → path | Assumes POSIX `/`. Windows cwd `C:\foo\bar` isn't representable in a dir name using only `-`. Need to confirm Claude Code's Windows encoding scheme | blocker on Windows |
| `server.py:977` — `Path.home() / ".claude" / "projects"` | Hardcoded fallback in `restore_from_trash` | Should re-use centralised constant | workaround |
| `Dockerfile:14` — `-v ~/.claude:/root/.claude:ro` | Host bind mount path | Windows Docker Desktop uses different syntax; only affects docs | nit |
| `Makefile:17` — `rm -rf data/ __pycache__/` | Clean target | No Windows equivalent; Makefile itself requires `make` | nit |
| `run.py:22` — `host = "0.0.0.0" if os.environ.get("DOCKER") else "127.0.0.1"` | Bind host | Works everywhere | n/a |

No other platform-sensitive code found (`webbrowser.open`, SQLite, FastAPI, Jinja are all cross-platform).

## Proposed `platform_compat.py` Abstraction

Create a new module (name: `platform_compat.py` — avoids shadowing stdlib `platform`). Public API:

```python
def claude_data_dir() -> Path: ...
    # Returns the Claude Code data root, honouring $CLAUDE_DIR.
    # Default: Path.home() / ".claude" on all platforms (pending OQ-1).

def find_claude_processes() -> list[ProcessInfo]: ...
    # Returns running claude processes with pid, cwd, cmdline, open_files.
    # Implementation uses psutil — single code path, no shell-outs.

def open_terminal_with_command(cwd: str, command: str) -> tuple[bool, str]: ...
    # Returns (launched, description). On failure returns (False, reason)
    # and the caller falls back to "copy command" (existing Docker path).

def quote_for_shell(arg: str) -> str: ...
    # POSIX: shlex.quote. Windows: subprocess.list2cmdline for cmd.exe.
```

Module layout:

```
platform_compat/
    __init__.py          # re-exports the four functions
    _common.py           # ProcessInfo dataclass, data dir logic
    _macos.py            # osascript terminal launcher
    _linux.py            # terminal probe list + DESKTOP_SESSION heuristics
    _windows.py          # wt.exe / powershell / cmd.exe launcher
```

Dispatch via `sys.platform` at import time into a single implementation object. No `if platform.system() == ...` scattered through callers.

See `adr-001-platform-abstraction-layer.md` for the decision rationale.

## Platform Matrix

| Feature | macOS | Linux (GNOME/KDE/XFCE) | Linux (headless) | Windows 10/11 |
|---|---|---|---|---|
| Indexing `~/.claude/projects` | works | works | works | works (pending OQ-1) |
| Full-text search | works | works | works | works |
| Active detection (running) | works | works via psutil | works via psutil | works via psutil |
| Active detection (open-but-idle) | works | works | works | works |
| Resume → terminal | Terminal.app | first found of: gnome-terminal, konsole, xfce4-terminal, alacritty, kitty, wezterm, tilix, xterm | copy command | Windows Terminal (`wt.exe`) → PowerShell → `cmd.exe` |
| Copy-command fallback | yes | yes | yes | yes |
| Export, archive, trash | works | works | works | works |
| SSE live updates | works | works | works | works |

## Packaging Strategy

**Decision: stay pip-installable everywhere; no per-platform binaries in Phase 1–3.**

Rationale:
- Current install is two commands. PyInstaller/Briefcase bundles are large, slow to build, and hide the fact that users already have Python (Claude Code itself requires Node, and its users typically have Python nearby).
- The only new Python dep is `psutil`, which ships prebuilt wheels for macOS/Linux/Windows × CPython 3.10–3.12 — no compiler needed.
- `requirements.txt` becomes: `fastapi`, `uvicorn[standard]`, `jinja2`, `psutil>=5.9`.
- Revisit bundling only if we add a system tray (Phase 4+).

Docker image continues to work on Linux-host Docker; Windows Docker Desktop docs get a one-liner in README.

## CI Matrix

GitHub Actions workflow `.github/workflows/ci.yml`:

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, windows-latest, macos-latest]
    python: ["3.10", "3.12"]
```

Per-job steps:
1. Checkout
2. Setup Python
3. `pip install -r requirements.txt`
4. Import-smoke: `python -c "import server, indexer, detector, platform_compat"`
5. Unit tests: `pytest tests/` (new suite — see Testing Strategy)
6. Playwright GUI test: `python3 test_gui.py --headless` — macOS + Linux only initially; Windows Playwright works but headless Chromium on Win runners can be flaky, enable after Phase 2 stabilises.

No secrets required. Runs on push + PR.

## Testing Strategy

Three layers:

1. **Unit tests for `platform_compat`** — mock `sys.platform`, `subprocess.run`, `psutil.process_iter`. Cover each branch: terminal found, terminal missing, psutil raises AccessDenied. Run on every OS but actually exercise all branches via mocks.

2. **Integration tests** — real `psutil` against the test process itself; skip branches that require a real `claude` binary on the runner (gate with `pytest.mark.requires_claude_cli`).

3. **Manual smoke checklist per platform** (maintained in `docs/specs/manual-smoke.md`, out of scope for this spec):
   - Launch server, index runs, dashboard loads.
   - Start a real `claude` session, verify green badge appears within 30s.
   - Click Resume → new terminal opens in correct cwd with `claude -r <uuid>`.
   - Copy-command fallback triggers when we force-disable terminal probes (env var `HUB_FORCE_COPY=1`).

Acceptance gate for a platform: all manual steps pass + CI green on the matching matrix cell.

## Rollout Phases

**Phase 1 — Linux parity** (est. 3–5 person-days)
- Introduce `platform_compat` module; move macOS code into `_macos.py` unchanged.
- Add `psutil` to `requirements.txt`; rewrite `detector.py:_get_claude_processes` to use `psutil.process_iter(['pid','name','cmdline','cwd','open_files'])`. Remove `pgrep`/`ps`/`lsof` shell-outs.
- Expand `_linux.py` terminal probe list; add `$DESKTOP_SESSION` / `$XDG_CURRENT_DESKTOP` hinting.
- CI: add `ubuntu-latest` to matrix.
- Update README: drop "Linux support is straightforward to add" line.

**Phase 2 — Windows support** (est. 5–8 person-days)
- `_windows.py`: terminal launcher probe order `wt.exe` → `powershell.exe` → `cmd.exe`. Use `subprocess.list2cmdline` for quoting; `CREATE_NEW_CONSOLE` flag so the parent server doesn't inherit stdio.
- Confirm Claude Code's Windows data dir (OQ-1); adjust `claude_data_dir()` if it's not `%USERPROFILE%\.claude`.
- Audit `decode_project_path` against an actual Windows-encoded dir name — likely needs a drive-letter carve-out (OQ-2).
- CI: add `windows-latest` to matrix.
- README: Quick Start snippet for PowerShell.

**Phase 3 — Polish** (est. 2–3 person-days)
- User-configurable preferred terminal (env var `HUB_TERMINAL` or a settings.json key).
- Centralise `Path.home() / ".claude"` uses; remove the `server.py:977` hardcode.
- Better error messages when terminal launch fails (surface `stderr` to the user).
- Optional: system notification when a new active session appears (future work; out of scope).

## Open Questions

1. **Claude Code's Windows data directory.** Is it `%USERPROFILE%\.claude\projects`, `%APPDATA%\claude`, or `%LOCALAPPDATA%\claude`? Must confirm empirically before shipping Phase 2.
2. **Windows project-dir encoding.** On POSIX, `/Users/me/foo` becomes `-Users-me-foo`. On Windows, how does Claude Code encode `C:\Users\me\foo`? Options: `C--Users-me-foo`, `C-Users-me-foo`, or something else. Need a real session to inspect.
3. **`psutil` as a hard dep.** It's ~500KB and pulls in a small C extension. Acceptable? Alternative is a thin `_process_posix.py` / `_process_windows.py` split using `ctypes` + `/proc` — more code, zero deps.
4. **Wayland terminal launch.** Some Wayland compositors refuse non-DBus launches. Is xdg-terminal-exec available widely enough to rely on? Fallback is copy-command, which is acceptable.
5. **Subagent `/tasks/<uuid>/` path on Windows.** `detector.py:21` hardcodes a POSIX regex. Need to know the Windows equivalent before adapting.

## References

- Current platform-gated code: `detector.py`, `server.py:257–287`, `server.py:574–627`.
- README current limitation: line 65 — "macOS (for Terminal.app resume — Linux support is straightforward to add)".
- Existing fallback that proves the copy-command UX works: `server.py:601–602` (Docker path).
