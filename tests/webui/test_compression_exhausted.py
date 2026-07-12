"""W9 compression_exhausted WebUI contract — classify, focus, migration helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def streaming(monkeypatch):
    import importlib

    import api.streaming as mod

    return importlib.reload(mod)


def test_suggested_focus_truncates(streaming):
    long = "x" * 600
    assert len(streaming._suggested_focus_from_user_text(long)) == 500
    assert streaming._suggested_focus_from_user_text("  hello  ") == "hello"


def test_classify_flag_wins(streaming):
    c = streaming._classify_provider_error(
        "rate limit exceeded",
        compression_exhausted=True,
    )
    assert c["type"] == "compression_exhausted"


def test_classify_rate_limit_not_exhaustion(streaming):
    c = streaming._classify_provider_error("Rate limit exceeded (429)")
    assert c["type"] == "rate_limit"


def test_classify_quota_before_overflow_phrase(streaming):
    # quota detector should win over overflow phrases when both could match
    c = streaming._classify_provider_error("You exceeded your current quota")
    assert c["type"] == "quota_exhausted"


def test_classify_overflow_phrase_without_flag(streaming):
    c = streaming._classify_provider_error("This model's maximum context length was exceeded")
    assert c["type"] == "compression_exhausted"


def test_exhaustion_payload_shape(streaming):
    payload = streaming._compression_exhausted_apperror_payload(
        msg_text="Please finish the migration plan",
        err_str="Cannot compress further",
        session_id="new-sid",
        old_session_id="old-sid",
        continuation_session_id="new-sid",
    )
    assert payload["type"] == "compression_exhausted"
    assert payload["compression_exhausted"] is True
    assert payload["suggested_focus"] == "Please finish the migration plan"
    assert payload["session_id"] == "new-sid"
    assert payload["continuation_session_id"] == "new-sid"
    assert payload["old_session_id"] == "old-sid"


def test_rotate_webui_session_when_agent_sid_differs(streaming, tmp_path, monkeypatch):
    monkeypatch.setattr(streaming, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(streaming, "SESSIONS", {})
    monkeypatch.setattr(streaming, "SESSION_AGENT_LOCKS", {})
    monkeypatch.setattr(streaming, "LOCK", MagicMock())
    monkeypatch.setattr(streaming, "SESSION_AGENT_LOCKS_LOCK", MagicMock())

    s = SimpleNamespace(
        session_id="old-sid",
        profile=None,
        parent_session_id=None,
        messages=[],
    )
    agent = SimpleNamespace(session_id="new-sid")
    lock = object()

    with patch.object(streaming, "_preserve_pre_compression_snapshot") as preserve:
        with patch("api.config.SESSION_AGENT_CACHE", {}), \
             patch("api.config.SESSION_AGENT_CACHE_LOCK", MagicMock()), \
             patch("api.config.session_agent_cache_key", side_effect=lambda sid, mid: sid):
            origin, cont, rotated = streaming._rotate_webui_session_if_agent_compressed(
                s,
                agent,
                "old-sid",
                agent_lock=lock,
                member_id=None,
                resolved_profile_name="default",
            )

    assert rotated is True
    assert origin == "old-sid"
    assert cont == "new-sid"
    assert s.session_id == "new-sid"
    assert s.parent_session_id == "old-sid"
    assert s.profile == "default"
    preserve.assert_called_once()


def test_rotate_noop_when_same_sid(streaming):
    s = SimpleNamespace(session_id="same", profile=None, parent_session_id=None)
    agent = SimpleNamespace(session_id="same")
    origin, cont, rotated = streaming._rotate_webui_session_if_agent_compressed(
        s, agent, "same", agent_lock=object()
    )
    assert rotated is False
    assert cont is None
    assert origin == "same"
