"""Behavior contracts for the learning-graph assembler."""

from __future__ import annotations

import pytest

from agent import learning_graph


def _node(name: str, category: str, related=None):
    n = learning_graph.SkillNode(name=name, category=category)
    n.related = list(related or [])
    return n


def test_edges_only_connect_existing_nodes():
    nodes = {
        "a": _node("a", "x", related=["b", "ghost"]),
        "b": _node("b", "x", related=["a"]),
        "c": _node("c", "y"),
    }
    edges = learning_graph.build_edges(nodes)
    assert edges == [("a", "b")]


def test_density_stats_count_isolated_nodes():
    nodes = {
        "a": _node("a", "x", related=["b"]),
        "b": _node("b", "x", related=["a"]),
        "c": _node("c", "y"),
    }
    stats = learning_graph.density_stats(nodes, learning_graph.build_edges(nodes))
    assert stats["nodes"] == 3
    assert stats["linked_nodes"] == 2
    assert stats["isolated_pct"] == round(100 / 3, 1)


def test_skill_node_timestamp_uses_iso_usage_activity(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "dev" / "iso-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: iso-skill\ncategory: dev\n---\n# ISO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        learning_graph,
        "_load_usage",
        lambda: {
            "iso-skill": {
                "created_by": "agent",
                "last_used_at": "2026-04-30T12:00:00+00:00",
                "use_count": 1,
            }
        },
    )
    nodes = learning_graph.build_skill_nodes([("profile", tmp_path / "skills")])
    assert nodes["iso-skill"].timestamp == 1_777_550_400


def test_memory_is_cards_split_on_separator(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(
        "Project uses pytest with xdist\n§\nUser prefers concise responses",
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: mem)

    graph = learning_graph.build_learning_graph()

    titles = [c["title"] for c in graph["memory"]]
    assert "Project uses pytest with xdist" in titles
    assert "User prefers concise responses" in titles
    assert all(c["source"] in {"memory", "profile"} for c in graph["memory"])
    assert any(n["kind"] == "memory" for n in graph["nodes"])


def test_malformed_frontmatter_metadata_does_not_crash(tmp_path):
    skill_dir = tmp_path / "skills" / "misc" / "bad-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        '---\nname: bad-skill\nmetadata: not-a-dict\ndescription: "oops\n---\n# Bad\n',
        encoding="utf-8",
    )
    node = learning_graph.build_skill_nodes([("profile", tmp_path / "skills")])["bad-skill"]
    assert node.category == "misc"
    assert node.related == []


def test_intellect_meta_tolerates_non_dict():
    assert learning_graph._intellect_meta({"metadata": "junk"}) == {}
    assert learning_graph._intellect_meta({"metadata": {"intellect": "junk"}}) == {}
    assert learning_graph._intellect_meta({"metadata": {"intellect": {"category": "x"}}}) == {"category": "x"}


def test_full_payload_shape_and_edge_integrity(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr(learning_graph, "_skill_roots", lambda: [("profile", home / "skills")])
    graph = learning_graph.build_learning_graph()

    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    cluster_cats = {c["category"] for c in graph["clusters"]}
    assert all(n["category"] in cluster_cats for n in graph["nodes"])
    skill_nodes = [n for n in graph["nodes"] if n["kind"] == "skill"]
    assert graph["stats"]["nodes"] == len(skill_nodes)
    assert graph["stats"]["memory_nodes"] == len(graph["memory"])


def test_graph_mutation_memory_parser_parity(tmp_path, monkeypatch):
    """Graph cards and mutation edits must share MemoryStore._read_file indices."""
    from agent import learning_mutations as lm
    from tools.memory_tool import ENTRY_DELIMITER, MemoryStore, get_memory_dir

    home = tmp_path / ".intellect"
    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("alpha\n§\nbeta", encoding="utf-8")
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: mem)

    graph = learning_graph.build_learning_graph()
    mem_nodes = [n for n in graph["nodes"] if n["kind"] == "memory"]
    assert len(mem_nodes) == 2

    assert lm.edit_node("memory:memory:0", "alpha rewritten")["ok"]
    path = get_memory_dir() / "MEMORY.md"
    entries = MemoryStore._read_file(path)
    assert entries == ["alpha rewritten", "beta"]
    assert path.read_text(encoding="utf-8") == ENTRY_DELIMITER.join(entries)
