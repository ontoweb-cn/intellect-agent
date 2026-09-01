"""Tests for the message_agent tool (BT-02 / B2-2): injection gate,
double title-gate dispatch, attribution, depth budget."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import bot_mode_dm as bdm
from tools.bot_mode_roster import BOT_CHAT_SESSION_TITLE


def _make_agent(tmp_path, *, title=BOT_CHAT_SESSION_TITLE, enabled=True):
    """An agent stub bound to a session-db stub whose title can be flipped
    at dispatch time (the double title-gate re-reads it)."""
    state = {"title": title}
    db = SimpleNamespace(
        db_path=tmp_path / "state.db",
        get_session_title=lambda sid: state["title"],
    )
    agent = SimpleNamespace(
        _session_db=db,
        session_id="sess-1",
        tools=[{"type": "function", "function": {"name": "terminal"}}],
        valid_tool_names={"terminal"},
    )
    return agent, state


@pytest.fixture
def bot_env(tmp_path, monkeypatch):
    """Config on, roster with default+alpha+beta, delivery captured."""
    monkeypatch.setattr(bdm, "bot_mode_enabled", lambda: True)
    monkeypatch.setattr(bdm, "max_dm_depth", lambda: 3)
    monkeypatch.setattr(bdm, "current_dm_depth", lambda: 0)

    roster = [
        {"name": "default", "home": str(tmp_path), "online": True, "model": ""},
        {"name": "alpha", "home": str(tmp_path / "alpha"), "online": True, "model": ""},
        {"name": "beta", "home": str(tmp_path / "beta"), "online": False, "model": ""},
    ]
    monkeypatch.setattr(bdm, "build_roster", lambda: roster)
    monkeypatch.setattr(
        bdm, "ensure_bot_chat_session",
        lambda home: str(calls.setdefault("ensured", home)),
    )
    written = {}

    def _fake_write(message):
        path = tmp_path / "query.txt"
        path.write_text(message, encoding="utf-8")
        written["path"] = path
        written["body"] = message
        return path

    monkeypatch.setattr(bdm, "write_dm_query_file", _fake_write)

    spawned = {}

    class _Session:
        id = "proc_1"
        pid = 4242
        notify_on_complete = False

    def _fake_spawn(command, cwd=None, task_id="", session_key="", env_vars=None, use_pty=False):
        spawned["command"] = command
        spawned["cwd"] = cwd
        spawned["env"] = env_vars
        spawned["session_key"] = session_key
        session = _Session()
        spawned["session"] = session
        return session

    import tools.process_registry as _pr

    monkeypatch.setattr(_pr.process_registry, "spawn_local", _fake_spawn)
    calls = {}
    return SimpleNamespace(state=None, roster=roster, written=written,
                           spawned=spawned, calls=calls)


# ── injection gate ──────────────────────────────────────────────────────

def test_injection_only_into_bot_chat_sessions(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path, title=BOT_CHAT_SESSION_TITLE)
    assert bdm.ensure_message_agent_tool(agent) is True
    names = {t["function"]["name"] for t in agent.tools}
    assert "message_agent" in names
    assert "message_agent" in agent.valid_tool_names

    # Non-Bot-Chat session: never injected (gate-4 clause 1).
    other, _ = _make_agent(tmp_path, title="normal chat")
    assert bdm.ensure_message_agent_tool(other) is False
    assert "message_agent" not in other.valid_tool_names


def test_injection_disabled_without_config(tmp_path, bot_env, monkeypatch):
    monkeypatch.setattr(bdm, "bot_mode_enabled", lambda: False)
    agent, _ = _make_agent(tmp_path)
    assert bdm.ensure_message_agent_tool(agent) is False
    assert len(agent.tools) == 1  # untouched


def test_injection_is_idempotent(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path)
    assert bdm.ensure_message_agent_tool(agent) is True
    count = sum(
        1 for t in agent.tools
        if t["function"]["name"] == "message_agent"
    )
    assert bdm.ensure_message_agent_tool(agent) is True
    count_after = sum(
        1 for t in agent.tools
        if t["function"]["name"] == "message_agent"
    )
    assert count == count_after == 1


# ── dispatch double title-gate ──────────────────────────────────────────

def test_forged_call_gets_structured_error(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path, title="just a normal chat")
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "hi"}))
    assert result["code"] == "not_bot_chat_session"
    assert "delivered" not in result
    assert "ensured" not in bot_env.calls  # nothing was delivered


def test_title_flip_between_inject_and_dispatch_blocks_delivery(tmp_path, bot_env):
    agent, state = _make_agent(tmp_path)  # Bot Chat at injection time
    assert bdm.ensure_message_agent_tool(agent) is True
    # User retitles the session before the call lands.
    state["title"] = "renamed"
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "hi"}))
    assert result["code"] == "not_bot_chat_session"


def test_disabled_config_blocks_dispatch_even_in_bot_chat(tmp_path, bot_env, monkeypatch):
    agent, _ = _make_agent(tmp_path)
    monkeypatch.setattr(bdm, "bot_mode_enabled", lambda: False)
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "hi"}))
    assert result["code"] == "not_bot_chat_session"


# ── delivery semantics ──────────────────────────────────────────────────

def test_delivery_attribution_and_transport(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path)
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "status report"}))
    assert result["delivered"] is True

    body = bot_env.written["body"]
    assert body.startswith("Message from 🤖 default (@default): ")
    assert "status report" in body

    command = bot_env.spawned["command"]
    # Delivery runs through the turn-lock wrapper (P2-1), from the TARGET's
    # home (P2-2), with every variable part shell-quoted.
    assert "-m tools.bot_mode_dm" in command
    assert "--turn-lock" in command
    assert "-p alpha" in command
    assert "--continue" in command and "'Bot Chat'" in command
    assert "--query-file" in command
    assert "status report" not in command  # body never in argv
    assert bot_env.spawned["env"]["INTELLECT_BOT_DM_DEPTH"] == "1"
    assert bot_env.spawned["env"]["INTELLECT_QUERY_FILE_DELETE"] == "1"
    assert bot_env.spawned["session"].notify_on_complete is True
    assert bot_env.spawned["cwd"] == str(tmp_path / "alpha")  # P2-2: target home
    assert bot_env.spawned["env"]["PYTHONPATH"].split(os.pathsep)[0] == (
        str(Path(__file__).resolve().parents[2])
    )


def test_unknown_target_rejected(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path)
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "ghost", "message": "hi"}))
    assert result["code"] == "unknown_target"
    assert "ghost" not in bot_env.spawned


def test_depth_budget_exhausted(tmp_path, bot_env, monkeypatch):
    monkeypatch.setattr(bdm, "current_dm_depth", lambda: 3)
    agent, _ = _make_agent(tmp_path)
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "hi"}))
    assert result["code"] == "depth_exhausted"
    assert "ensured" not in bot_env.calls


def test_message_size_cap(tmp_path, bot_env):
    agent, _ = _make_agent(tmp_path)
    result = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "alpha", "message": "x" * 16001}))
    assert result["code"] == "message_too_long"
