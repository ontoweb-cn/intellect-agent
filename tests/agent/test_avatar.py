"""Tests for deterministic blob avatars (BT-04 / B2-3)."""

from agent.avatar import (
    avatar_params,
    distinct_count,
    fnv1a32,
    render_ansi,
    render_svg,
)


def test_fnv1a32_standard_vectors():
    assert fnv1a32(b"") == 0x811C9DC5
    assert fnv1a32(b"a") == 0xE40C292C


def test_params_deterministic_and_wellformed():
    a1 = avatar_params("alpha")
    a2 = avatar_params("alpha")
    assert a1 == a2  # same name → same face, forever
    assert a1["hue"] == a2["hue"]
    assert 0 <= a1["hue"] < 360
    assert 0 <= a1["hue2"] < 360
    assert a1["eyes"]  # non-empty face components
    assert a1["mouth"]


def test_different_names_render_differently():
    names = [f"bot-{i}" for i in range(20)]
    # 20 names must produce mostly-distinct blobs (collision odds are
    # ~(1/360)^2 per pair — full uniqueness is not asserted, breadth is).
    assert distinct_count(names) >= 15


def test_render_svg_stable_and_structured():
    svg1 = render_svg("alpha")
    assert svg1 == render_svg("alpha")
    assert svg1.startswith("<svg")
    assert "hsl(" in svg1
    assert svg1 != render_svg("beta")


def test_render_ansi_color_toggle():
    plain = render_ansi("alpha", color=False)
    colored = render_ansi("alpha", color=True)
    assert "alpha" in plain and "alpha" in colored
    assert "\x1b[" in colored
    assert "\x1b[" not in plain
