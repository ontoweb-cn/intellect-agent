"""Same-host messaging gateway restart helpers (W2 Opt-D).

Restarts the **messaging gateway** for the active INTELLECT_HOME / profile —
not the WebUI process and not ``gateway_watcher``.
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
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "status": "idle",  # idle | in_progress | completed | busy | failed
    "message": "",
    "started_at": None,
    "finished_at": None,
}


def get_restart_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _set_state(**kwargs: Any) -> dict[str, Any]:
    with _LOCK:
        _STATE.update(kwargs)
        return dict(_STATE)


def _resolve_intellect_cli() -> list[str]:
    """Return argv prefix to invoke ``intellect`` with the active home."""
    which = shutil.which("intellect")
    if which:
        return [which]
    # Fall back to the running interpreter + module entry if present.
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
    # Fallback: infer from INTELLECT_HOME …/profiles/<name>
    home = _active_home()
    try:
        if home.parent.name == "profiles":
            return ["--profile", home.name]
    except Exception:
        pass
    return []


def _run_gateway_restart() -> tuple[bool, str]:
    if os.getenv("_HERMES_GATEWAY") == "1" or os.getenv("_INTELLECT_GATEWAY") == "1":
        return False, "Cannot restart gateway from inside the gateway process"
    cmd = _resolve_intellect_cli() + _profile_args() + ["gateway", "restart"]
    env = os.environ.copy()
    env["INTELLECT_HOME"] = str(_active_home())
    # Never inherit a nested-gateway sentinel into the child.
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
        return False, "gateway restart timed out"
    except OSError as exc:
        return False, f"gateway restart failed to start ({type(exc).__name__})"

    if proc.returncode == 0:
        return True, "Gateway restart completed"
    # Prefer short stderr tail; never dump full logs to the browser.
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = err[-1] if err else f"exit {proc.returncode}"
    if len(detail) > 200:
        detail = detail[:200] + "…"
    logger.warning("gateway restart failed: rc=%s detail=%s", proc.returncode, detail)
    return False, f"Gateway restart failed: {detail}"


def request_gateway_restart(*, wait: bool = False) -> dict[str, Any]:
    """Start a gateway restart. Fast-return ``in_progress`` unless ``wait``."""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "status": "busy", "message": "Gateway restart already in progress"}
    try:
        if _STATE.get("status") == "in_progress":
            return {"ok": False, "status": "busy", "message": "Gateway restart already in progress"}
        _STATE.update(
            {
                "status": "in_progress",
                "message": "Restarting messaging gateway for active profile…",
                "started_at": time.time(),
                "finished_at": None,
            }
        )
    finally:
        _LOCK.release()

    def _worker():
        ok, message = _run_gateway_restart()
        _set_state(
            status="completed" if ok else "failed",
            message=message,
            finished_at=time.time(),
        )

    if wait:
        _worker()
        return get_restart_status() | {"ok": get_restart_status().get("status") == "completed"}

    threading.Thread(target=_worker, name="gateway-restart", daemon=True).start()
    # Brief pause so same-process callers can poll a non-idle state.
    time.sleep(0.05)
    st = get_restart_status()
    return {"ok": True, "status": st.get("status") or "in_progress", "message": st.get("message") or ""}


def ensure_gateway_restarted_for_agent_update(*, timeout_s: float = 90.0) -> dict[str, Any]:
    """DECIDED #4: agent update must prove messaging gateway restart."""
    result = request_gateway_restart(wait=False)
    if result.get("status") == "busy":
        return {"ok": False, "status": "busy", "message": result.get("message") or "busy"}
    deadline = time.time() + max(5.0, float(timeout_s))
    while time.time() < deadline:
        st = get_restart_status()
        status = st.get("status")
        if status == "completed":
            return {"ok": True, "status": "completed", "message": st.get("message") or "ok"}
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
