from __future__ import annotations

from pathlib import Path

import pytest

from intellect_cli.wiki_scaffold import (
    global_wiki_dir,
    init_wiki,
    is_forbidden_path,
    members_scoping_active,
    redact_path_hint,
    resolve_wiki_target,
    safe_slug,
    wiki_write_mode,
)


def test_safe_slug_rejects_traversal() -> None:
    assert safe_slug("my-project") is True
    assert safe_slug("../evil") is False
    assert safe_slug("a/b") is False


def test_resolve_wiki_target_prefers_project(tmp_path: Path) -> None:
    target = resolve_wiki_target(
        intellect_home=tmp_path,
        member_id="m1",
        team_id="t1",
        project_id="p1",
    )
    assert target.scope == "project"
    assert target.scope_id == "p1"
    assert target.path == tmp_path / "projects" / "p1" / "wiki"


def test_resolve_wiki_target_env_override(tmp_path: Path) -> None:
    target = resolve_wiki_target(
        intellect_home=tmp_path,
        project_id="p1",
        env_wiki_path="/tmp/custom-wiki",
    )
    assert target.scope == "global"
    assert target.path == Path("/tmp/custom-wiki")
    assert target.path_source == "WIKI_PATH"


def test_init_wiki_creates_scaffold(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    result = init_wiki(wiki, domain="AI research")
    assert result.ok is True
    assert (wiki / "SCHEMA.md").is_file()
    assert (wiki / "index.md").is_file()
    assert (wiki / "log.md").is_file()
    assert (wiki / "raw" / "articles").is_dir()
    assert (wiki / "entities").is_dir()
    assert "AI research" in (wiki / "SCHEMA.md").read_text(encoding="utf-8")


def test_init_wiki_refuses_existing_guard_files(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "SCHEMA.md").write_text("# existing\n", encoding="utf-8")
    result = init_wiki(wiki)
    assert result.ok is False
    assert result.error_code == "wiki_already_exists"


def test_init_wiki_idempotent_on_partial_tree(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "entities").mkdir(parents=True)
    result = init_wiki(wiki)
    assert result.ok is True
    assert "entities/" in result.files_skipped
    assert "SCHEMA.md" in result.files_created


def test_redact_path_hint_uses_tilde(home_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home_tmp_path))
    wiki = home_tmp_path / "wiki"
    assert redact_path_hint(wiki).startswith("~/wiki")


@pytest.fixture
def home_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_is_forbidden_path_root() -> None:
    assert is_forbidden_path(Path("/")) is True


def test_global_wiki_target(tmp_path: Path) -> None:
    target = resolve_wiki_target(
        intellect_home=tmp_path,
        target_scope="global",
    )
    assert target.scope == "global"
    assert target.path == global_wiki_dir(tmp_path)


def test_members_scoping_skips_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKI_PATH", "/tmp/legacy-wiki")
    config = {"members": {"enabled": True}}
    assert members_scoping_active(config, "m1") is True
    target = resolve_wiki_target(
        intellect_home=tmp_path,
        member_id="m1",
        env_wiki_path=None,
        config=config,
    )
    assert target.scope == "member"
    assert target.path == tmp_path / "members" / "m1" / "wiki"


def test_wiki_write_mode_global_rbac() -> None:
    assert wiki_write_mode("global", "admin") == "read_write"
    assert wiki_write_mode("global", "member") == "read_only"
    assert wiki_write_mode("member", "member") == "read_write"
