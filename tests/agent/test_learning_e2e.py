"""W1 P1-4: Learning API E2E — handler → disk → restore/graph.

Must not mock ``learning_mutations.delete_node`` / ``node_detail``.
Gate cases: E1–E4 per docs/plans/2026-07-12-w1-journey-e2e-and-session-sse.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

_SKILL = """---
name: e2e-skill
description: E2E journey skill.
---

# E2E Skill

Body.
"""


def _nullcontext():
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Ctx()


@pytest.fixture
def learning_api_module():
    import importlib

    import api.learning as mod

    return importlib.reload(mod)


@pytest.fixture
def e2e_home(tmp_path, monkeypatch):
    """Active INTELLECT_HOME with cookie-home wiring (no mutation mocks)."""
    home = tmp_path / "active-profile"
    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("alpha\n§\nbeta", encoding="utf-8")
    (home / "skills").mkdir(parents=True)

    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: mem)
    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: home)
    monkeypatch.setattr(
        "api.profiles.cron_profile_context_for_home",
        lambda _h: _nullcontext(),
    )
    return home


def _capture_j():
    recorded: list[tuple[dict, int]] = []

    def _j(handler, payload, status=200, extra_headers=None):
        recorded.append((payload, status))

    return recorded, _j


def _graph_ids():
    from agent.learning_graph import build_learning_graph

    return {n["id"] for n in build_learning_graph().get("nodes", [])}


def _install_profile_skill(home: Path, name: str = "e2e-skill") -> Path:
    skill = home / "skills" / name
    skill.mkdir(parents=True)
    body = _SKILL.replace("e2e-skill", name)
    (skill / "SKILL.md").write_text(body, encoding="utf-8")
    from tools import skill_usage

    skill_usage.mark_agent_created(name)
    return skill


def _install_hub_skill(home: Path, monkeypatch, name: str = "hub-e2e") -> Path:
    import json

    import tools.skills_hub as hub

    hub_skill = home / "skills" / ".hub" / name
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Hub E2E.\n---\n\n# Hub\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hub, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(hub, "HUB_DIR", home / "skills" / ".hub")
    monkeypatch.setattr(hub, "LOCK_FILE", home / "skills" / ".hub" / "lock.json")
    # Poison module AUDIT_LOG so E3 asserts profile-safe audit (W0 Important #3).
    wrong = home.parent / "wrong-profile" / "skills" / ".hub"
    wrong.mkdir(parents=True)
    monkeypatch.setattr(hub, "AUDIT_LOG", wrong / "audit.log")

    lock = hub.HubLockFile(home / "skills" / ".hub" / "lock.json")
    lock.record_install(
        name=name,
        source="test",
        identifier=f"test/{name}",
        trust_level="trusted",
        scan_verdict="clean",
        skill_hash="sha256:e2e",
        install_path=f".hub/{name}",
        files=["SKILL.md"],
    )
    # Hub names are off-limits to skill_usage._mutate (bump_use is a no-op).
    # Journey still shows hub skills when .usage.json has use_count > 0.
    usage_path = home / "skills" / ".usage.json"
    data = {}
    if usage_path.exists():
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    data[name] = {
        "created_by": None,
        "use_count": 1,
        "view_count": 0,
        "last_used_at": "2026-07-12T00:00:00+00:00",
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": "2026-07-12T00:00:00+00:00",
        "state": "active",
        "pinned": False,
        "archived_at": None,
    }
    usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return hub_skill


# ── E1: agent-created archive → restore → graph ─────────────────────────────


def test_e1_archive_restore_via_handler(learning_api_module, e2e_home):
    name = "e2e-skill"
    _install_profile_skill(e2e_home, name)
    assert name in _graph_ids()

    recorded, capture = _capture_j()
    handler = MagicMock()
    with patch("api.helpers.j", side_effect=capture):
        learning_api_module.handle_learning_node_delete(handler, {"id": name})

    assert recorded and recorded[-1][1] == 200
    assert recorded[-1][0].get("ok") is True
    assert recorded[-1][0].get("deleteMode") == "archive"
    assert not (e2e_home / "skills" / name).exists()
    assert (e2e_home / "skills" / ".archive" / name / "SKILL.md").exists()
    assert name not in _graph_ids()

    from tools import skill_usage

    ok, msg = skill_usage.restore_skill(name)
    assert ok, msg
    assert (e2e_home / "skills" / name / "SKILL.md").exists()
    from agent.learning_graph import build_learning_graph

    nodes = {n["id"]: n for n in build_learning_graph()["nodes"]}
    assert name in nodes
    assert nodes[name].get("source") == "profile"


# ── E2: pinned refuse ───────────────────────────────────────────────────────


def test_e2_pinned_delete_refused_via_handler(learning_api_module, e2e_home):
    name = "e2e-skill"
    skill = _install_profile_skill(e2e_home, name)
    from tools import skill_usage

    skill_usage.set_pinned(name, True)

    recorded, capture = _capture_j()
    handler = MagicMock()
    with patch("api.helpers.j", side_effect=capture):
        learning_api_module.handle_learning_node_delete(handler, {"id": name})

    assert recorded and recorded[-1][1] == 400
    assert recorded[-1][0].get("ok") is not True
    assert "pinned" in (recorded[-1][0].get("error") or "").lower()
    assert skill.exists()
    assert name in _graph_ids()


# ── E3: hub uninstall + audit in active home ────────────────────────────────


def test_e3_hub_uninstall_via_handler(learning_api_module, e2e_home, monkeypatch):
    name = "hub-e2e"
    hub_skill = _install_hub_skill(e2e_home, monkeypatch, name)
    assert name in _graph_ids()

    recorded, capture = _capture_j()
    handler = MagicMock()
    with patch("api.helpers.j", side_effect=capture):
        learning_api_module.handle_learning_node_delete(handler, {"id": name})

    assert recorded and recorded[-1][1] == 200
    assert recorded[-1][0].get("deleteMode") == "uninstall"
    assert not hub_skill.exists()
    assert not (e2e_home / "skills" / ".archive" / name).exists()
    assert name not in _graph_ids()

    import tools.skills_hub as hub

    lock = hub.HubLockFile(e2e_home / "skills" / ".hub" / "lock.json")
    assert lock.get_installed(name) is None

    active_audit = e2e_home / "skills" / ".hub" / "audit.log"
    assert active_audit.exists()
    assert name in active_audit.read_text(encoding="utf-8")
    wrong_audit = e2e_home.parent / "wrong-profile" / "skills" / ".hub" / "audit.log"
    assert not wrong_audit.exists() or name not in wrong_audit.read_text(encoding="utf-8")


# ── E4: ambiguous profile ∩ hub ─────────────────────────────────────────────


def test_e4_ambiguous_via_handlers(learning_api_module, e2e_home, monkeypatch):
    name = "shared-skill"
    profile_dir = _install_profile_skill(e2e_home, name)
    hub_dir = _install_hub_skill(e2e_home, monkeypatch, name)
    profile_before = (profile_dir / "SKILL.md").read_text(encoding="utf-8")
    hub_before = (hub_dir / "SKILL.md").read_text(encoding="utf-8")

    recorded, capture = _capture_j()
    handler = MagicMock()
    parsed = urlparse(f"/api/learning/node?id={name}")

    with patch("api.helpers.j", side_effect=capture):
        learning_api_module.handle_learning_node_get(handler, parsed)
        learning_api_module.handle_learning_node_delete(handler, {"id": name})
        learning_api_module.handle_learning_node_put(
            handler,
            {"id": name, "content": profile_before.replace("E2E journey skill.", "Should not write.")},
        )

    assert len(recorded) == 3
    for payload, status in recorded:
        assert status == 409, (status, payload)
        assert payload.get("code") == "ambiguous"

    assert profile_dir.exists()
    assert hub_dir.exists()
    assert (profile_dir / "SKILL.md").read_text(encoding="utf-8") == profile_before
    assert (hub_dir / "SKILL.md").read_text(encoding="utf-8") == hub_before
    import tools.skills_hub as hub

    assert hub.HubLockFile(e2e_home / "skills" / ".hub" / "lock.json").get_installed(name) is not None
