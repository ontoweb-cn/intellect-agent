"""Source contracts for the chat turn navigator.

The WebUI has no jsdom harness. These checks lock the decisions from the
second review: instant scroll, no full-history fetch, rail outside the
scrollport, and reply text that does not rebuild the rail mid-stream.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_UI = (_ROOT / "webui" / "static" / "ui.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "static" / "index.html").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    for marker in (f"function {name}(", f"async function {name}("):
        start = source.find(marker)
        if start >= 0:
            break
    else:
        raise AssertionError(f"{name} not found")
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"{name} is unterminated")


def test_jump_unpins_and_does_not_use_scroll_into_view():
    body = _function_body(_UI, "jumpToTurnQuestion")
    assert "_scrollPinned=false" in body
    assert "scrollTopForTurn" in body
    assert "_programmaticScroll=false" in body
    assert "scrollIntoView" not in body


def test_cached_transcript_refreshes_the_turn_navigator():
    marker = "_sessionHtmlCacheSid=sid;"
    start = _UI.find(marker)
    assert start >= 0
    window = _UI[start : start + 1200]
    return_at = window.find("return;")
    assert return_at > 0
    assert "_syncTurnNavigator" in window[:return_at]


def test_navigator_sync_does_not_fetch_the_full_transcript():
    body = _function_body(_UI, "_syncTurnNavigator")
    assert "_ensureAllMessagesLoaded" not in body


def test_reply_updates_wait_until_streaming_stops():
    body = _function_body(_UI, "_updateTurnNavigatorReplies")
    assert "activeStreamId" in body


def test_keyboard_requires_alt_and_ignores_text_fields():
    body = _function_body(_UI, "_onTurnNavigatorKeydown")
    assert "e.altKey" in body
    assert "INPUT" in body
    assert "TEXTAREA" in body


def test_wheel_on_the_rail_is_ignored_by_the_capture_listener():
    body = _function_body(_UI, "_recordNonMessageScrollIntent")
    assert "#turnNavigator" in body


def test_rail_is_a_sibling_of_the_message_scrollport():
    shell = _HTML.index('<div class="messages-shell">')
    messages = _HTML.index('<div class="messages" id="messages">', shell)
    nav = _HTML.index('<nav id="turnNavigator"', shell)
    shell_end = _HTML.index("<!-- /.messages-shell -->", nav)
    assert shell < messages < nav < shell_end
    chunk = _HTML[messages:nav]
    assert chunk.count("<div") == chunk.count("</div>")
    assert 'src="static/turn-outline.js' in _HTML
    assert _HTML.index('src="static/turn-outline.js') < _HTML.index('src="static/ui.js')


def test_javascript_outline_matches_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    import sys

    webui = _ROOT / "webui"
    if str(webui) not in sys.path:
        sys.path.insert(0, str(webui))
    import api.turn_outline as outline

    messages = [
        {"role": "user", "content": "[Workspace::v1: /tmp/proj]\n# See [docs](https://example.test)"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "search"}]},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "", "attachments": [{"name": "notes/pic.png"}]},
        {
            "role": "user",
            "content": "[Your active task list was preserved across context compression]",
        },
    ]
    script = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const messages = JSON.parse(process.argv[2]);
const entries = sandbox.window.buildTurnOutline(messages).map((entry) => ({
  raw_idx: entry.rawIdx,
  title: entry.title,
  reply: entry.reply,
}));
const active = sandbox.window.pickActiveTurn([
  { rawIdx: 3, relTop: -1 },
  { rawIdx: 9, relTop: 100 },
]);
const scrolled = sandbox.window.scrollTopForTurn(10, 20, 56);
process.stdout.write(JSON.stringify({ entries, active, scrolled }));
"""
    completed = subprocess.run(
        [node, "-e", script, str(_ROOT / "webui" / "static" / "turn-outline.js"), json.dumps(messages)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["entries"] == outline.build_turn_outline(messages)
    assert payload["active"] == outline.pick_active_turn(
        [{"raw_idx": 3, "rel_top": -1}, {"raw_idx": 9, "rel_top": 100}]
    )
    assert payload["scrolled"] == outline.scroll_top_for_turn(10, 20, 56)
