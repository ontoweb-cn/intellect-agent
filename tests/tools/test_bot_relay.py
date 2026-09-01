"""Tests for the peer-gateway DM relay (BT-03 / B2-4)."""

import httpx
import json
import pytest
from intellect_state import SessionDB
from types import SimpleNamespace

from tools.bot_mode_dm import BOT_CHAT_SESSION_TITLE
from tools import bot_relay

PEER = {
    "url": "https://peer.example",
    "api_key": "peer-secret-key",
    "profile": "beta",
}


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None,
                response=httpx.Response(self.status_code),
            )



@pytest.fixture
def sender_home(tmp_path):
    home = tmp_path / "sender"
    home.mkdir()
    return home


@pytest.fixture
def fake_client(monkeypatch):
    """Replace httpx.Client with a recorder; returns (requests, status_fn)."""
    requests = []

    class _Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}", request=None,
                    response=httpx.Response(self.status_code),
                )

    class _Client:
        def __init__(self, **kwargs):
            requests.append(("__init__", kwargs))
            _Client.last_kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            requests.append(("GET", url, headers))
            status = _Client.status_map.get(url, 200)
            return _Resp(status, {})

        def post(self, url, json=None, headers=None):
            requests.append(("POST", url, headers, json))
            status = _Client.status_map.get(url, 200)
            if status >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {status}", request=None,
                    response=httpx.Response(status),
                )
            return _Resp(status, {"message": {"role": "assistant",
                                              "content": "peer reply"}})

    _Client.requests = requests
    _Client.status_map = {}
    monkeypatch.setattr(httpx, "Client", _Client)
    return _Client.status_map


# ── resolution ──────────────────────────────────────────────────────────

def test_resolve_peer(monkeypatch):
    monkeypatch.setattr(
        bot_relay, "peers_config",
        lambda: {"beta": {"url": "https://p", "api_key": "k"}},
    )
    assert bot_relay.resolve_peer("Beta") == (
        "beta", {"url": "https://p", "api_key": "k"})
    assert bot_relay.resolve_peer("ghost") is None


def test_peers_config_default_empty(monkeypatch):
    import intellect_cli.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda: {"bot_mode": {}})
    assert bot_relay.peers_config() == {}


def test_peer_api_key_env_indirection(monkeypatch):
    monkeypatch.setenv("MY_PEER_KEY", "env-key-1")
    assert bot_relay._peer_api_key({"api_key_env": "MY_PEER_KEY"}) == "env-key-1"
    assert bot_relay._peer_api_key({"api_key": "direct"}) == "direct"
    assert bot_relay._peer_api_key({}) == ""


# ── relay delivery ──────────────────────────────────────────────────────

def test_relay_delivery_full_roundtrip(tmp_path, monkeypatch, sender_home):
    """Probe → create → chat (Bearer + attribution) → reply written back
    to the sender's Bot Chat session."""
    from tools.bot_relay import relay_delivery

    calls = []

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            calls.append(("GET", url, headers))
            return _Resp(404, {})  # session absent → create

        def post(self, url, json=None, headers=None):
            calls.append(("POST", url, headers, json))
            if url.endswith("/api/sessions"):
                return _Resp(201, {"object": "intellect.session"})
            return _Resp(200, {"message": {"role": "assistant",
                                           "content": "peer says hi"}})

    monkeypatch.setattr(httpx, "Client", _Client)

    out = relay_delivery("beta", PEER, "alpha", "status report",
                         sender_home, timeout=30)
    assert out["delivered"] is True
    assert out["reply"] == "peer says hi"

    gets = [c for c in calls if c[0] == "GET"]
    posts = [c for c in calls if c[0] == "POST"]
    # /p/beta prefix on every request
    assert all("/p/beta/" in c[1] for c in gets + posts)
    # create with the deterministic id + title
    assert any(c[1].endswith("/api/sessions")
               and c[3] == {"id": "bot_chat", "title": BOT_CHAT_SESSION_TITLE}
               for c in posts)
    # chat with Bearer + attributed body
    chat = [c for c in posts if c[1].endswith("/chat")][0]
    assert chat[2]["Authorization"] == "Bearer peer-secret-key"
    assert chat[3]["message"].startswith(
        "Message from 🤖 alpha (@alpha): status report")

    # reply written back into the sender's Bot Chat session
    db = SessionDB(db_path=sender_home / "state.db")
    msgs = db.get_messages("bot_chat")
    assert any("Reply from 🤖 beta: peer says hi" in m["content"]
               for m in msgs)


def test_relay_unreachable_writes_visible_error(
    tmp_path, monkeypatch, sender_home
):
    from tools.bot_relay import relay_delivery

    class _RefusedClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

        def post(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "Client", _RefusedClient)

    out = relay_delivery("beta", PEER, "alpha", "hi", sender_home)
    assert out["delivered"] is False
    assert "unreachable" in out["outcome"]

    from intellect_state import SessionDB

    db = SessionDB(db_path=sender_home / "state.db")
    msgs = db.get_messages("bot_chat")
    assert any("[relay] delivery to beta" in m["content"]
               for m in msgs)


# ── review findings ─────────────────────────────────────────────────────

def test_local_roster_shadows_peer(tmp_path, monkeypatch, sender_home):
    """A name present in BOTH the local roster and bot_mode.peers delivers
    LOCALLY — same-machine is canonical (review P3)."""
    import tools.bot_mode_dm as bdm
    from tools.process_registry import process_registry

    home = tmp_path / "beta"
    home.mkdir()

    class _Agent:
        _session_db = SimpleNamespace(db_path=home / "state.db")
        session_id = "bot_chat"

    monkeypatch.setattr(bdm, "build_roster", lambda: [
        {"name": "beta", "home": str(home), "online": True,
         "model": "", "avatar": {}}])
    monkeypatch.setattr(bdm, "bot_mode_enabled", lambda: True)
    monkeypatch.setattr(bdm, "_read_session_title", lambda agent: "Bot Chat")
    monkeypatch.setattr(bdm, "current_dm_depth", lambda: 0)
    monkeypatch.setattr(bdm, "max_dm_depth", lambda: 3)
    monkeypatch.setattr(bdm, "_session_home", lambda agent: home)
    # resolve_peer must never be consulted for a roster-shadowed name
    def _forbidden(target):
        raise AssertionError("resolve_peer consulted for a local target")
    monkeypatch.setattr(bdm, "resolve_peer", _forbidden)

    # local-delivery machinery, mocked to no-op recorders
    monkeypatch.setattr(bdm, "ensure_bot_chat_session", lambda h: "bot_chat")

    written = {}
    monkeypatch.setattr(bdm, "write_dm_query_file",
                        lambda text: written.update(text=text) or
                        (home / "q.txt"))

    spawned = []
    monkeypatch.setattr(process_registry, "spawn_local",
                        lambda **kw: spawned.append(kw) or
                        SimpleNamespace(id="p1", pid=1,
                                        notify_on_complete=False))

    agent = _Agent()
    out = json.loads(bdm.handle_message_agent_call(
        agent, {"target": "beta", "message": "hello"}))

    assert out["delivered"] is True
    assert "relayed" not in out  # LOCAL delivery won
    assert spawned  # the local background chat was spawned


def test_relay_timeout_path_writes_error(tmp_path, monkeypatch, sender_home):
    """The peer's synchronous turn exceeding the timeout lands as a visible
    error entry (TimeoutException branch, review note)."""
    from tools.bot_relay import relay_delivery

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return _Resp(200, {})

        def post(self, *a, **kw):
            raise httpx.TimeoutException("peer turn stalled")

    monkeypatch.setattr(httpx, "Client", _Client)

    out = relay_delivery("beta", PEER, "alpha", "hi", sender_home, timeout=30)
    assert out["delivered"] is False
    assert "timeout" in out["outcome"]

    from intellect_state import SessionDB

    db = SessionDB(db_path=sender_home / "state.db")
    msgs = db.get_messages("bot_chat")
    assert any("[relay]" in m["content"] for m in msgs)


class _Resp200:
    status_code = 200

    def json(self):
        return {}

    def raise_for_status(self):
        return None
