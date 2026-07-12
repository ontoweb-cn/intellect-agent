"""Behavior contracts for journey node edit/delete (agent.learning_mutations)."""

from __future__ import annotations

import pytest

from agent import learning_mutations as lm

_SKILL = """---
name: my-skill
description: A test skill.
---

# My Skill

Body.
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    base = tmp_path / ".intellect"
    mem = base / "memories"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("alpha note\nline two\n§\nbeta note", encoding="utf-8")
    (mem / "USER.md").write_text("user profile note", encoding="utf-8")
    skill = base / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL, encoding="utf-8")

    monkeypatch.setenv("INTELLECT_HOME", str(base))
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: mem)

    from tools import skill_usage

    skill_usage.mark_agent_created("my-skill")
    return base


def test_parse_node_kind():
    assert lm.parse_node_kind("memory:memory:0") == "memory"
    assert lm.parse_node_kind("memory:profile:3") == "memory"
    assert lm.parse_node_kind("debugging-intellect") == "skill"


def test_memory_local_index_per_source(home):
    assert lm.node_detail("memory:memory:0")["content"].startswith("alpha note")
    assert lm.node_detail("memory:memory:1")["content"] == "beta note"
    assert lm.node_detail("memory:profile:0")["content"] == "user profile note"


def test_memory_label_is_first_line(home):
    assert lm.node_detail("memory:memory:0")["label"] == "alpha note"


def test_delete_memory_rewrites_file(home):
    assert lm.delete_node("memory:memory:0")["ok"]
    remaining = (home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert "alpha note" not in remaining
    assert "beta note" in remaining


def test_edit_memory_replaces_chunk(home):
    assert lm.edit_node("memory:profile:0", "rewritten profile")["ok"]
    assert (home / "memories" / "USER.md").read_text(encoding="utf-8").strip() == "rewritten profile"


def test_edit_memory_empty_is_rejected(home):
    res = lm.edit_node("memory:memory:1", "   ")
    assert not res["ok"]
    assert "delete" in res["message"]


def test_stale_memory_index_errors(home):
    res = lm.node_detail("memory:memory:9")
    assert not res["ok"]
    assert res.get("code") == "stale"
    res = lm.node_detail("memory:profile:9")
    assert not res["ok"]
    assert res.get("code") == "stale"


def test_profile_local_stable_after_memory_delete(home):
    """Cross-source stability: deleting MEMORY.md[0] must not shift USER.md ids."""
    before = lm.node_detail("memory:profile:0")
    assert before["ok"]
    assert lm.delete_node("memory:memory:0")["ok"]
    after = lm.node_detail("memory:profile:0")
    assert after["ok"]
    assert after["content"] == before["content"]


def test_bad_memory_id_returns_error(home):
    res = lm.delete_node("memory:bogus:0")
    assert not res["ok"]


def test_skill_detail_returns_skill_md(home):
    d = lm.node_detail("my-skill")
    assert d["ok"] and d["kind"] == "skill"
    assert d.get("source") == "profile"
    assert d.get("deleteMode") == "archive"
    assert "name: my-skill" in d["content"]


def test_delete_skill_archives_recoverably(home):
    res = lm.delete_node("my-skill")
    assert res["ok"]
    assert res.get("deleteMode") == "archive"
    assert not (home / "skills" / "my-skill").exists()
    assert (home / "skills" / ".archive" / "my-skill" / "SKILL.md").exists()


def test_hub_skill_detail_and_uninstall(home, monkeypatch):
    import tools.skills_hub as hub

    hub_skill = home / "skills" / ".hub" / "hub-skill"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        "---\nname: hub-skill\ndescription: Hub installed.\n---\n\n# Hub\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hub, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", home / "skills" / ".hub")
    monkeypatch.setattr(hub, "LOCK_FILE", home / "skills" / ".hub" / "lock.json")
    monkeypatch.setattr(hub, "AUDIT_LOG", home / "skills" / ".hub" / "audit.log")

    lock = hub.HubLockFile(hub.LOCK_FILE)
    lock.record_install(
        name="hub-skill",
        source="test",
        identifier="test/hub-skill",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:deadbeef",
        install_path=".hub/hub-skill",
        files=["SKILL.md"],
    )
    from tools import skill_usage

    skill_usage.bump_use("hub-skill")

    detail = lm.node_detail("hub-skill")
    assert detail["ok"]
    assert detail.get("source") == "hub"
    assert detail.get("deleteMode") == "uninstall"

    res = lm.delete_node("hub-skill")
    assert res["ok"]
    assert res.get("deleteMode") == "uninstall"
    assert not hub_skill.exists()
    assert not (home / "skills" / ".archive" / "hub-skill").exists()
    assert lock.get_installed("hub-skill") is None
    audit = home / "skills" / ".hub" / "audit.log"
    assert audit.exists()
    assert "UNINSTALL" in audit.read_text(encoding="utf-8")
    assert "hub-skill" in audit.read_text(encoding="utf-8")


def test_hub_uninstall_audit_ignores_module_audit_path(home, tmp_path, monkeypatch):
    """Important #3: audit must land in active INTELLECT_HOME, not import-time AUDIT_LOG."""
    import tools.skills_hub as hub

    wrong = tmp_path / "other-profile" / "skills" / ".hub"
    wrong.mkdir(parents=True)
    wrong_audit = wrong / "audit.log"

    hub_skill = home / "skills" / ".hub" / "hub-skill"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        "---\nname: hub-skill\ndescription: Hub installed.\n---\n\n# Hub\n",
        encoding="utf-8",
    )
    # Simulate stale module globals pointing at another profile.
    monkeypatch.setattr(hub, "SKILLS_DIR", wrong.parent)
    monkeypatch.setattr(hub, "HUB_DIR", wrong)
    monkeypatch.setattr(hub, "AUDIT_LOG", wrong_audit)
    monkeypatch.setattr(hub, "LOCK_FILE", wrong / "lock.json")

    lock = hub.HubLockFile(home / "skills" / ".hub" / "lock.json")
    lock.record_install(
        name="hub-skill",
        source="test",
        identifier="test/hub-skill",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:deadbeef",
        install_path=".hub/hub-skill",
        files=["SKILL.md"],
    )

    res = lm.delete_node("hub-skill")
    assert res["ok"]
    active_audit = home / "skills" / ".hub" / "audit.log"
    assert active_audit.exists()
    assert "hub-skill" in active_audit.read_text(encoding="utf-8")
    assert not wrong_audit.exists() or "hub-skill" not in wrong_audit.read_text(encoding="utf-8")


def test_profile_and_hub_same_name_is_ambiguous(home, monkeypatch):
    """Critical #1: refuse detail/delete when profile + hub share a name."""
    import tools.skills_hub as hub

    hub_skill = home / "skills" / ".hub" / "my-skill"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Hub copy.\n---\n\n# Hub\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hub, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", home / "skills" / ".hub")
    monkeypatch.setattr(hub, "LOCK_FILE", home / "skills" / ".hub" / "lock.json")
    monkeypatch.setattr(hub, "AUDIT_LOG", home / "skills" / ".hub" / "audit.log")

    lock = hub.HubLockFile(hub.LOCK_FILE)
    lock.record_install(
        name="my-skill",
        source="test",
        identifier="test/my-skill",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:deadbeef",
        install_path=".hub/my-skill",
        files=["SKILL.md"],
    )

    detail = lm.node_detail("my-skill")
    assert not detail["ok"]
    assert detail.get("code") == "ambiguous"

    deleted = lm.delete_node("my-skill")
    assert not deleted["ok"]
    assert deleted.get("code") == "ambiguous"
    assert (home / "skills" / "my-skill").exists()
    assert hub_skill.exists()
    assert lock.get_installed("my-skill") is not None

    edited = lm.edit_node("my-skill", _SKILL.replace("A test skill.", "Should not write."))
    assert not edited["ok"]
    assert edited.get("code") == "ambiguous"
    assert "Updated desc." not in (home / "skills" / "my-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Should not write" not in (home / "skills" / "my-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_delete_pinned_skill_refused(home):
    from tools import skill_usage

    skill_usage.set_pinned("my-skill", True)
    res = lm.delete_node("my-skill")
    assert not res["ok"]
    assert "pinned" in res["message"]
    assert (home / "skills" / "my-skill").exists()


def test_edit_skill_rewrites_and_validates(home):
    bad = lm.edit_node("my-skill", "no frontmatter here")
    assert not bad["ok"]
    good = lm.edit_node("my-skill", _SKILL.replace("A test skill.", "Updated desc."))
    assert good["ok"]
    assert "Updated desc." in (home / "skills" / "my-skill" / "SKILL.md").read_text(encoding="utf-8")


def test_hub_edit_rewrites_under_hub(home, monkeypatch):
    import tools.skills_hub as hub

    hub_skill = home / "skills" / ".hub" / "hub-skill"
    hub_skill.mkdir(parents=True)
    content = "---\nname: hub-skill\ndescription: Hub installed.\n---\n\n# Hub\n"
    (hub_skill / "SKILL.md").write_text(content, encoding="utf-8")
    monkeypatch.setattr(hub, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", home / "skills" / ".hub")
    monkeypatch.setattr(hub, "LOCK_FILE", home / "skills" / ".hub" / "lock.json")
    monkeypatch.setattr(hub, "AUDIT_LOG", home / "skills" / ".hub" / "audit.log")
    hub.HubLockFile(hub.LOCK_FILE).record_install(
        name="hub-skill",
        source="test",
        identifier="test/hub-skill",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:deadbeef",
        install_path=".hub/hub-skill",
        files=["SKILL.md"],
    )

    updated = content.replace("Hub installed.", "Hub updated.")
    res = lm.edit_node("hub-skill", updated)
    assert res["ok"]
    assert "Hub updated." in (hub_skill / "SKILL.md").read_text(encoding="utf-8")


def test_hub_edit_rejects_path_outside_hub(home, tmp_path, monkeypatch):
    """Important #5: refuse writes whose resolved path escapes .hub."""
    outside = tmp_path / "escape-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: escape-skill\ndescription: Outside.\n---\n\n# X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lm,
        "_resolve_journey_skill",
        lambda _name: {
            "path": outside,
            "source": "hub",
            "deleteMode": "uninstall",
        },
    )
    res = lm.edit_node(
        "escape-skill",
        "---\nname: escape-skill\ndescription: Should not write.\n---\n\n# X\n",
    )
    assert not res["ok"]
    assert "escapes" in res["message"]
    assert "Should not write" not in (outside / "SKILL.md").read_text(encoding="utf-8")


def test_hub_edit_rejects_skill_md_symlink_escape(home, tmp_path, monkeypatch):
    import tools.skills_hub as hub

    hub_skill = home / "skills" / ".hub" / "link-skill"
    hub_skill.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside-secret\n", encoding="utf-8")
    try:
        (hub_skill / "SKILL.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    monkeypatch.setattr(hub, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", home / "skills" / ".hub")
    monkeypatch.setattr(hub, "LOCK_FILE", home / "skills" / ".hub" / "lock.json")
    monkeypatch.setattr(hub, "AUDIT_LOG", home / "skills" / ".hub" / "audit.log")
    hub.HubLockFile(hub.LOCK_FILE).record_install(
        name="link-skill",
        source="test",
        identifier="test/link-skill",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:deadbeef",
        install_path=".hub/link-skill",
        files=["SKILL.md"],
    )

    res = lm.edit_node(
        "link-skill",
        "---\nname: link-skill\ndescription: Escaped.\n---\n\n# Nope\n",
    )
    assert not res["ok"]
    assert "escapes" in res["message"]
    assert outside.read_text(encoding="utf-8") == "outside-secret\n"


def test_missing_skill_detail(home):
    assert not lm.node_detail("nonexistent-skill")["ok"]


def test_memory_writes_match_memory_tool_format(home):
    from tools.memory_tool import ENTRY_DELIMITER, MemoryStore

    assert lm.edit_node("memory:memory:0", "alpha rewritten")["ok"]
    path = home / "memories" / "MEMORY.md"
    entries = MemoryStore._read_file(path)

    assert entries == ["alpha rewritten", "beta note"]
    assert path.read_text(encoding="utf-8") == ENTRY_DELIMITER.join(entries)
