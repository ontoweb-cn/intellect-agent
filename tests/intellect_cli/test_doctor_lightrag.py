"""Doctor — LightRAG RAG provider branch coverage."""

from __future__ import annotations

from unittest.mock import patch

from plugins.rag.lightrag.doctor import diagnose_lightrag_rag


def _noop_ok(text, detail=""):
    pass


def _noop_warn(text, detail=""):
    pass


def _noop_info(text):
    pass


def _fail(text, detail, fix, issues):
    issues.append(fix)


def test_diagnose_lightrag_unreachable(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        '{"server": {"base_url": "http://127.0.0.1:59999"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))

    issues: list = []
    with patch(
        "plugins.rag.lightrag.client.LightRAGClientManager.health",
        side_effect=Exception("connection refused"),
    ):
        diagnose_lightrag_rag(
            str(home),
            check_ok=_noop_ok,
            check_warn=_noop_warn,
            check_info=_noop_info,
            fail_fn=_fail,
            issues=issues,
        )
    assert issues


def test_diagnose_lightrag_ok(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        '{"server": {"base_url": "http://127.0.0.1:9621"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))

    issues: list = []
    with patch(
        "plugins.rag.lightrag.client.LightRAGClientManager.health",
        return_value={"status": "ok", "embedding_model": "text-embedding-3-large"},
    ), patch(
        "plugins.rag.lightrag.client.LightRAGClientManager.shutdown",
        return_value=None,
    ):
        diagnose_lightrag_rag(
            str(home),
            check_ok=_noop_ok,
            check_warn=_noop_warn,
            check_info=_noop_info,
            fail_fn=_fail,
            issues=issues,
        )
    assert not issues


def test_diagnose_lightrag_not_configured(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        '{"server": {"base_url": ""}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))

    issues: list = []
    diagnose_lightrag_rag(
        str(home),
        check_ok=_noop_ok,
        check_warn=_noop_warn,
        check_info=_noop_info,
        fail_fn=_fail,
        issues=issues,
    )
    assert any("base_url" in i for i in issues)
