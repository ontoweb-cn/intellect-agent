"""W8 update_channel contract — stable default, experimental skips tags, honest fail."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def upd(monkeypatch):
    import importlib

    import api.updates as mod

    mod = importlib.reload(mod)
    monkeypatch.delenv("INTELLECT_WEBUI_EXPERIMENTAL_REF", raising=False)
    return mod


def test_defaults_include_update_channel():
    from api.config import _SETTINGS_DEFAULTS, _SETTINGS_ENUM_VALUES

    assert _SETTINGS_DEFAULTS.get("update_channel") == "stable"
    assert "ignore_agent_updates" in _SETTINGS_DEFAULTS
    assert _SETTINGS_DEFAULTS["ignore_agent_updates"] is False
    assert _SETTINGS_ENUM_VALUES.get("update_channel") == {"stable", "experimental"}


def test_experimental_ref_default_and_env(upd, monkeypatch):
    assert upd.experimental_compare_ref() == "origin/experimental"
    monkeypatch.setenv("INTELLECT_WEBUI_EXPERIMENTAL_REF", "origin/nightly")
    assert upd.experimental_compare_ref() == "origin/nightly"


def test_resolve_experimental_missing_raises(upd):
    with patch.object(upd, "_remote_ref_exists", return_value=False):
        with pytest.raises(ValueError, match="Experimental update track not found"):
            upd.resolve_compare_ref(Path("/tmp"), "experimental")


def test_resolve_experimental_returns_ref(upd):
    with patch.object(upd, "_remote_ref_exists", return_value=True):
        assert upd.resolve_compare_ref(Path("/tmp"), "experimental") == "origin/experimental"


def test_check_repo_experimental_skips_release(upd, tmp_path):
    """U8: experimental path must never call _check_repo_release."""
    (tmp_path / ".git").mkdir()

    with (
        patch.object(upd, "_run_git", return_value=("", True)),
        patch.object(upd, "resolve_compare_ref", return_value="origin/experimental"),
        patch.object(
            upd,
            "_check_repo_branch",
            return_value={"name": "intellect-agent", "behind": 2, "branch": "origin/experimental"},
        ) as branch,
        patch.object(upd, "_check_repo_release") as release,
    ):
        info = upd._check_repo(tmp_path, "intellect-agent", channel="experimental")
        release.assert_not_called()
        branch.assert_called_once()
        assert branch.call_args.kwargs.get("compare_ref") == "origin/experimental"
        assert info["channel"] == "experimental"
        assert info["behind"] == 2


def test_ensure_checkout_for_experimental(upd, tmp_path):
    """U9: experimental apply checkouts/tracks the remote ref."""
    calls = []

    def fake_run(args, path, timeout=None):
        calls.append(list(args))
        return ("", True)

    with patch.object(upd, "_run_git", side_effect=fake_run):
        ok, err = upd._ensure_checkout_for_experimental(tmp_path, "origin/experimental")
    assert ok and err == ""
    assert calls[0][:3] == ["checkout", "-B", "experimental"]
    assert calls[0][3] == "origin/experimental"
    assert any(c[:2] == ["branch", "--set-upstream-to=origin/experimental"] for c in calls)


def test_invalidate_update_cache(upd):
    with upd._cache_lock:
        upd._update_cache["checked_at"] = 999
        upd._update_cache["updates"] = {"behind": 1}
        upd._update_cache["channel"] = "experimental"
    upd.invalidate_update_cache()
    with upd._cache_lock:
        assert upd._update_cache["checked_at"] == 0
        assert upd._update_cache["updates"] is None
        assert upd._update_cache["channel"] is None
