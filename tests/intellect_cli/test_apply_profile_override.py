"""Regression tests for _apply_profile_override INTELLECT_HOME guard (issue #22502).

When INTELLECT_HOME is set to the intellect root (e.g. systemd hardcodes
INTELLECT_HOME=/root/.intellect), _apply_profile_override must still read
active_profile and update INTELLECT_HOME to the profile directory.

When INTELLECT_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path



def _run_apply_profile_override(
    tmp_path, monkeypatch, *, intellect_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["INTELLECT_HOME"] after the call,
    or None if unset.
    """
    intellect_root = tmp_path / ".intellect"
    intellect_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (intellect_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (intellect_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if intellect_home is not None:
        monkeypatch.setenv("INTELLECT_HOME", intellect_home)
    else:
        monkeypatch.delenv("INTELLECT_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["intellect", "gateway", "start"])

    from intellect_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("INTELLECT_HOME")


class TestApplyProfileOverrideintellectHomeGuard:
    """Regression guard for issue #22502.

    Verifies that INTELLECT_HOME pointing to the intellect root does NOT suppress
    the active_profile check, while INTELLECT_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_intellect_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """INTELLECT_HOME=/root/.intellect + active_profile=coder must redirect
        INTELLECT_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets INTELLECT_HOME to the intellect root
        and the user switches to a profile via `intellect profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        intellect_root = tmp_path / ".intellect"
        intellect_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            intellect_home=str(intellect_root),
            active_profile="coder",
        )

        assert result is not None, "INTELLECT_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected INTELLECT_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected INTELLECT_HOME to end with 'coder', got: {result!r}"
        )

    def test_intellect_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """INTELLECT_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with INTELLECT_HOME already set to a specific profile must stay in that
        profile.
        """
        intellect_root = tmp_path / ".intellect"
        profile_dir = intellect_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (intellect_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("INTELLECT_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["intellect", "gateway", "start"])

        from intellect_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("INTELLECT_HOME") == str(profile_dir), (
            "INTELLECT_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_intellect_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: INTELLECT_HOME unset + active_profile=coder must set
        INTELLECT_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            intellect_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_intellect_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect INTELLECT_HOME."""
        intellect_root = tmp_path / ".intellect"
        intellect_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("INTELLECT_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["intellect", "gateway", "start"])
        (intellect_root / "active_profile").write_text("default")

        from intellect_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("INTELLECT_HOME") is None
