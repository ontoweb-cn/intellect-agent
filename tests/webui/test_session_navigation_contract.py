"""Source contracts for session navigation gating (#157).

The WebUI has no jsdom harness. These checks lock the decisions from the
#157 triage: draft restore reuses the real ``force`` parameter (the
undefined ``forceReload`` crashed every load of a session carrying a
composer draft), the per-answer jump-to-question button is gated by the
``session_jump_buttons`` setting via the ``session-nav-enabled`` class,
and the turn navigator rides the same setting.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UI = (_ROOT / "webui" / "static" / "ui.js").read_text(encoding="utf-8")
_SESSIONS = (_ROOT / "webui" / "static" / "sessions.js").read_text(encoding="utf-8")
_STYLE = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    for marker in (f"function {name}(", f"async function {name}("):
        start = source.find(marker)
        if start >= 0:
            break
    else:
        raise AssertionError(f"{name} not found")
    # Skip the parameter list first — a default like `options={}` would
    # otherwise be mistaken for the function body.
    paren_depth = 0
    params_open = source.index("(", start)
    for index in range(params_open, len(source)):
        char = source[index]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                brace = source.find("{", index + 1)
                break
    else:
        raise AssertionError(f"{name} has unterminated parameters")
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


def test_load_session_never_references_undefined_force_reload():
    body = _function_body(_SESSIONS, "loadSession")
    assert "forceReload" not in body
    assert "_restoreComposerDraft" in body


def test_question_jump_button_hides_when_session_nav_disabled():
    gate = ".messages:not(.session-nav-enabled) .msg-question-jump-btn { display: none; }"
    assert gate in _STYLE


def test_turn_navigator_rides_session_jump_buttons_setting():
    body = _function_body(_UI, "_applyTurnNavigatorChrome")
    assert "_isSessionJumpButtonsEnabled()" in body
    assert "nav.hidden=true" in body


def test_toggling_the_setting_reapplies_the_navigator():
    body = _function_body(_UI, "_applySessionNavigationPrefs")
    assert "session-nav-enabled" in body
    assert "_applyTurnNavigatorChrome()" in body
