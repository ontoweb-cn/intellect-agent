"""B2-4 E2E: relay transport against a real gateway child.

Env-gated real integration test — run explicitly with (the run_tests.sh
wrapper scrubs the environment via env -i, so invoke pytest directly):

    source .venv/bin/activate
    INTELLECT_BOT_RELAY_E2E=1 python -m pytest \\
        tests/gateway/test_bot_relay_e2e.py -q

Starts a REAL gateway child for profile beta (api_server enabled, no LLM
provider needed) and verifies the relay transport contract:

1. control socket becomes ready (gateway started);
2. a correctly-keyed POST to ``/api/sessions`` creates the Bot Chat session;
3. a correctly-keyed POST to ``/api/sessions/{id}/chat`` is ACCEPTED by
   the api_server (proof the relay transport reaches the peer, regardless
   of whether the peer's LLM turn succeeds);
4. a wrong-key POST is rejected 401 (cross-key isolation).

No external services, no fixed ports. Transport-only — LLM turn success
requires real provider credentials and is out of scope for this test.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("INTELLECT_BOT_RELAY_E2E") != "1",
        reason="real-process bot relay E2E — set INTELLECT_BOT_RELAY_E2E=1",
    ),
    pytest.mark.timeout(180),
]

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _poll(fn, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return None


def test_relay_transport_to_real_gateway(tmp_path):
    key = "e2e-beta-key-0123456789abcdef"
    port = _free_port()

    root = Path("/tmp") / f"intellect-relay-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    shutil.rmtree(root, ignore_errors=True)
    beta_home = root / ".intellect"
    beta_home.mkdir(parents=True)

    (beta_home / "config.yaml").write_text(
        "platforms:\n"
        "  api_server:\n"
        "    enabled: true\n"
        "    extra:\n"
        "      host: 127.0.0.1\n"
        f"      port: {port}\n"
        f"      key: {key}\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["INTELLECT_HOME"] = str(beta_home)
    log = open(root / "gateway.log", "ab")
    gateway = subprocess.Popen(
        [sys.executable, "-m", "gateway.run"],
        cwd=str(REPO), env=env,
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        # ── gateway ready (control socket answers) ────────────────────
        from gateway.control_socket import query_control_socket

        ready = _poll(
            lambda: query_control_socket(
                "identify", timeout=2.0,
                path=beta_home / "gateway.control.sock",
            ),
            timeout=60,
        )
        assert ready, "beta gateway never became ready"

        import urllib.request
        import urllib.error

        def _post(path, body, auth_key=None):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}")
            req.add_header("Content-Type", "application/json")
            if auth_key:
                req.add_header("Authorization", f"Bearer {auth_key}")
            data = json.dumps(body).encode("utf-8")
            try:
                with urllib.request.urlopen(req, data=data, timeout=15) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", "replace")
                try:
                    return exc.code, json.loads(body_text)
                except ValueError:
                    return exc.code, {}
            except (urllib.error.URLError, ConnectionError, OSError):
                return None, {}

        # ── session create (the relay's ensure step) ──────────────────
        status, body = _post(
            "/api/sessions",
            {"id": "bot_chat", "title": "Bot Chat"},
            auth_key=key,
        )
        assert status in (200, 201), (
            f"session create failed: {status} {body}")

        # ── relay chat POST is accepted by the api_server ─────────────
        status, body = _post(
            "/api/sessions/bot_chat/chat",
            {"message": "Message from 🤖 alpha (@alpha): hello"},
            auth_key=key,
        )
        # The relay POST reached the peer's api_server — the transport
        # works even if the peer's LLM turn fails (no provider configured).
        assert status is not None and status != 401, (
            f"relay chat POST transport failed: {status} {body}")

        # ── cross-key isolation: wrong key → 401 ──────────────────────
        status, _ = _post(
            "/api/sessions/bot_chat/chat",
            {"message": "sneak"},
            auth_key="wrong-key-0123456789abcdef",
        )
        assert status == 401, (
            f"wrong-key POST must be rejected (got {status})")
    finally:
        if gateway.poll() is None:
            try:
                os.killpg(os.getpgid(gateway.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                gateway.terminate()
        deadline = time.time() + 20
        while time.time() < deadline and gateway.poll() is None:
            time.sleep(0.5)
        if gateway.poll() is None:
            gateway.kill()
        shutil.rmtree(root, ignore_errors=True)
