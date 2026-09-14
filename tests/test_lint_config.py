"""Tests for ruff lint config — guards against accidental rule removal.

PLW1514 (unspecified-encoding) was enabled after a debug session on
Windows turned up three separate UTF-8 regressions in execute_code.
The rule catches bare ``open()`` / ``read_text()`` / ``write_text()``
calls that default to locale encoding — cp1252 on Windows — which
silently corrupts non-ASCII content.

These tests ensure:
  1. PLW1514 stays in ``[tool.ruff.lint.select]``
  2. The CI workflow's blocking step still invokes ``ruff check .``
  3. pyproject.toml has ``preview = true`` (required — PLW1514 is a
     preview rule in ruff 0.15.x)

If someone removes any of these, CI stops enforcing UTF-8-explicit
opens and we're back to the original Windows-regression trap.
"""

from __future__ import annotations

import pathlib

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover — 3.10 and earlier
    import tomli as tomllib  # type: ignore

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_pyproject() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


class TestRuffConfig:
    def test_plw1514_is_in_select_list(self):
        """pyproject.toml must keep PLW1514 in [tool.ruff.lint.select]."""
        cfg = _load_pyproject()
        selected = (
            cfg.get("tool", {})
            .get("ruff", {})
            .get("lint", {})
            .get("select", [])
        )
        assert "PLW1514" in selected, (
            "PLW1514 (unspecified-encoding) was removed from "
            "[tool.ruff.lint.select].  This rule blocks bare open() calls "
            "that default to locale encoding on Windows — removing it "
            "re-opens a class of UTF-8 bugs we already paid to close.  "
            "If you genuinely want to remove it, delete this test in the "
            "same commit so the intent is deliberate."
        )

    def test_preview_mode_enabled(self):
        """PLW1514 is a preview rule in ruff 0.15.x — preview=true is
        required for it to actually run."""
        cfg = _load_pyproject()
        ruff_cfg = cfg.get("tool", {}).get("ruff", {})
        assert ruff_cfg.get("preview") is True, (
            "[tool.ruff] preview=true is required — PLW1514 is a preview "
            "rule and silently becomes a no-op without it.  If this ever "
            "becomes a stable rule, you can drop preview=true but must "
            "verify PLW1514 still fires in a sample test run first."
        )


class TestLintWorkflow:
    WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "lint.yml"

    def test_workflow_exists(self):
        assert self.WORKFLOW_PATH.exists(), (
            f"CI workflow missing: {self.WORKFLOW_PATH}"
        )

    def test_workflow_has_blocking_ruff_step(self):
        """The workflow must FAIL on ruff violations.

        Enforced as a ratchet rather than an absolute ``ruff check .``: the tree
        carries ~1100 pre-existing findings under the selected rules, so an
        absolute gate was red on every push and enforced nothing (a permanently
        failing check is equivalent to no check).  The gate diffs against the
        base commit and fails only on NEW diagnostics, which preserves the
        intent — a PR cannot introduce a PLW1514/F violation — while letting the
        backlog shrink incrementally.
        """
        content = self.WORKFLOW_PATH.read_text(encoding="utf-8")

        # The collection step uses --exit-zero so the reports can be diffed;
        # the blocking decision must come from --fail-on-new.
        assert "--fail-on-new" in content, (
            "lint.yml no longer blocks on new ruff diagnostics.  Without this, "
            "PLW1514 and the F rules are advisory only and CI cannot fail on a "
            "violation."
        )
        for i, line in enumerate(content.splitlines()):
            if "--fail-on-new" in line:
                window = " ".join(content.splitlines()[i:i + 3])
                assert "|| true" not in window, (
                    "--fail-on-new is masked by `|| true`, so new lint findings "
                    "would not fail the job."
                )
                break

    def test_workflow_has_blocking_windows_footgun_step(self):
        """The footgun checker must be wired in without being masked."""
        content = self.WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "check-windows-footguns.py --all" in content, (
            "lint.yml no longer runs the Windows footgun checker."
        )
        for line in content.splitlines():
            if "check-windows-footguns.py --all" in line:
                assert "--exit-zero" not in line and "|| true" not in line, (
                    "the footgun check is masked and cannot fail the job."
                )
                break

    def test_workflow_yaml_is_valid(self):
        """Workflow file must parse as valid YAML (can't ship a broken
        CI config to main)."""
        import yaml
        content = self.WORKFLOW_PATH.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            pytest.fail(f"lint.yml is not valid YAML: {exc}")
        assert isinstance(parsed, dict)
        assert "jobs" in parsed
