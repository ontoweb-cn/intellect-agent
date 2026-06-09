"""RAG plugin discovery tests."""

from __future__ import annotations


def test_discover_rag_providers_lists_lightrag():
    from plugins.rag import discover_rag_providers

    names = [n for n, _, _ in discover_rag_providers()]
    assert "lightrag" in names


def test_load_lightrag_provider():
    from plugins.rag import load_rag_provider

    provider = load_rag_provider("lightrag")
    assert provider is not None
    assert provider.name == "lightrag"
    assert len(provider.get_tool_schemas()) == 7


def test_plugin_manager_records_rag_kind_without_import(tmp_path, monkeypatch):
    """RAG plugins are recorded by PluginManager but not loaded (plugins/rag/)."""
    from intellect_cli import plugins as plugin_mod

    intellect_home = tmp_path / ".intellect"
    intellect_home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(intellect_home))

    user_plugin = intellect_home / "plugins" / "test-rag-provider"
    user_plugin.mkdir(parents=True)
    (user_plugin / "plugin.yaml").write_text(
        "name: test-rag-provider\n"
        "kind: rag\n"
        "version: 0.0.1\n"
    )
    (user_plugin / "__init__.py").write_text(
        "raise AssertionError('rag plugins must not be imported by PluginManager')\n"
    )

    manager = plugin_mod.PluginManager()
    manager.discover_and_load(force=True)

    loaded = manager._plugins.get("test-rag-provider")
    assert loaded is not None
    assert loaded.manifest.kind == "rag"
    assert loaded.enabled is True
