"""W4: variable-height transcript virtual window contract tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def tvw():
    import importlib

    import api.transcript_virtual_window as mod

    return importlib.reload(mod)


def test_below_threshold_not_virtualized(tvw):
    heights = [80.0] * 40
    win = tvw.variable_height_virtual_window(heights, scroll_top=0, viewport_height=600)
    assert win["virtualized"] is False
    assert win["start"] == 0
    assert win["end"] == 40
    assert win["top_pad"] == 0
    assert win["bottom_pad"] == 0


def test_variable_pads_use_prefix_sums_not_fixed_row(tvw):
    # Varying heights — pad must equal sum of skipped rows, not count*52.
    heights = [100.0] * 50 + [200.0] * 50 + [50.0] * 50  # 150 rows > 80
    win = tvw.variable_height_virtual_window(
        heights, scroll_top=0, viewport_height=400, buffer_px=100, threshold=80
    )
    assert win["virtualized"] is True
    assert win["start"] == 0
    assert win["top_pad"] == 0.0
    # Bottom pad = total - visible prefix
    visible = sum(heights[win["start"] : win["end"]])
    assert abs(win["bottom_pad"] - (sum(heights) - visible)) < 1e-6
    # Not equal to (total-end)*52
    assert win["bottom_pad"] != (win["total"] - win["end"]) * 52


def test_scroll_mid_window(tvw):
    heights = [100.0] * 200
    # Scroll deep into the list
    win = tvw.variable_height_virtual_window(
        heights, scroll_top=5000, viewport_height=600, buffer_px=300, threshold=80
    )
    assert win["virtualized"] is True
    assert win["start"] > 0
    assert win["end"] < 200
    assert win["top_pad"] == sum(heights[: win["start"]])
    assert win["end"] - win["start"] < 200


def test_pin_index_brings_target_into_window(tvw):
    heights = [80.0] * 200
    win = tvw.variable_height_virtual_window(
        heights,
        scroll_top=0,
        viewport_height=400,
        buffer_px=200,
        threshold=80,
        pin_index=150,
    )
    assert win["start"] <= 150 < win["end"]


def test_force_start_pans_earlier(tvw):
    heights = [90.0] * 200
    win = tvw.variable_height_virtual_window(
        heights,
        scroll_top=9000,
        viewport_height=500,
        buffer_px=200,
        threshold=80,
        force_start=10,
    )
    assert win["start"] == 10
    assert win["end"] > 10


def test_expand_to_turn_boundaries(tvw):
    roles = ["user", "assistant", "assistant", "user", "assistant"]
    s, e = tvw.expand_to_turn_boundaries(2, 3, roles=roles)
    # start=2 is mid assistant run → back to 1
    assert s == 1
    assert e == 3


def test_threshold_constant(tvw):
    assert tvw.MSG_VIRTUAL_THRESHOLD == 80


def test_settings_default_transcript_virtual_window_off():
    from api.config import _SETTINGS_BOOL_KEYS, _SETTINGS_DEFAULTS

    assert _SETTINGS_DEFAULTS.get("transcript_virtual_window") is False
    assert "transcript_virtual_window" in _SETTINGS_BOOL_KEYS
