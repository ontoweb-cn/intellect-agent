from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from intellect_cli import vault_build as vb


def _write_wiki(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SCHEMA.md").write_text("# schema\n", encoding="utf-8")
    (path / "entities").mkdir(exist_ok=True)
    (path / "entities" / "one.md").write_text("# one\n", encoding="utf-8")


def test_discover_vault_build_targets_scoped_and_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_wiki(tmp_path / "projects" / "p1" / "wiki")
    _write_wiki(tmp_path / "members" / "m1" / "wiki")
    global_wiki = tmp_path / "wiki-global"
    _write_wiki(global_wiki)
    monkeypatch.setenv("WIKI_PATH", str(global_wiki))
    targets = vb.discover_vault_build_targets(tmp_path)

    scopes = {(t.scope, t.scope_id) for t in targets}
    assert ("project", "p1") in scopes
    assert ("member", "m1") in scopes
    assert ("global", None) in scopes


def test_should_build_vault_skips_unchanged() -> None:
    target = vb.VaultBuildTarget("global", None, Path("/w"), Path("/v"), "/vault", "LLM Wiki")
    ok, reason = vb.should_build_vault(
        target, last_build_ts=100.0, wiki_mtime=50.0, throttle_seconds=0, force=False
    )
    assert ok is False
    assert reason == "unchanged"


def test_is_cron_due_hourly(tmp_path: Path) -> None:
    base = time.time() - 7200
    assert vb.is_cron_due("0 * * * *", base, time.time()) is True


def test_run_scheduled_vault_tick_not_due(tmp_path: Path) -> None:
    state_path = tmp_path / "vaults" / ".last-build-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"_scheduler": {"last_scheduled_tick_at": ' + str(time.time()) + "}}",
        encoding="utf-8",
    )
    vcfg = {"build_trigger": "scheduled", "build_schedule": "0 0 1 1 *"}
    result = vb.run_scheduled_vault_tick(vcfg, intellect_home=tmp_path)
    assert result.ran is False
    assert result.skipped_reason == "not_due"


def test_run_scheduled_vault_tick_builds_changed_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = tmp_path / "projects" / "p1" / "wiki"
    _write_wiki(wiki)
    vcfg = {
        "build_trigger": "scheduled",
        "build_schedule": "0 * * * *",
        "build_throttle_seconds": 0,
        "build_timeout_seconds": 30,
    }

    def fake_run(target, **kwargs):
        return vb.BuildResult(ok=True, target=target, trigger="scheduled")

    monkeypatch.setattr(vb, "run_vault_build", fake_run)
    result = vb.run_scheduled_vault_tick(vcfg, intellect_home=tmp_path, force=True)
    assert result.ran is True
    assert result.built == 1
    state = vb.read_last_build_state(tmp_path)
    assert state["_scheduler"]["last_scheduled_status"] == "ok"


def test_validate_cron_schedule_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        vb.validate_cron_schedule("not a cron")
