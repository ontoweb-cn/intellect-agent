"""W5: deferred Activity worklog contract tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def dw():
    import importlib

    import api.deferred_worklog as mod

    return importlib.reload(mod)


def test_n_threshold(dw):
    assert dw.DEFERRED_WORKLOG_N == 8
    assert (
        dw.should_defer_activity_worklog(
            enabled=True, compact_worklog=True, tool_count=7
        )
        is False
    )
    assert (
        dw.should_defer_activity_worklog(
            enabled=True, compact_worklog=True, tool_count=8
        )
        is True
    )


def test_flag_off_never_defers(dw):
    assert (
        dw.should_defer_activity_worklog(
            enabled=False, compact_worklog=True, tool_count=20
        )
        is False
    )


def test_transparent_stream_never_defers(dw):
    assert (
        dw.should_defer_activity_worklog(
            enabled=True, compact_worklog=False, tool_count=20
        )
        is False
    )


def test_live_never_defers(dw):
    assert (
        dw.should_defer_activity_worklog(
            enabled=True, compact_worklog=True, tool_count=20, settled=False
        )
        is False
    )


def test_shell_label(dw):
    label = dw.worklog_shell_label(["a", "b", "c"], 3)
    assert "3 tools" in label
    assert "a" in label


def test_settings_default_off():
    from api.config import _SETTINGS_BOOL_KEYS, _SETTINGS_DEFAULTS

    assert _SETTINGS_DEFAULTS.get("deferred_activity_worklog") is False
    assert "deferred_activity_worklog" in _SETTINGS_BOOL_KEYS
