"""W10 J7: Gateway Journey graph is scoped to process INTELLECT_HOME."""

from __future__ import annotations

from pathlib import Path

from agent.learning_graph import build_learning_graph
from intellect_constants import reset_intellect_home_override, set_intellect_home_override


def _seed_home(home: Path, marker: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(f"UNIQUE_MEMORY_{marker}\n", encoding="utf-8")
    skill_dir = home / "skills" / "dev" / f"skill-{marker}"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: skill-{marker}\ncategory: dev\n"
        f"metadata:\n  intellect:\n    created_by: agent\n---\n# {marker}\n",
        encoding="utf-8",
    )
    usage = home / "skills" / ".usage.json"
    usage.write_text(
        f'{{"skill-{marker}": {{"created_by": "agent", "use_count": 1}}}}\n',
        encoding="utf-8",
    )


def _labels(graph: dict) -> set[str]:
    labels = {str(n.get("label") or n.get("id") or "") for n in graph.get("nodes", [])}
    labels.update(str(c.get("title") or "") for c in graph.get("memory", []))
    return labels


def test_journey_graph_home_a_excludes_home_b(tmp_path):
    """T-home-A: override home A → graph contains A markers, not B."""
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    _seed_home(home_a, "AAA")
    _seed_home(home_b, "BBB")

    token = set_intellect_home_override(home_a)
    try:
        labels = _labels(build_learning_graph())
    finally:
        reset_intellect_home_override(token)

    assert any("AAA" in x for x in labels)
    assert not any("BBB" in x for x in labels)


def test_journey_graph_home_b_excludes_home_a(tmp_path):
    """T-home-B: override home B → graph contains B markers, not A."""
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    _seed_home(home_a, "AAA")
    _seed_home(home_b, "BBB")

    token = set_intellect_home_override(home_b)
    try:
        labels = _labels(build_learning_graph())
    finally:
        reset_intellect_home_override(token)

    assert any("BBB" in x for x in labels)
    assert not any("AAA" in x for x in labels)
