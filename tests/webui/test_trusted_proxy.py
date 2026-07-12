"""W7 trusted-proxy-first — loopback default; forged XFF ignored from remote peers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from api import trusted_proxy as tp  # noqa: E402


class _H:
    def __init__(self, peer: str, headers: dict | None = None):
        self.client_address = (peer, 12345)
        self.headers = headers or {}
        self.request = SimpleNamespace()  # no getpeercert


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    tp.clear_trusted_proxy_cache()
    yield
    tp.clear_trusted_proxy_cache()
    monkeypatch.delenv("INTELLECT_WEBUI_TRUSTED_PROXIES", raising=False)


def test_unset_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("INTELLECT_WEBUI_TRUSTED_PROXIES", raising=False)
    tp.clear_trusted_proxy_cache()
    assert tp.is_trusted_proxy(_H("127.0.0.1"))
    assert not tp.is_trusted_proxy(_H("8.8.8.8"))


def test_explicit_none_trusts_nothing(monkeypatch):
    monkeypatch.setenv("INTELLECT_WEBUI_TRUSTED_PROXIES", "none")
    tp.clear_trusted_proxy_cache()
    assert not tp.is_trusted_proxy(_H("127.0.0.1"))
    assert tp.client_ip(_H("127.0.0.1", {"X-Forwarded-For": "10.0.0.1"})) == "127.0.0.1"


def test_remote_peer_forged_xff_ignored(monkeypatch):
    monkeypatch.delenv("INTELLECT_WEBUI_TRUSTED_PROXIES", raising=False)
    tp.clear_trusted_proxy_cache()
    h = _H("203.0.113.9", {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "10.0.0.2"})
    assert tp.client_ip(h) == "203.0.113.9"
    assert tp.request_scheme(h) == "http"  # forged Proto ignored
    h2 = _H("203.0.113.9", {"X-Forwarded-Proto": "https"})
    assert tp.request_scheme(h2) == "http"


def test_loopback_peer_honors_forwarded(monkeypatch):
    monkeypatch.delenv("INTELLECT_WEBUI_TRUSTED_PROXIES", raising=False)
    tp.clear_trusted_proxy_cache()
    h = _H(
        "127.0.0.1",
        {
            "X-Forwarded-For": "203.0.113.50",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "app.example.com",
            "Host": "localhost:9119",
        },
    )
    assert tp.client_ip(h) == "203.0.113.50"
    assert tp.request_scheme(h) == "https"
    assert tp.request_host(h) == "app.example.com"


def test_x_real_ip_when_trusted(monkeypatch):
    monkeypatch.delenv("INTELLECT_WEBUI_TRUSTED_PROXIES", raising=False)
    tp.clear_trusted_proxy_cache()
    h = _H("127.0.0.1", {"X-Real-IP": "198.51.100.7"})
    assert tp.client_ip(h) == "198.51.100.7"
