"""门-4 E2E: Bot Mode cross-profile DM over the REAL transport.

Env-gated real integration test — run explicitly with (the run_tests.sh
wrapper scrubs the environment, so invoke pytest directly):

    source .venv/bin/activate
    INTELLECT_BOT_MODE_E2E=1 python -m pytest \\
        tests/gateway/test_bot_mode_e2e.py -q

Builds a root home (Bot Mode enabled) + an alpha profile, then asserts the
gate-4 clauses that need real processes:

1. roster liveness — alpha reads OFFLINE until a real gateway child for it
   is up, then ONLINE (control-socket probe);
2. cross-profile DM — the real dispatch spawns a real background
   ``intellect chat -Q --continue "Bot Chat" --query-file`` child; the
   attributed message lands in alpha's Bot Chat session even though the
   child's LLM call fails (unreachable provider) — persistence happens on
   every exit path, so the fire-and-forget transport is verified without
   needing live model credentials;
3. non-Bot-Chat sessions cannot deliver (structured forged-call error
   under the real config gate).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("INTELLECT_BOT_MODE_E2E") != "1",
        reason="real-process Bot Mode E2E — set INTELLECT_BOT_MODE_E2E=1",
    ),
    pytest.mark.timeout(240),
]


def _wait_for(fn, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return None


def _make_agent(home: Path, session_id: str):
    """Minimal agent double bound to a REAL SessionDB at `home` — enough for
    the dispatch title-gate to exercise real persistence."""
    from intellect_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    return SimpleNamespace(_session_db=db, session_id=session_id)


def _alpha_session_has_message(alpha_home: Path, needle: str) -> bool:
    from intellect_state import SessionDB

    db = SessionDB(db_path=alpha_home / "state.db")
    try:
        sid = db.resolve_session_by_title("Bot Chat")
        if not sid:
            return False
        for msg in db.get_messages(sid):
            content = msg.get("content") or ""
            if isinstance(content, str) and needle in content:
                return True
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


def test_bot_mode_dm_transport_and_roster(tmp_path, monkeypatch):
    from gateway.control_socket import query_control_socket
    from tools import bot_mode_dm as bdm
    from tools.bot_mode_roster import build_roster

    # SHORT home path — control sockets are AF_UNIX (macOS ~104-byte cap).
    home_root = Path("/tmp") / f"intellect-bt-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    shutil.rmtree(home_root, ignore_errors=True)
    root_home = home_root / ".intellect"
    alpha_home = root_home / "profiles" / "alpha"
    alpha_home.mkdir(parents=True)

    monkeypatch.setenv("INTELLECT_HOME", str(root_home))
    monkeypatch.setenv("HOME", str(home_root))
    # Bot Mode config (real config gate) for the root/sender profile.
    (root_home / "config.yaml").write_text(
        "bot_mode:\n  enabled: true\n  max_dm_depth: 3\n", encoding="utf-8"
    )
    # The spawned target chat needs *a* provider to pass the first-run
    # guard — point it at an unreachable port: its LLM call fails, but the
    # attributed user message is persisted on the exit path regardless.
    # Written to the ALPHA profile's own .env (its HOME is a throwaway,
    # so ambient ~/.intellect/.env credentials would not be found).
    (alpha_home / ".env").write_text(
        "OPENAI_API_KEY=e2e-not-a-real-key\n"
        "OPENAI_BASE_URL=http://127.0.0.1:9/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "e2e-not-a-real-key")

    gateway = None
    try:
        # ── clause: roster OFFLINE → ONLINE with a real gateway child ───
        roster = {e["name"]: e for e in build_roster()}
        assert roster["alpha"]["online"] is False  # no gateway yet

        env = dict(os.environ)
        env["INTELLECT_HOME"] = str(alpha_home)
        log = open(home_root / "gateway-alpha.log", "ab")
        gateway = subprocess.Popen(
            [sys.executable, "-m", "gateway.run"],
            cwd=str(REPO),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert _wait_for(
            lambda: bool(
                query_control_socket(
                    "identify",
                    timeout=1.0,
                    path=alpha_home / "gateway.control.sock",
                )
            ),
            timeout=90,
        ), "alpha gateway never became reachable"
        roster = {e["name"]: e for e in build_roster()}
        assert roster["alpha"]["online"] is True

        # ── clause: non-Bot-Chat session cannot deliver (structured) ────
        agent_normal = _make_agent(root_home, session_id="not_a_bot_session")
        result = json.loads(bdm.handle_message_agent_call(
            agent_normal, {"target": "alpha", "message": "sneak"}))
        assert result["code"] == "not_bot_chat_session"

        # ── clause: cross-profile DM over the REAL transport ────────────
        from tools.bot_mode_dm import ensure_bot_chat_session

        sender_session = ensure_bot_chat_session(root_home)
        agent_bot = _make_agent(root_home, session_id=sender_session)
        result = json.loads(bdm.handle_message_agent_call(
            agent_bot,
            {"target": "alpha", "message": "hello from A"},
        ))
        assert result["delivered"] is True, result

        found = _wait_for(
            lambda: _alpha_session_has_message(
                alpha_home,
                "Message from 🤖 default (@default): hello from A",
            ),
            timeout=120,
        )
        assert found, (
            "attributed DM never reached alpha's Bot Chat session — "
            "alpha log tail:\n"
            + (home_root / "gateway-alpha.log").read_text(errors="replace")[-1500:]
        )
    finally:
        if gateway is not None and gateway.poll() is None:
            try:
                os.killpg(os.getpgid(gateway.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                gateway.terminate()
        shutil.rmtree(home_root, ignore_errors=True)
