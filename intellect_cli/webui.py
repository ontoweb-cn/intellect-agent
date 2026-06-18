"""
WebUI management commands for the intellect CLI.

Handles: intellect webui [start|stop|restart|status|logs]

The webui server runs as a background daemon managed via PID file.
Process management mirrors the ``ctl.sh`` pattern from the standalone
intellect-webui repository, adapted to the agent CLI conventions.

PID / log / state files live under ~/.intellect/:
    ~/.intellect/webui.pid         PID file
    ~/.intellect/webui.log         Server log
    ~/.intellect/webui.ctl.env     Runtime state (host, port, started_at)

Cross-platform support (Linux, macOS, Windows):
    - pid_exists via psutil (core dependency) — avoids the Windows
      ``os.kill(pid, 0)`` footgun documented in gateway/status.py:338.
    - Process termination via gateway.status.terminate_pid().
    - Process detachment via intellect_cli._subprocess_compat.
    - Log viewing uses pure-Python reads (no ``tail`` dependency).
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from intellect_cli.config import get_intellect_home
from intellect_cli.colors import Colors, color

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_HOST = os.getenv("INTELLECT_WEBUI_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("INTELLECT_WEBUI_PORT", "9119"))

_INTELLECT_HOME = Path(get_intellect_home())
_PID_FILE = _INTELLECT_HOME / "webui.pid"
_LOG_FILE = _INTELLECT_HOME / "webui.log"
_STATE_FILE = _INTELLECT_HOME / "webui.ctl.env"


# ── Cross-platform PID helpers ─────────────────────────────────────────────

def _pid_from_file() -> int | None:
    """Read the PID from the PID file, validating format."""
    try:
        raw = _PID_FILE.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform "is this PID alive?" check.

    Uses psutil (core dependency) which correctly handles Windows via
    OpenProcess + GetExitCodeProcess, avoiding the dangerous
    ``os.kill(pid, 0)`` quirk on Windows (see gateway/status.py:338).
    """
    try:
        import psutil  # type: ignore[import-not-found]
        return bool(psutil.pid_exists(int(pid)))
    except ImportError:
        pass

    # psutil unavailable — fall back to os.kill(pid, 0) which is safe on
    # POSIX but NOT on Windows.  On Windows this code path should never
    # be reached because psutil is a core dependency.
    try:
        os.kill(int(pid), 0)  # windows-footgun: safe only on POSIX
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _proc_args(pid: int) -> str:
    """Return the process command line, cross-platform.

    Prefers psutil (works on all platforms) with fallbacks to /proc
    (Linux) and ``ps`` (POSIX).  Never shells out to a missing command
    on Windows.
    """
    # 1. psutil — canonical cross-platform answer
    try:
        import psutil  # type: ignore[import-not-found]
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
        if cmdline:
            return " ".join(cmdline)
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # 2. /proc/<pid>/cmdline — Linux fast path (no subprocess)
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # 3. ps — macOS / other POSIX fallback; does not exist on Windows
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return ""


def _is_webui_process(pid: int) -> bool:
    """Verify that the process looks like a webui server."""
    args = _proc_args(pid)
    if not args:
        return False
    # Check for both POSIX and Windows path separators
    return (
        "webui/server.py" in args
        or "webui\\server.py" in args
        or "webui.server" in args
    )


def _get_running_pid() -> int | None:
    """Return the PID of a running webui server, or None."""
    pid = _pid_from_file()
    if pid is None:
        return None
    if _is_pid_alive(pid) and _is_webui_process(pid):
        return pid
    # Stale PID file
    _clear_stale_files()
    return None


def _clear_stale_files() -> None:
    """Remove stale PID and state files."""
    for f in (_PID_FILE, _STATE_FILE):
        try:
            f.unlink()
        except (FileNotFoundError, OSError):
            pass


# ── State file helpers ─────────────────────────────────────────────────────

def _write_state(pid: int, host: str, port: int) -> None:
    """Write runtime state file for status display."""
    content = (
        f"PID={pid}\n"
        f"HOST={host}\n"
        f"PORT={port}\n"
        f"LOG_FILE={_LOG_FILE}\n"
        f"STARTED_AT={_now_utc()}\n"
    )
    _STATE_FILE.write_text(content, encoding="utf-8")


def _load_state() -> dict[str, str]:
    """Load runtime state from the state file."""
    state: dict[str, str] = {}
    try:
        for line in _STATE_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            state[k.strip()] = v.strip()
    except (FileNotFoundError, OSError):
        pass
    return state


def _now_utc() -> str:
    """Return current UTC time as ISO 8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Health check ───────────────────────────────────────────────────────────

def _health_check(host: str, port: int, timeout: float = 2.0) -> str:
    """Perform a lightweight health check against the webui /health endpoint."""
    import json as _json
    import urllib.request as _req
    url = f"http://{host}:{port}/health"
    try:
        with _req.urlopen(url, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            sessions = data.get("sessions", data.get("session_count", "?"))
            active = data.get("active_streams", "?")
            status = data.get("status", "ok")
            if status == "ok":
                return f"ok ({sessions} sessions, {active} active streams)"
            return status
    except Exception:
        return f"unreachable ({url})"


# ── Uptime ─────────────────────────────────────────────────────────────────

def _uptime(pid: int) -> str:
    """Return the human-readable uptime for a process, cross-platform.

    Uses psutil.create_time() on all platforms; falls back to ``ps`` on
    POSIX.  Returns "unknown" when the process can't be queried.
    """
    try:
        import psutil  # type: ignore[import-not-found]
        proc = psutil.Process(pid)
        create_time = proc.create_time()
        if create_time:
            elapsed = int(time.time() - create_time)
            if elapsed < 60:
                return f"00:{elapsed:02d}"
            elif elapsed < 3600:
                return f"{elapsed // 60:02d}:{elapsed % 60:02d}"
            else:
                hours = elapsed // 3600
                minutes = (elapsed % 3600) // 60
                seconds = elapsed % 60
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # POSIX fallback: ps -p <pid> -o etime=
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return "unknown"


# ── Start ──────────────────────────────────────────────────────────────────

def webui_start(args) -> None:
    """Start the webui server as a background daemon.

    On POSIX, detaches via ``start_new_session=True`` (os.setsid).  On
    Windows, uses ``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS |
    CREATE_NO_WINDOW`` creationflags so the child survives console close
    without flashing a cmd window.
    """
    host = getattr(args, "host", None) or DEFAULT_HOST
    port = getattr(args, "port", None) or DEFAULT_PORT

    # Ensure state directory exists
    _INTELLECT_HOME.mkdir(parents=True, exist_ok=True)

    # Check if already running
    existing_pid = _get_running_pid()
    if existing_pid is not None:
        print(f"[webui] Already running (PID {existing_pid})")
        return

    _clear_stale_files()

    # Touch log file
    _LOG_FILE.touch(exist_ok=True)

    # Launch server as background process.
    # Use sys.executable so it runs in the same venv as the CLI.
    env = os.environ.copy()
    env["INTELLECT_WEBUI_HOST"] = host
    env["INTELLECT_WEBUI_PORT"] = str(port)

    # Cross-platform detach: POSIX uses start_new_session; Windows uses
    # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW.
    # On Windows, prefer a real pythonw.exe (GUI-subsystem, windowless).
    # The venv pythonw.exe is just a copy of python.exe (console-subsystem)
    # and still creates a blank window.  The real windowless binary lives
    # at sys.base_prefix/pythonw.exe (the parent Python installation).
    from intellect_cli._subprocess_compat import windows_detach_popen_kwargs

    _python_exe = sys.executable
    if sys.platform == "win32":
        _pyw = Path(getattr(sys, "base_prefix", sys.prefix)) / "pythonw.exe"
        if _pyw.is_file():
            _python_exe = str(_pyw)

    with open(_LOG_FILE, "a", encoding="utf-8") as log_fp:
        process = subprocess.Popen(
            [_python_exe, "-m", "webui.server"],
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            close_fds=True,
            **windows_detach_popen_kwargs(),
        )

    pid = process.pid
    _PID_FILE.write_text(str(pid), encoding="utf-8")
    _write_state(pid, host, port)

    # Brief wait to confirm it's still running
    time.sleep(0.3)
    if not _is_pid_alive(pid):
        print(f"[webui] Failed to stay running. Check log: {_LOG_FILE}", file=sys.stderr)
        _clear_stale_files()
        raise SystemExit(1)

    print(f"[webui] Started (PID {pid})")
    print(f"[webui] Bound: {host}:{port}")
    print(f"[webui] Log:   {_LOG_FILE}")
    print(f"[webui] Open:  http://{host}:{port}")


# ── Stop ───────────────────────────────────────────────────────────────────

def webui_stop(args) -> None:  # noqa: ARG001
    """Stop a running webui server gracefully.

    On POSIX, sends SIGTERM with a 5-second grace period then SIGKILL.
    On Windows, uses ``taskkill /T /F`` for a true tree-killing hard stop
    (os.kill with SIGTERM is not equivalent on Windows).
    """
    from gateway.status import terminate_pid

    pid = _pid_from_file()

    if pid is None:
        print("[webui] Not running")
        _clear_stale_files()
        return

    if not _is_pid_alive(pid) or not _is_webui_process(pid):
        print("[webui] Not running (stale PID)")
        _clear_stale_files()
        return

    print(f"[webui] Stopping (PID {pid})")

    # Graceful shutdown via platform-appropriate mechanism
    try:
        terminate_pid(pid, force=False)
    except (ProcessLookupError, PermissionError, OSError):
        _clear_stale_files()
        print("[webui] Stopped")
        return

    # Wait up to 5 seconds for clean exit
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            _clear_stale_files()
            print("[webui] Stopped")
            return
        time.sleep(0.2)

    # Force kill if still alive
    print("[webui] Not responding; forcing termination", file=sys.stderr)
    try:
        terminate_pid(pid, force=True)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    _clear_stale_files()
    print("[webui] Stopped")


# ── Restart ────────────────────────────────────────────────────────────────

def webui_restart(args) -> None:
    """Stop then start the webui server."""
    webui_stop(args)
    webui_start(args)


# ── Status ─────────────────────────────────────────────────────────────────

def webui_status(args) -> None:  # noqa: ARG001
    """Show the running status of the webui server."""
    _INTELLECT_HOME.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    host = state.get("HOST", DEFAULT_HOST)
    port = int(state.get("PORT", str(DEFAULT_PORT)))

    pid = _get_running_pid()
    if pid is not None:
        uptime_val = _uptime(pid)
        health = _health_check(host, port)
        print(f"{color('●', Colors.GREEN)} intellect-webui — running")
        print(f"  PID:     {pid}")
        print(f"  Uptime:  {uptime_val}")
        print(f"  Bound:   {host}:{port}")
        print(f"  Log:     {_LOG_FILE}")
        print(f"  Health:  {health}")
    else:
        if _pid_from_file() is not None:
            _clear_stale_files()
        print(f"{color('●', Colors.DIM)} intellect-webui — stopped")
        print(f"  PID:     -")
        print(f"  Bound:   {host}:{port}")
        print(f"  Log:     {_LOG_FILE}")
        print(f"  Health:  not checked")


# ── Logs ───────────────────────────────────────────────────────────────────

def webui_logs(args) -> None:
    """Show the webui server log file.

    Uses a pure-Python read (no ``tail`` dependency) so it works on
    every platform without an external command.  The ``--follow`` mode
    uses ``tail -f`` when available (POSIX) and falls back to a one-shot
    read on Windows.
    """
    lines = getattr(args, "lines", 100) or 100
    follow = getattr(args, "follow", False)

    _LOG_FILE.touch(exist_ok=True)

    if follow:
        # Try tail -f for live following (POSIX); fall back to one-shot
        try:
            subprocess.run(["tail", "-n", str(lines), "-f", str(_LOG_FILE)])
            return
        except FileNotFoundError:
            print("[webui] tail -f not available; showing last lines only", file=sys.stderr)
        except KeyboardInterrupt:
            return

    # Pure-Python read: works on all platforms
    try:
        content = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        print("[webui] Log file not found or unreadable", file=sys.stderr)
        return

    content_lines = content.splitlines()
    for line in content_lines[-lines:]:
        print(line)


# ── Command dispatch ───────────────────────────────────────────────────────

def webui_command(args):
    """Dispatch ``intellect webui <subcommand>``."""
    # Ensure home directory exists
    _INTELLECT_HOME.mkdir(parents=True, exist_ok=True)

    sub = getattr(args, "webui_command", None)

    if sub == "start":
        webui_start(args)
    elif sub == "stop":
        webui_stop(args)
    elif sub == "restart":
        webui_restart(args)
    elif sub == "status" or sub is None:
        webui_status(args)
    elif sub == "logs":
        webui_logs(args)
    else:
        print(f"[webui] Unknown command: {sub}", file=sys.stderr)
        print("Usage: intellect webui [start|stop|restart|status|logs]", file=sys.stderr)
        raise SystemExit(2)
