"""W2 Opt-D / B4 unit coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def wakeup_mod(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    (tmp_path / ".intellect").mkdir(parents=True, exist_ok=True)
    import api.wakeup_pause as mod

    return importlib.reload(mod)


def test_wakeup_pause_blocks_same_fingerprint(wakeup_mod):
    wakeup_mod.set_pause(reason="quota_exhausted", provider="OpenAI", model="gpt-4o")
    blocked = wakeup_mod.is_blocked("openai", "GPT-4o")
    assert blocked is not None
    assert blocked["reason"] == "quota_exhausted"
    # Different model clears / does not block
    assert wakeup_mod.is_blocked("openai", "gpt-4.1") is None


def test_wakeup_pause_clear(wakeup_mod):
    wakeup_mod.set_pause(reason="rate_limit", provider="x", model="y")
    wakeup_mod.clear_pause()
    assert wakeup_mod.read_pause() is None


def test_gateway_lifecycle_busy_and_status(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    import api.gateway_lifecycle as gl

    gl = importlib.reload(gl)

    def _fake_run():
        return True, "ok"

    monkeypatch.setattr(gl, "_run_gateway_restart", _fake_run)
    monkeypatch.setattr(gl, "_run_gateway_cli", lambda action: (True, f"{action} ok"))
    # Reset module state
    with gl._LOCK:
        gl._STATE.update(
            status="idle", operation=None, message="", started_at=None, finished_at=None
        )

    result = gl.request_gateway_restart(wait=True)
    assert result.get("status") == "completed"
    assert result.get("operation") == "restart"
    assert gl.get_restart_status()["status"] == "completed"
    assert gl.get_restart_status()["operation"] == "restart"

    # Force in_progress to exercise busy
    with gl._LOCK:
        gl._STATE["status"] = "in_progress"
        gl._STATE["operation"] = "restart"
    busy = gl.request_gateway_restart(wait=False)
    assert busy.get("status") == "busy"


def test_ensure_gateway_restart_hard_fail(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    import api.gateway_lifecycle as gl

    gl = importlib.reload(gl)

    monkeypatch.setattr(gl, "_run_gateway_cli", lambda action: (False, "boom"))
    with gl._LOCK:
        gl._STATE.update(
            status="idle", operation=None, message="", started_at=None, finished_at=None
        )
    out = gl.ensure_gateway_restarted_for_agent_update(timeout_s=2)
    assert out.get("ok") is False
    assert out.get("status") == "failed"


def test_stop_completed_does_not_prove_restart(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    import api.gateway_lifecycle as gl

    gl = importlib.reload(gl)

    with gl._LOCK:
        gl._STATE.update(
            status="completed",
            operation="stop",
            message="stopped",
            started_at=1.0,
            finished_at=2.0,
        )

    monkeypatch.setattr(
        gl,
        "request_gateway_restart",
        lambda **kw: {"ok": True, "status": "in_progress", "operation": "restart"},
    )
    out = gl.ensure_gateway_restarted_for_agent_update(timeout_s=1.5)
    assert out.get("ok") is False
    assert out.get("status") == "failed"


def test_in_gateway_env_blocks_ops(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    monkeypatch.setenv("_INTELLECT_GATEWAY", "1")
    import api.gateway_lifecycle as gl

    gl = importlib.reload(gl)
    ok, msg = gl._run_gateway_cli("start")
    assert ok is False
    assert "inside" in msg.lower()


def test_wait_cap_keeps_in_progress_not_failed(monkeypatch, tmp_path):
    import importlib
    import threading
    import time

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    import api.gateway_lifecycle as gl

    gl = importlib.reload(gl)

    started = threading.Event()

    def _slow():
        started.set()
        time.sleep(2.0)
        return True, "done"

    monkeypatch.setattr(gl, "_run_gateway_cli", lambda action: _slow())
    with gl._LOCK:
        gl._STATE.update(
            status="idle", operation=None, message="", started_at=None, finished_at=None
        )

    result = gl.request_gateway_restart(wait=True, wait_cap_s=0.3)
    assert result.get("status") == "in_progress"
    assert result.get("timed_out") is True
    assert result.get("ok") is True
    assert gl.get_lifecycle_status()["status"] == "in_progress"
    assert gl.lifecycle_http_status(result) == 200
    started.wait(timeout=1.0)


def test_lifecycle_http_status_busy_only_409():
    import api.gateway_lifecycle as gl

    assert gl.lifecycle_http_status({"status": "busy"}) == 409
    assert gl.lifecycle_http_status({"status": "failed", "ok": False}) == 200
    assert gl.lifecycle_http_status({"status": "completed", "ok": True}) == 200
    assert gl.lifecycle_http_status({"status": "in_progress", "timed_out": True}) == 200


def test_gateway_lifecycle_paths_not_csrf_exempt():
    from api.routes import _csrf_exempt_path

    for path in (
        "/api/gateway/restart",
        "/api/gateway/start",
        "/api/gateway/stop",
        "/api/health/restart",
    ):
        assert not _csrf_exempt_path(path)


def test_l3_stale_active_prefers_fresh_root(monkeypatch, tmp_path):
    """Active home has stale runtime; root has fresh → probe_scope root_fallback."""
    import importlib
    from datetime import datetime, timezone

    root = tmp_path / ".intellect"
    profile = root / "profiles" / "coder"
    profile.mkdir(parents=True)
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INTELLECT_HOME", str(profile))

    import api.agent_health as ah

    ah = importlib.reload(ah)

    fresh = {
        "gateway_state": "running",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    stale = {
        "gateway_state": "running",
        "updated_at": "2020-01-01T00:00:00Z",
    }

    class _GS:
        def get_running_pid(self, pid_path=None, cleanup_stale=False):
            return None

        def read_runtime_status(self, pid_path=None):
            if pid_path is None:
                return None
            p = str(pid_path)
            if "/profiles/coder/" in p or p.endswith(str(profile / "gateway.pid")):
                return stale
            return fresh

    monkeypatch.setattr(ah, "_gateway_status_module", lambda: _GS())
    monkeypatch.setattr(ah, "_gateway_root_pid_path", lambda: root / "gateway.pid")
    monkeypatch.setattr(ah, "_gateway_active_pid_path", lambda: profile / "gateway.pid")

    payload = ah.build_agent_health_payload()
    assert payload.get("alive") is True
    assert payload["details"]["probe_scope"] == "root_fallback"


def test_clear_pause_for_fingerprint_only(wakeup_mod):
    wakeup_mod.set_pause(reason="quota_exhausted", provider="a", model="m1")
    assert wakeup_mod.clear_pause_for_fingerprint("a", "m2") is False
    assert wakeup_mod.read_pause() is not None
    assert wakeup_mod.clear_pause_for_fingerprint("a", "m1") is True
    assert wakeup_mod.read_pause() is None


def test_process_wakeup_paused_event_shape(wakeup_mod):
    paused = wakeup_mod.set_pause(reason="rate_limit", provider="p", model="m")
    ev = wakeup_mod.process_wakeup_paused_event(paused)
    assert ev["v"] == 1
    assert ev["reason"] == "rate_limit"
    assert ev["provider"] == "p"
