"""Same-host messaging gateway lifecycle helpers (W2 Opt-D + W13 layer C).

Controls the **messaging gateway** for the active INTELLECT_HOME / profile —
not the WebUI process and not ``gateway_watcher``.

Operations share one lock and ``_STATE`` including ``operation`` so agent-update
restart proof (DECIDED #4) cannot treat start/stop completion as restart success.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "status": "idle",  # idle | in_progress | completed | busy | failed
    "operation": None,  # restart | start | stop | None
    "message": "",
    "started_at": None,
    "finished_at": None,
}

_PANEL_WAIT_CAP_S = 60.0


def get_restart_status() -> dict[str, Any]:
    """Backward-compatible alias for shared lifecycle status."""
    return get_lifecycle_status()


def get_lifecycle_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def lifecycle_http_status(result: dict[str, Any]) -> int:
    """HTTP status for lifecycle JSON: 409 only when busy; terminal results are 200."""
    if result.get("status") == "busy":
        return 409
    return 200


def _set_state(**kwargs: Any) -> dict[str, Any]:
    with _LOCK:
        _STATE.update(kwargs)
        return dict(_STATE)


def _resolve_intellect_cli() -> list[str]:
    """Return argv prefix to invoke ``intellect`` with the active home."""
    which = shutil.which("intellect")
    if which:
        return [which]
    return [sys.executable, "-m", "intellect_cli"]


def _active_home() -> Path:
    """Prefer request-scoped WebUI profile home when available."""
    try:
        from api.profiles import get_active_intellect_home

        return Path(get_active_intellect_home())
    except Exception:
        pass
    try:
        from intellect_constants import get_intellect_home

        return Path(get_intellect_home())
    except Exception:
        raw = os.environ.get("INTELLECT_HOME") or ""
        if raw:
            return Path(raw)
        return Path.home() / ".intellect"


def _profile_args() -> list[str]:
    """Pass ``--profile`` for the request-scoped active profile when named."""
    try:
        from api.profiles import get_active_profile_name

        name = str(get_active_profile_name() or "").strip()
        if name and name != "default":
            return ["--profile", name]
    except Exception:
        pass
    home = _active_home()
    try:
        if home.parent.name == "profiles":
            return ["--profile", home.name]
    except Exception:
        pass
    return []


def _in_gateway_process() -> bool:
    return os.getenv("_HERMES_GATEWAY") == "1" or os.getenv("_INTELLECT_GATEWAY") == "1"


def _run_gateway_cli(action: str) -> tuple[bool, str]:
    if _in_gateway_process():
        return False, f"Cannot {action} gateway from inside the gateway process"
    if action not in ("restart", "start", "stop"):
        return False, f"Unknown gateway action: {action}"
    cmd = _resolve_intellect_cli() + _profile_args() + ["gateway", action]
    env = os.environ.copy()
    env["INTELLECT_HOME"] = str(_active_home())
    env.pop("_HERMES_GATEWAY", None)
    env.pop("_INTELLECT_GATEWAY", None)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return False, "intellect CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"gateway {action} timed out"
    except OSError as exc:
        return False, f"gateway {action} failed to start ({type(exc).__name__})"

    if proc.returncode == 0:
        return True, f"Gateway {action} completed"
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = err[-1] if err else f"exit {proc.returncode}"
    if len(detail) > 200:
        detail = detail[:200] + "…"
    logger.warning("gateway %s failed: rc=%s detail=%s", action, proc.returncode, detail)
    return False, f"Gateway {action} failed: {detail}"


def _run_gateway_restart() -> tuple[bool, str]:
    return _run_gateway_cli("restart")


def _request_gateway_op(
    operation: str,
    *,
    wait: bool = False,
    wait_cap_s: Optional[float] = None,
    runner: Optional[Callable[[], tuple[bool, str]]] = None,
) -> dict[str, Any]:
    """Start a gateway lifecycle op. Fast-return ``in_progress`` unless ``wait``."""
    run = runner or (lambda: _run_gateway_cli(operation))
    if not _LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "status": "busy",
            "operation": _STATE.get("operation"),
            "message": "Gateway lifecycle operation already in progress",
        }
    try:
        if _STATE.get("status") == "in_progress":
            return {
                "ok": False,
                "status": "busy",
                "operation": _STATE.get("operation"),
                "message": "Gateway lifecycle operation already in progress",
            }
        _STATE.update(
            {
                "status": "in_progress",
                "operation": operation,
                "message": f"{operation.capitalize()} messaging gateway for active profile…",
                "started_at": time.time(),
                "finished_at": None,
            }
        )
    finally:
        _LOCK.release()

    def _worker():
        ok, message = run()
        _set_state(
            status="completed" if ok else "failed",
            operation=operation,
            message=message,
            finished_at=time.time(),
        )

    if wait:
        cap = _PANEL_WAIT_CAP_S if wait_cap_s is None else float(wait_cap_s)
        deadline = time.time() + max(1.0, cap)
        thread = threading.Thread(target=_worker, name=f"gateway-{operation}", daemon=True)
        thread.start()
        thread.join(timeout=max(0.1, deadline - time.time()))
        st = get_lifecycle_status()
        if st.get("status") == "in_progress":
            # Honest: CLI may still be running (subprocess timeout 120s). Do not
            # claim failed while _STATE remains in_progress.
            return {
                "ok": True,
                "status": "in_progress",
                "timed_out": True,
                "operation": operation,
                "message": (
                    f"Gateway {operation} still running after {int(cap)}s — "
                    "poll /api/health/restart/status"
                ),
            }
        return st | {"ok": st.get("status") == "completed", "timed_out": False}

    threading.Thread(target=_worker, name=f"gateway-{operation}", daemon=True).start()
    time.sleep(0.05)
    st = get_lifecycle_status()
    return {
        "ok": True,
        "status": st.get("status") or "in_progress",
        "operation": operation,
        "message": st.get("message") or "",
    }


def request_gateway_restart(*, wait: bool = False, wait_cap_s: Optional[float] = None) -> dict[str, Any]:
    return _request_gateway_op("restart", wait=wait, wait_cap_s=wait_cap_s)


def request_gateway_start(*, wait: bool = False, wait_cap_s: Optional[float] = None) -> dict[str, Any]:
    return _request_gateway_op("start", wait=wait, wait_cap_s=wait_cap_s)


def request_gateway_stop(*, wait: bool = False, wait_cap_s: Optional[float] = None) -> dict[str, Any]:
    return _request_gateway_op("stop", wait=wait, wait_cap_s=wait_cap_s)


def ensure_gateway_restarted_for_agent_update(*, timeout_s: float = 90.0) -> dict[str, Any]:
    """DECIDED #4: agent update must prove messaging gateway **restart**."""
    result = request_gateway_restart(wait=False)
    if result.get("status") == "busy":
        return {"ok": False, "status": "busy", "message": result.get("message") or "busy"}
    deadline = time.time() + max(5.0, float(timeout_s))
    while time.time() < deadline:
        st = get_lifecycle_status()
        status = st.get("status")
        operation = st.get("operation")
        if status == "completed" and operation == "restart":
            return {"ok": True, "status": "completed", "message": st.get("message") or "ok"}
        if status == "completed" and operation in ("start", "stop"):
            return {
                "ok": False,
                "status": "failed",
                "message": f"Expected restart proof, saw {operation} completed",
            }
        if status == "failed":
            return {
                "ok": False,
                "status": "failed",
                "message": st.get("message") or "Gateway restart failed",
            }
        time.sleep(0.4)
    return {
        "ok": False,
        "status": "failed",
        "message": "Gateway restart did not complete in time",
    }
