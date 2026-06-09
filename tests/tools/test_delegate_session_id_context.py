"""delegate_task must not leave parent intellect_SESSION_ID polluted."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from gateway.session_context import get_session_env, set_current_session_id
from tools.delegate_tool import (
    _build_child_agent,
    _parent_session_id_for_delegate,
    _restore_parent_session_context,
    _run_single_child,
)


@pytest.fixture(autouse=True)
def _clean_session_env():
    os.environ.pop("intellect_SESSION_ID", None)
    yield
    os.environ.pop("intellect_SESSION_ID", None)


def test_parent_session_id_for_delegate_prefers_agent_attribute():
    parent = MagicMock(session_id="parent-abc")
    assert _parent_session_id_for_delegate(parent) == "parent-abc"


def test_build_child_agent_does_not_publish_session_on_main_thread():
    set_current_session_id("parent-main")
    parent = MagicMock(
        session_id="parent-main",
        platform="cli",
        model="test/model",
        base_url="https://example.com/v1",
        api_key="k",
        provider="openrouter",
        enabled_toolsets=["terminal"],
        valid_tool_names=["terminal"],
        _session_db=None,
        _delegate_depth=0,
        _print_fn=None,
        prefill_messages=None,
        max_tokens=None,
        reasoning_config=None,
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        openrouter_min_coding_score=None,
    )
    with patch("run_agent.AIAgent") as mock_agent_cls:
        mock_child = MagicMock(session_id="child-xyz")
        mock_agent_cls.return_value = mock_child
        child = _build_child_agent(
            task_index=0,
            goal="do work",
            context=None,
            toolsets=None,
            model="test/model",
            max_iterations=5,
            task_count=1,
            parent_agent=parent,
        )
        assert child is mock_child
        mock_agent_cls.assert_called_once()
        assert mock_agent_cls.call_args.kwargs.get("publish_session_context") is False
    assert os.environ.get("intellect_SESSION_ID") == "parent-main"
    assert get_session_env("intellect_SESSION_ID") == "parent-main"


def test_run_single_child_restores_parent_session_in_finally():
    set_current_session_id("parent-aaa")
    parent = MagicMock(session_id="parent-aaa")
    child = MagicMock(session_id="child-bbb")
    child.run_conversation.return_value = {
        "final_response": "done",
        "completed": True,
        "interrupted": False,
        "api_calls": 1,
        "messages": [],
    }
    child.get_activity_summary.return_value = {
        "current_tool": None,
        "api_call_count": 1,
        "max_iterations": 5,
        "last_activity_desc": "",
    }
    child.close = MagicMock()
    child._delegate_saved_tool_names = []
    child._subagent_id = "sa-0-test"
    child._delegate_depth = 1
    child._delegate_role = "leaf"
    child._credential_pool = None

    with patch("tools.delegate_tool._get_child_timeout", return_value=600):
        with patch("tools.delegate_tool.file_state"):
            _run_single_child(0, "goal", child=child, parent_agent=parent)

    assert os.environ.get("intellect_SESSION_ID") == "parent-aaa"
    assert get_session_env("intellect_SESSION_ID") == "parent-aaa"
    child.run_conversation.assert_called_once()


def test_restore_parent_session_context_noop_when_empty():
    set_current_session_id("before")
    _restore_parent_session_context(None)
    assert os.environ.get("intellect_SESSION_ID") == "before"
