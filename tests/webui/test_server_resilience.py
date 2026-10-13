"""Tests for webui/server.py resilience fixes (2026-10-12 outage follow-up).

Covers:
- fix 1: access-log emission must never raise into the response path, even
  when sys.stdout is closed/replaced (the outage's kill chain);
- fix 2: the durable access log is a FileHandler under INTELLECT_HOME/logs/,
  independent of stdout;
- fix 4: the liveness watchdog probes its own /health over real HTTP.
"""

from __future__ import annotations

import http.client
import importlib
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def server_mod(tmp_path, monkeypatch):
    """Import webui/server.py with an isolated INTELLECT_HOME."""
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    (tmp_path / ".intellect").mkdir(parents=True, exist_ok=True)
    import server  # noqa: PLC0415 — webui dir on sys.path above

    mod = importlib.reload(server)
    # Reset access-log module state so each test attaches a fresh FileHandler
    # pointing at *this* test's INTELLECT_HOME.
    mod._access_log_ready = False
    mod._access_logger.handlers.clear()
    yield mod
    mod._access_logger.handlers.clear()
    mod._access_log_ready = False


def _access_log_path() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home() / "logs" / "webui-access.log"


class TestEmitAccessRecord:
    def test_writes_durable_file(self, server_mod):
        server_mod.emit_access_record('{"ts": "t", "path": "/x"}')
        path = _access_log_path()
        assert path.exists()
        assert '{"ts": "t", "path": "/x"}' in path.read_text(encoding="utf-8")

    def test_closed_stdout_does_not_raise(self, server_mod, monkeypatch):
        """THE regression: with the old bare print(), a closed stdout raised
        ValueError out of log_request into every response."""
        dead = io.StringIO()
        dead.close()
        monkeypatch.setattr(sys, "stdout", dead)

        # Must not raise even though both the stdout mirror is dead.
        server_mod.emit_access_record('{"ts": "t", "path": "/y"}')

        assert '{"ts": "t", "path": "/y"}' in _access_log_path().read_text(
            encoding="utf-8"
        )

    def test_raising_file_handler_does_not_raise(self, server_mod, monkeypatch):
        # Even if the logging sink explodes, emission stays quiet.
        def _boom(record):
            raise RuntimeError("disk on fire")

        server_mod._access_logger.handlers.clear()
        monkeypatch.setattr(server_mod._access_logger, "handle", _boom)
        monkeypatch.setattr(sys, "stdout", io.StringIO())  # quiet mirror target

        server_mod.emit_access_record('{"ts": "t"}')  # must not raise


class TestLiveServerAccessLog:
    @pytest.fixture
    def live_server(self, server_mod):
        httpd = server_mod.QuietHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield port
        httpd.shutdown()
        httpd.server_close()

    def test_health_ok_and_access_record_lands_in_file(self, live_server):
        port = live_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200

        deadline = time.time() + 5
        content = ""
        while time.time() < deadline:
            path = _access_log_path()
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if '"path": "/health"' in content:
                    break
            time.sleep(0.05)
        assert '"path": "/health"' in content
        record = json.loads(content.strip().splitlines()[-1])
        assert record["status"] == 200
        assert record["method"] == "GET"


class TestWatchdog:
    def test_probe_target_maps_wildcard_hosts(self, server_mod, monkeypatch):
        import api.config as api_config

        monkeypatch.setattr(server_mod, "HOST", "0.0.0.0")
        monkeypatch.setattr(server_mod, "PORT", 9119)
        monkeypatch.setattr(api_config, "TLS_ENABLED", False)
        assert server_mod._watchdog_probe_target() == ("http", "127.0.0.1", 9119)

        monkeypatch.setattr(server_mod, "HOST", "192.168.1.10")
        monkeypatch.setattr(api_config, "TLS_ENABLED", True)
        scheme, host, port = server_mod._watchdog_probe_target()
        assert (scheme, host, port) == ("https", "192.168.1.10", 9119)

    def test_probe_true_against_live_server(self, server_mod, live_server_port):
        assert live_server_port > 0
        assert server_mod._probe_health_once(5.0) is True

    def test_probe_false_against_dead_port(self, server_mod, monkeypatch):
        # Bind then close: the port is (almost certainly) free, so connect fails.
        import socket as socket_mod

        s = socket_mod.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        monkeypatch.setattr(server_mod, "HOST", "127.0.0.1")
        monkeypatch.setattr(server_mod, "PORT", port)
        assert server_mod._probe_health_once(1.0) is False

    def test_watchdog_disabled_by_env(self, server_mod, monkeypatch):
        monkeypatch.setenv("INTELLECT_WEBUI_WATCHDOG", "0")
        before = {t.name for t in threading.enumerate()}
        server_mod.start_liveness_watchdog()
        time.sleep(0.1)
        after = {t.name for t in threading.enumerate()}
        assert "webui-liveness-watchdog" not in (after - before)


@pytest.fixture
def live_server_port(server_mod):
    """A live QuietHTTPServer on an ephemeral port, with HOST/PORT patched so
    the watchdog's probe target points at it."""
    import api.config as api_config

    httpd = server_mod.QuietHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    old_host, old_port = server_mod.HOST, server_mod.PORT
    old_tls = api_config.TLS_ENABLED
    server_mod.HOST = "127.0.0.1"
    server_mod.PORT = port
    api_config.TLS_ENABLED = False
    yield port
    server_mod.HOST, server_mod.PORT = old_host, old_port
    api_config.TLS_ENABLED = old_tls
    httpd.shutdown()
    httpd.server_close()
