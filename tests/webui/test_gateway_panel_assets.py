"""Static-asset guards for the gateway panel (webui/static/gateway-logos.js).

The logo file is code loaded via <script src>, same trust level as panels.js —
these assertions pin the vendored content to a known-safe shape (path-only
SVG fragments, no event handlers / external references) so a poisoned vendor
snapshot cannot smuggle active content past review.
"""

from __future__ import annotations

import re
from pathlib import Path

LOGOS_PATH = Path(__file__).resolve().parents[2] / "webui" / "static" / "gateway-logos.js"


def _entries():
    src = LOGOS_PATH.read_text(encoding="utf-8")
    return re.findall(r' (\w+): \{viewBox: "([^"]+)"(, multicolor: true)?(, color: (null|"[^"]*"))?, body: "(.*)"\},', src)


def test_all_expected_platforms_have_logos():
    entries = dict((name, vb) for name, vb, *_ in _entries())
    for platform in ("telegram", "slack", "discord", "whatsapp", "weixin", "qqbot",
                     "dingtalk", "email", "wecom", "wecom_callback", "feishu"):
        assert platform in entries, platform


def test_bodies_are_path_only_svg_fragments():
    forbidden = ("<script", "onerror", "onload", "onclick", "javascript:", "<use",
                 "<image", "foreignObject", "xlink", "href", "<iframe", "<object",
                 "<embed", "<style")
    for name, _vb, _multi, _color, _cq, body in _entries():
        unescaped = body.encode().decode("unicode_escape")
        low = unescaped.lower()
        for needle in forbidden:
            assert needle not in low, (name, needle)
        assert re.fullmatch(r"(\s*<path [^>]*/>\s*)+", unescaped), name


def test_feishu_is_multicolor_with_official_fills():
    src = LOGOS_PATH.read_text(encoding="utf-8")
    m = re.search(r' feishu: \{viewBox: "([^"]+)", multicolor: true, body: "(.*)"\},', src)
    assert m, "feishu entry must stay multicolor"
    body = m.group(2).encode().decode("unicode_escape")
    fills = set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', body))
    assert fills == {"#3370FF", "#14C0FF", "#93E55E"}
