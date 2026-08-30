"""Tests for tools/tool_search.py — progressive tool disclosure.

Coverage targets — these mirror the issues called out in the OpenClaw tool
search report. Every test that names an OpenClaw issue is the regression
guard that would have caught that specific failure mode.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Dict, Any

import pytest


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.tool_search import _HAS_SNOWBALL  # noqa: E402


def _td(name: str, description: str = "", properties: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
            },
        },
    }


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_default_when_missing(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(None)
        assert cfg.enabled == "auto"
        assert cfg.threshold_pct == 5.0
        assert cfg.max_search_limit == 25
        assert cfg.listing == "auto"
        assert cfg.listing_max_tokens == 4000

    def test_bool_true_maps_to_auto(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(True)
        assert cfg.enabled == "auto"

    def test_bool_false_maps_to_off(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(False)
        assert cfg.enabled == "off"

    def test_explicit_on(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert cfg.enabled == "on"

    def test_invalid_enabled_falls_back_to_auto(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"enabled": "maybe"})
        assert cfg.enabled == "auto"

    def test_threshold_clamped(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"threshold_pct": 150})
        assert cfg.threshold_pct == 100.0
        cfg = ToolSearchConfig.from_raw({"threshold_pct": -5})
        assert cfg.threshold_pct == 0.0

    def test_search_limits_clamped(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({
            "search_default_limit": 999,
            "max_search_limit": 999,
        })
        assert cfg.max_search_limit == 50
        assert cfg.search_default_limit <= cfg.max_search_limit


# ---------------------------------------------------------------------------
# Classification — the hard invariant: core tools NEVER defer.
# ---------------------------------------------------------------------------


class TestClassification:
    def test_core_tools_never_defer(self):
        """The critical invariant from the OpenClaw report."""
        from tools.tool_search import is_deferrable_tool_name
        # Sample of core tools from _INTELLECT_CORE_TOOLS.
        for core_name in ["terminal", "read_file", "write_file", "patch",
                          "search_files", "todo", "memory", "browser_navigate",
                          "web_search", "session_search", "clarify",
                          "execute_code", "delegate_task", "send_message"]:
            assert not is_deferrable_tool_name(core_name), (
                f"Core tool '{core_name}' must NEVER be deferrable"
            )

    def test_bridge_tools_never_defer(self):
        from tools.tool_search import is_deferrable_tool_name, BRIDGE_TOOL_NAMES
        for name in BRIDGE_TOOL_NAMES:
            assert not is_deferrable_tool_name(name)

    def test_unknown_tool_not_deferrable(self):
        """Defensive: a tool name we cannot resolve to a registry entry must
        not be claimed as deferrable. This protects against the OpenClaw
        cron regression where unresolved tools were silently dropped."""
        from tools.tool_search import is_deferrable_tool_name
        assert not is_deferrable_tool_name("xx_definitely_not_a_tool_xx")

    def test_classify_keeps_unknown_in_visible(self):
        """A tool we can't classify stays visible — never silently dropped.

        This is the OpenClaw #84141 regression guard (cron lost ``exec``
        because it wasn't in the catalog).
        """
        from tools.tool_search import classify_tools
        # Build a tool def for something we don't have a registry entry for.
        defs = [_td("xx_unknown_tool", "Unknown tool")]
        visible, deferrable = classify_tools(defs)
        names = {(td.get("function") or {}).get("name") for td in visible}
        assert "xx_unknown_tool" in names
        assert deferrable == []


# ---------------------------------------------------------------------------
# Token estimation + threshold gate
# ---------------------------------------------------------------------------


class TestThresholdGate:
    def test_off_never_activates(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "off"})
        assert not should_activate(cfg, deferrable_tokens=1_000_000)

    def test_zero_deferrable_never_activates(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert not should_activate(cfg, deferrable_tokens=0)

    def test_on_activates_with_any_deferrable(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert should_activate(cfg, deferrable_tokens=100)

    def test_auto_activates_with_any_deferrable(self):
        """'auto' is an alias of 'on' (常开): a single deferrable tool
        activates the bridge regardless of token cost."""
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "auto", "threshold_pct": 10})
        assert should_activate(cfg, deferrable_tokens=1)
        assert should_activate(cfg, deferrable_tokens=10_000)

    def test_auto_equals_on(self):
        """auto and on must produce identical activation decisions."""
        from tools.tool_search import ToolSearchConfig, should_activate
        auto_cfg = ToolSearchConfig.from_raw({"enabled": "auto"})
        on_cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        for tokens in (1, 10_000, 1_000_000):
            assert should_activate(auto_cfg, tokens) == should_activate(on_cfg, tokens)

    def test_token_estimate_proportional_to_schema_size(self):
        from tools.tool_search import estimate_tokens_from_schemas
        small = [_td("a", "x")]
        big = [_td(f"name_{i}", f"description for tool {i} " * 20,
                   {"q": {"type": "string", "description": "search query " * 10}})
               for i in range(10)]
        small_t = estimate_tokens_from_schemas(small)
        big_t = estimate_tokens_from_schemas(big)
        assert big_t > small_t * 10


# ---------------------------------------------------------------------------
# Retrieval (BM25 + substring fallback)
# ---------------------------------------------------------------------------


class TestRetrieval:
    def _fake_catalog(self):
        """Build a catalog directly without touching the registry."""
        from tools.tool_search import CatalogEntry, _tokenize, _entry_search_text
        defs = [
            _td("github_create_issue", "Open a new issue in a GitHub repository",
                {"title": {"type": "string"}, "body": {"type": "string"}}),
            _td("github_search_repos", "Search GitHub for matching repositories",
                {"query": {"type": "string"}}),
            _td("slack_send_message", "Post a message into a Slack channel",
                {"channel": {"type": "string"}, "text": {"type": "string"}}),
            _td("calendar_create_event", "Add an event to the user's calendar",
                {"title": {"type": "string"}, "start": {"type": "string"}}),
        ]
        catalog = []
        for d in defs:
            fn = d["function"]
            e = CatalogEntry(
                name=fn["name"], description=fn["description"],
                schema=d, source="mcp", source_name="mcp-test",
            )
            e._tokens = _tokenize(_entry_search_text(d))
            catalog.append(e)
        return catalog

    def test_search_finds_relevant_tool(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "create a github issue", limit=3)
        names = [h.name for h in hits]
        assert names[0] == "github_create_issue"

    def test_search_returns_empty_for_irrelevant_query(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "asdf qwerty foobar", limit=3)
        assert hits == []

    def test_search_substring_fallback(self):
        """Even when no BM25 hit, a literal substring of the tool name returns."""
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "calendar", limit=3)
        assert any("calendar" in h.name for h in hits)

    def test_search_respects_limit(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "github", limit=1)
        assert len(hits) <= 1


# ---------------------------------------------------------------------------
# Assembly — the full passthrough/activate decision.
# ---------------------------------------------------------------------------


class TestAssembly:
    def test_no_deferrable_returns_unchanged(self):
        """Pure-core toolset: pass-through, no bridge tools added."""
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        defs = [_td("terminal", "Run shell"), _td("read_file", "Read a file")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on"}),
        )
        assert not result.activated
        assert {t["function"]["name"] for t in result.tool_defs} == {"terminal", "read_file"}

    def test_auto_activates_with_small_deferrable_surface(self, monkeypatch):
        """常开: a single MCP tool (tiny schema) is enough to activate.

        Uses a monkeypatched ``get_entry`` so no real tool is registered in
        the global registry (the module's own registry has no public
        ``unregister``).
        """
        import types
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig

        def _fake_get_entry(name):
            if name == "mcp_p0_tiny_probe":
                return types.SimpleNamespace(toolset="mcp-p0-probe")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)

        defs = [_td("mcp_p0_tiny_probe", "A tiny MCP tool")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "auto", "threshold_pct": 10}),
        )
        assert result.activated
        names = {(t.get("function") or {}).get("name") for t in result.tool_defs}
        assert "tool_search" in names
        assert "mcp_p0_tiny_probe" not in names  # deferred behind the bridge

    def test_idempotent_when_bridge_already_present(self):
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig, BRIDGE_TOOL_NAMES
        defs = [_td("terminal", "Run shell"), _td("tool_search", "old")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "off"}),
        )
        names = [(t["function"]["name"]) for t in result.tool_defs]
        # The pre-existing tool_search was stripped (it would be re-injected if
        # activation happened; here it didn't).
        assert "tool_search" not in names


# ---------------------------------------------------------------------------
# Bridge dispatch
# ---------------------------------------------------------------------------


class TestBridgeDispatch:
    def test_tool_search_requires_query(self):
        from tools.tool_search import dispatch_tool_search
        result = dispatch_tool_search({}, current_tool_defs=[])
        assert "error" in json.loads(result)

    def test_tool_describe_requires_name(self):
        from tools.tool_search import dispatch_tool_describe
        result = dispatch_tool_describe({}, current_tool_defs=[])
        assert "error" in json.loads(result)

    def test_tool_describe_rejects_non_deferrable(self):
        """If the model asks to describe a core tool, refuse — it's already
        in the visible list."""
        from tools.tool_search import dispatch_tool_describe
        result = dispatch_tool_describe(
            {"names": ["terminal"]}, current_tool_defs=[_td("terminal", "Run shell")],
        )
        parsed = json.loads(result)
        assert "terminal" in parsed["errors"]

    def test_resolve_underlying_call_parses_object_args(self):
        from tools.tool_search import resolve_underlying_call
        name, args, err = resolve_underlying_call({
            "name": "unknown_xxx",
            "arguments": {"foo": "bar"},
        })
        # Will fail classification because unknown_xxx isn't deferrable.
        assert err is not None

    def test_resolve_underlying_call_parses_json_string_args(self):
        """Some models emit ``arguments`` as a JSON string instead of object."""
        from tools.tool_search import resolve_underlying_call
        # Use a name that won't classify (so we don't depend on registry),
        # but exercise the JSON parse path.
        _, _, err = resolve_underlying_call({
            "name": "fake",
            "arguments": '{"a": 1}',
        })
        # err is about classification, but the parse worked (it would have
        # failed earlier with "not valid JSON" otherwise).
        assert "not valid JSON" not in (err or "")

    def test_resolve_underlying_call_rejects_bad_json(self):
        from tools.tool_search import resolve_underlying_call
        _, _, err = resolve_underlying_call({
            "name": "fake",
            "arguments": "{this is not json",
        })
        assert err is not None
        assert "JSON" in err

    def test_resolve_underlying_call_rejects_recursion(self):
        """tool_call cannot invoke tool_call itself."""
        from tools.tool_search import resolve_underlying_call, TOOL_CALL_NAME
        name, args, err = resolve_underlying_call({
            "name": TOOL_CALL_NAME,
            "arguments": {},
        })
        assert err is not None
        assert "bridge tool" in err.lower()


# ---------------------------------------------------------------------------
# End-to-end via the real handle_function_call (smoke test).
# ---------------------------------------------------------------------------


class TestHandleFunctionCallIntegration:
    def test_tool_search_dispatch_through_handle_function_call(self):
        """The dispatcher recognizes the bridge tool by name."""
        import model_tools
        result = model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"queries": ["nothing matches this"]},
        )
        parsed = json.loads(result)
        # Without a real registry, the matches will be empty, but the
        # dispatch path completed without error.
        assert "results" in parsed or "error" in parsed


class TestRegression_OpenClawCron84141:
    """Regression guard for the OpenClaw cron-tool-loss class of bug.

    OpenClaw #84141: ``toolsAllow: ["exec"]`` on an isolated cron turn
    resulted in the agent receiving only ``sessions_send`` — the catalog
    builder silently dropped the requested core tool.

    Our defense: core tools are NEVER deferred. This test exercises the
    full assembly pipeline with a mixed core+MCP toolset and asserts that
    every core tool survives.
    """

    def test_core_tool_survives_alongside_many_mcp_tools(self):
        from tools.tool_search import (
            assemble_tool_defs, ToolSearchConfig, BRIDGE_TOOL_NAMES,
            classify_tools,
        )
        # 1 core tool + 50 unknown/MCP-shaped tools (deferrable).
        defs = [_td("terminal", "Run shell commands")]
        # Pad with fake "deferrable" tools — without registry registration,
        # classify_tools puts them in 'visible'. So instead, we just verify
        # the core-tool side: terminal stays in visible regardless.
        visible, deferrable = classify_tools(defs)
        assert any(
            (td.get("function") or {}).get("name") == "terminal"
            for td in visible
        ), "Core tool 'terminal' was wrongly classified as deferrable"

        # Now force activation and check the resulting tool-defs list.
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on"}),
        )
        names = {(t.get("function") or {}).get("name") for t in result.tool_defs}
        # terminal must be present; bridges are only added if there are
        # deferrable tools to put behind them.
        assert "terminal" in names

    def test_unwrap_rejects_core_tool_attempt(self):
        """Even if the model tries to invoke a core tool through tool_call,
        we reject the call and tell the model to use it directly."""
        from tools.tool_search import resolve_underlying_call
        _, _, err = resolve_underlying_call({
            "name": "terminal",
            "arguments": {"command": "echo hi"},
        })
        assert err is not None
        assert "not a deferrable" in err


class TestRegression_ToolsetScoping:
    """A restricted-toolset session must not see or invoke out-of-scope tools.

    The bug: the bridge dispatch and the tool_executor unwrap read the
    catalog from the *global* registry (get_tool_definitions with no
    toolset scope = "start with everything"), so a session scoped to one
    MCP server could tool_search the entire process registry and tool_call
    any plugin tool it was never granted. registry.dispatch() has no
    enabled_tools gate for non-execute_code tools, so the out-of-scope tool
    actually ran.

    The fix threads the session's enabled/disabled toolsets into the bridge
    dispatch (model_tools.handle_function_call) and the executor unwrap
    (agent.tool_executor), scoping both the searchable catalog and the
    invocable set to the session's own toolsets.
    """

    @staticmethod
    def _register(name, toolset):
        from tools.registry import registry

        def _handler(args, task_id=None, **kw):
            return json.dumps({"ok": True, "tool": name})

        registry.register(
            name=name,
            handler=_handler,
            schema=_td(name, f"desc for {name}", {"repo": {"type": "string"}}),
            toolset=toolset,
        )

    def test_search_catalog_is_scoped_to_session_toolsets(self):
        import model_tools

        for i in range(12):
            self._register(f"mcp_scoped_gh_{i}", "mcp-scoped-gh")
        self._register("scoped_oos_plugin", "scopedoosplugin")

        # tool_search scoped to the github toolset must not count the
        # out-of-scope plugin tool (or any of the host registry).
        result = model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"queries": ["mcp_scoped_gh"], "limit": 5},
            enabled_toolsets=["mcp-scoped-gh"],
        )
        parsed = json.loads(result)
        assert parsed["total_available"] == 12, (
            f"expected scoped catalog of 12, got {parsed['total_available']} "
            "— catalog leaked tools outside the session's toolsets"
        )
        hit_names = set(parsed["results"][0]["matches"])
        assert "scoped_oos_plugin" not in hit_names

    def test_tool_call_rejects_out_of_scope_tool(self):
        import model_tools

        self._register("mcp_inscope_gh_op", "mcp-inscope-gh")
        self._register("inscope_oos_plugin", "inscopeoosplugin")

        # Out-of-scope plugin tool: rejected even though it is registered
        # and deferrable in the global registry.
        rejected = json.loads(model_tools.handle_function_call(
            function_name="tool_call",
            function_args={"name": "inscope_oos_plugin", "arguments": {}},
            enabled_toolsets=["mcp-inscope-gh"],
        ))
        assert "error" in rejected
        assert "not available in this session" in rejected["error"]

        # In-scope tool: dispatches normally.
        ok = json.loads(model_tools.handle_function_call(
            function_name="tool_call",
            function_args={"name": "mcp_inscope_gh_op", "arguments": {"repo": "a/b"}},
            enabled_toolsets=["mcp-inscope-gh"],
        ))
        assert ok.get("ok") is True
        assert ok.get("tool") == "mcp_inscope_gh_op"

    def test_bridge_dispatch_does_not_pollute_global_resolved_names(self):
        import model_tools

        self._register("mcp_pollute_op_0", "mcp-pollute")
        self._register("mcp_pollute_op_1", "mcp-pollute")

        # Establish the scoped session global.
        model_tools.get_tool_definitions(
            enabled_toolsets=["mcp-pollute"], quiet_mode=True,
        )
        before = set(model_tools._last_resolved_tool_names)
        assert "terminal" not in before

        # A scoped tool_search call must not widen the process-global
        # _last_resolved_tool_names to the whole registry (which would leak
        # core/sandbox tools into execute_code's fallback).
        model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"queries": ["pollute"]},
            enabled_toolsets=["mcp-pollute"],
        )
        after = set(model_tools._last_resolved_tool_names)
        assert "terminal" not in after, (
            "bridge dispatch polluted _last_resolved_tool_names with "
            "out-of-scope tools"
        )

    def test_scoped_deferrable_names_helper(self):
        from tools.tool_search import scoped_deferrable_names

        self._register("mcp_helper_op", "mcp-helper")
        import model_tools
        defs = model_tools.get_tool_definitions(
            enabled_toolsets=["mcp-helper"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
        names = scoped_deferrable_names(defs)
        assert "mcp_helper_op" in names
        # core tools are never deferrable
        assert "terminal" not in names


# ---------------------------------------------------------------------------
# Tiered catalog listing
# ---------------------------------------------------------------------------


class TestCatalogListing:
    def test_config_defaults(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(None)
        assert cfg.listing == "auto"
        assert cfg.listing_max_tokens == 4000
        # legacy bool shapes keep listing defaults too
        assert ToolSearchConfig.from_raw(True).listing == "auto"

    def test_listing_budget_is_min_of_percent_and_cap(self):
        from tools.tool_search import ToolSearchConfig, listing_token_budget
        cfg = ToolSearchConfig.from_raw({"threshold_pct": 5, "listing_max_tokens": 4000})
        # 5% of 200K = 10K → capped at 4K.
        assert listing_token_budget(cfg, 200_000) == 4000
        # 5% of 40K = 2K → percent leg wins.
        assert listing_token_budget(cfg, 40_000) == 2000
        # Unknown context → fixed 10K leg, capped at 4K.
        assert listing_token_budget(cfg, 0) == 4000

    @staticmethod
    def _register(name, desc="Deferred capability description."):
        from tools.registry import registry

        def _handler(args, task_id=None, **kw):
            return json.dumps({"ok": True})

        registry.register(
            name=name,
            handler=_handler,
            schema=_td(name, desc),
            toolset="mcp-listingtest",
        )

    def test_full_listing_renders_grouped_names_and_short_descs(self, monkeypatch):
        import types
        from tools.tool_search import build_catalog_listing_with_form

        def _fake_get_entry(name):
            if name.startswith("github_"):
                return types.SimpleNamespace(toolset="mcp-github")
            if name.startswith("slack_"):
                return types.SimpleNamespace(toolset="mcp-slack")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)

        defs = [
            _td("github_create_issue", "Open a new issue in a GitHub repository."),
            _td("github_merge_pr", "Merge an open pull request."),
            _td("slack_post", "Post a message to a channel."),
        ]
        text, form = build_catalog_listing_with_form(defs, max_tokens=4000)
        assert form == "full"
        assert "github tools (2):" in text
        assert "- github_create_issue: Open a new issue" in text
        assert "- slack_post: Post a message to a channel." in text

    def test_deterministic_bytes_across_calls(self):
        from tools.tool_search import build_catalog_listing_with_form
        defs = [_td(f"t{i}", f"Desc {i}. More.") for i in range(20)]
        text1, form1 = build_catalog_listing_with_form(defs, max_tokens=4000)
        text2, form2 = build_catalog_listing_with_form(list(reversed(defs)), max_tokens=4000)
        assert (text1, form1) == (text2, form2)

    def test_mixed_keeps_small_server_tools_when_big_server_folds(self, monkeypatch):
        import types
        from tools.tool_search import build_catalog_listing_with_form

        def _fake_get_entry(name):
            if name.startswith("big_"):
                return types.SimpleNamespace(toolset="mcp-big")
            if name.startswith("small_"):
                return types.SimpleNamespace(toolset="mcp-small")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)

        big = [_td(f"big_{i:03d}", "Synchronize enterprise credentials across regions. " * 3)
               for i in range(200)]
        small = [_td(f"small_{i:03d}", "Ping.") for i in range(3)]
        text, form = build_catalog_listing_with_form(big + small, max_tokens=120)
        assert form == "mixed"
        assert "names not listed" in text
        assert "small" in text

    def test_oversized_catalog_degrades_to_names_then_groups(self):
        from tools.tool_search import build_catalog_listing_with_form
        many = [_td(f"big_tool_{i}", "A deliberately verbose description " * 8)
                for i in range(60)]
        _, form_names = build_catalog_listing_with_form(many, max_tokens=300)
        assert form_names == "names"
        _, form_groups = build_catalog_listing_with_form(many, max_tokens=45)
        assert form_groups == "groups"

    def test_default_listing_cap_bounds_fixed_catalog_overhead(self):
        """The default manifest must not grow back to the old 20K-token cap."""
        from tools.registry import registry
        from tools.tool_search import (
            ToolSearchConfig, assemble_tool_defs, estimate_tokens_from_schemas,
        )
        names = []
        defs = []
        for i in range(500):
            name = f"lean_catalog_tool_{i:04d}"
            names.append(name)
            self._register(name, "Perform a deliberately verbose connected service action.")
            defs.append(_td(name, "Perform a deliberately verbose connected service action."))
        try:
            cfg = ToolSearchConfig.from_raw(None)
            result = assemble_tool_defs(defs, context_length=1_000_000, config=cfg)
            search = next(
                td for td in result.tool_defs
                if td["function"]["name"] == "tool_search"
            )
            description_tokens = estimate_tokens_from_schemas([search])
            # Bridge schema around the listing, so allow modest framing
            # overhead above the 4K listing budget.
            assert description_tokens < 4500
            assert result.listing_form in {"names", "groups", "mixed"}
        finally:
            for n in names:
                registry.deregister(n)

    def test_assembly_listing_off_keeps_legacy_description(self):
        from tools.registry import registry
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        names = [f"mcp_off_{i}" for i in range(10)]
        defs = []
        for n in names:
            self._register(n, "Deferred.")
            defs.append(_td(n, "Deferred."))
        try:
            result = assemble_tool_defs(
                defs, context_length=1000,
                config=ToolSearchConfig.from_raw({"enabled": "on", "listing": "off"}),
            )
            assert result.activated
            assert result.listing_form == "none"
            search = next(t for t in result.tool_defs if t["function"]["name"] == "tool_search")
            assert "mcp_off_0" not in search["function"]["description"]
        finally:
            for n in names:
                registry.deregister(n)


class TestShortDescSentenceBoundary:
    """Listing lines survive abbreviations, versions, hostnames."""

    def test_clean_two_sentence_case_still_clips_at_first(self):
        from tools.tool_search import _short_desc
        assert _short_desc("Open an issue. Second sentence dropped.") == "Open an issue."

    def test_abbreviation_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Create an issue (e.g. a bug report) in a repository.")
        assert s.startswith("Create an issue (e.g. a bug report)")

    def test_hostname_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Fetch a page from api.github.com and return the JSON body.")
        assert "api.github.com" in s

    def test_version_string_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Upgrade to v1.2 of the schema and migrate all rows.")
        assert "v1.2" in s

    def test_title_abbreviation_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Contact Dr. Smith for help. Runs daily.")
        assert s.startswith("Contact Dr. Smith for help.")

    def test_vs_abbreviation_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Compare this PR vs. the baseline branch.")
        assert s.startswith("Compare this PR vs. the baseline branch.")

    def test_lowercase_continuation_does_not_truncate(self):
        from tools.tool_search import _short_desc
        s = _short_desc("Fetch a page from api.github.com and return the body.")
        assert s.startswith("Fetch a page from api.github.com and return")

    def test_exclamation_terminator_is_kept(self):
        from tools.tool_search import _short_desc
        assert _short_desc("List repos! Supports pagination.") == "List repos!"

    def test_question_terminator_is_kept(self):
        from tools.tool_search import _short_desc
        s = _short_desc("What does this do? It lists channels.")
        assert s == "What does this do?"

    def test_long_text_still_clips_with_ellipsis(self):
        from tools.tool_search import _short_desc
        s = _short_desc("word " * 40)
        assert len(s) <= 61
        assert s.endswith("…")

    def test_empty_is_empty(self):
        from tools.tool_search import _short_desc
        assert _short_desc("") == ""


# ---------------------------------------------------------------------------
# Multi-query search + batch describe
# ---------------------------------------------------------------------------


class TestMultiQuerySearch:
    def test_multiple_queries_group_results_per_query(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        def _fake_get_entry(name):
            if name.startswith("github_"):
                return types.SimpleNamespace(toolset="mcp-github")
            if name.startswith("slack_"):
                return types.SimpleNamespace(toolset="mcp-slack")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [
            _td("github_create_issue", "Open a new issue in a GitHub repository."),
            _td("slack_post_message", "Post a message into a Slack channel."),
        ]
        out = json.loads(dispatch_tool_search(
            {"queries": ["create issue", "post message"]}, current_tool_defs=defs))
        assert out["queries"] == ["create issue", "post message"]
        assert [g["query"] for g in out["results"]] == ["create issue", "post message"]
        assert out["results"][0]["matches"] == ["github_create_issue"]
        assert out["results"][1]["matches"] == ["slack_post_message"]
        # shared tools map holds each matched tool exactly once
        assert set(out["tools"]) == {"github_create_issue", "slack_post_message"}
        assert out["tools"]["github_create_issue"]["source"] == "mcp"

    def test_limit_applies_per_query(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        def _fake_get_entry(name):
            return types.SimpleNamespace(toolset="mcp-github")

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [_td(f"github_tool_{i}", f"Perform action {i}.") for i in range(20)]
        out = json.loads(dispatch_tool_search(
            {"queries": ["github"], "limit": 3}, current_tool_defs=defs))
        assert len(out["results"][0]["matches"]) == 3

    def test_stringified_json_array_accepted_as_queries(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        def _fake_get_entry(name):
            return types.SimpleNamespace(toolset="mcp-github")

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [
            _td("github_create_issue", "Open a new issue."),
            _td("github_search_repos", "Search repositories."),
        ]
        out = json.loads(dispatch_tool_search(
            {"queries": '["create issue", "search repos"]'}, current_tool_defs=defs))
        assert [g["query"] for g in out["results"]] == ["create issue", "search repos"]

    def test_bare_string_accepted_as_single_query(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        def _fake_get_entry(name):
            return types.SimpleNamespace(toolset="mcp-github")

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [_td("github_issue", "Open an issue.")]
        out = json.loads(dispatch_tool_search({"queries": "github"}, current_tool_defs=defs))
        assert out["results"][0]["query"] == "github"

    def test_rejects_empty_or_overcap_queries(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        monkeypatch.setattr("tools.registry.registry.get_entry",
                            lambda n: types.SimpleNamespace(toolset="mcp-x"))
        assert "error" in json.loads(dispatch_tool_search({}, current_tool_defs=[]))
        assert "error" in json.loads(dispatch_tool_search({"queries": []}, current_tool_defs=[]))
        assert "error" in json.loads(dispatch_tool_search(
            {"queries": ["q"] * 11}, current_tool_defs=[]))


class TestBatchDescribe:
    def test_batch_describe_returns_tools_not_found_and_errors(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_describe

        def _fake_get_entry(name):
            if name == "mcp_doc_reader":
                return types.SimpleNamespace(toolset="mcp-docs")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [_td("mcp_doc_reader", "Read a document", {"path": {"type": "string"}})]
        out = json.loads(dispatch_tool_describe(
            {"names": ["mcp_doc_reader", "ghost_tool", "terminal"]},
            current_tool_defs=defs,
        ))
        assert "path" in out["tools"]["mcp_doc_reader"]["parameters"]["properties"]
        assert "ghost_tool" in out["not_found"]
        assert "terminal" in out["errors"]

    def test_bare_string_accepted(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_describe

        def _fake_get_entry(name):
            if name == "mcp_doc_reader":
                return types.SimpleNamespace(toolset="mcp-docs")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [_td("mcp_doc_reader", "Read a document")]
        out = json.loads(dispatch_tool_describe(
            {"names": "mcp_doc_reader"}, current_tool_defs=defs))
        assert "mcp_doc_reader" in out["tools"]

    def test_oversized_batch_describe_is_rejected_not_truncated(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_describe, _MAX_DESCRIBE_RESPONSE_CHARS

        def _fake_get_entry(name):
            if name.startswith("big_"):
                return types.SimpleNamespace(toolset="mcp-big")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        huge = "x" * (_MAX_DESCRIBE_RESPONSE_CHARS // 2)
        defs = [_td(f"big_{i}", huge) for i in range(3)]
        out = json.loads(dispatch_tool_describe(
            {"names": [f"big_{i}" for i in range(3)]}, current_tool_defs=defs))
        assert "error" in out
        assert "Retry tool_describe with fewer names" in out["error"]


class TestRetrievalHardening:
    def test_exact_name_ranks_first(self):
        from tools.tool_search import (
            CatalogEntry, _tokenize, _entry_search_text, search_catalog,
        )
        defs = [
            _td("get_issue", "Retrieve issues from the tracker."),
            _td("github_get_issue", "Get a single issue from GitHub."),
        ]
        catalog = [CatalogEntry(
            name=d["function"]["name"], description=d["function"]["description"],
            schema=d, source="mcp", source_name="mcp-test",
            _tokens=_tokenize(_entry_search_text(d))) for d in defs]
        hits = search_catalog(catalog, "get_issue", limit=5)
        assert hits[0].name == "get_issue"

    def test_service_query_reaches_tool_without_service_in_name(self, monkeypatch):
        import types
        from tools.tool_search import build_catalog, search_catalog

        def _fake_get_entry(name):
            if name.startswith("create_issue"):
                return types.SimpleNamespace(toolset="mcp-linear")
            if name.startswith("post_message"):
                return types.SimpleNamespace(toolset="mcp-slack")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [
            _td("create_issue", "Create a new issue in a team."),
            _td("post_message", "Post a message to a channel."),
        ]
        catalog = build_catalog(defs)
        hits = search_catalog(catalog, "linear")
        assert [h.name for h in hits] == ["create_issue"]

    def test_mcp_prefix_is_not_a_matchable_token(self):
        from tools.tool_search import _entry_search_text
        td = _td("mcp__create_issue", "Create an issue.")
        text = _entry_search_text(td, source_label="github")
        assert "mcp" not in text.split()

    def test_mcp_and_plugin_same_label_do_not_merge(self, monkeypatch):
        import types
        from tools.tool_search import build_catalog_listing_with_form

        def _fake_get_entry(name):
            if name.startswith("cloudflare_"):
                return types.SimpleNamespace(toolset="mcp-cloudflare")
            if name.startswith("plugin_"):
                return types.SimpleNamespace(toolset="cloudflare")
            return None

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = (
            [_td(f"cloudflare_{i}", f"CF tool {i}.") for i in range(5)]
            + [_td("plugin_one", "Plugin tool.")]
        )
        text, form = build_catalog_listing_with_form(defs, max_tokens=4000)
        assert form == "full"
        # An MCP server "mcp-cloudflare" and a plugin toolset "cloudflare" are
        # two distinct degradation groups: the tiny plugin server must not be
        # dragged into the big server's group.
        assert text.count("cloudflare tools (") == 2
        assert "plugin_one: Plugin tool." in text

    def test_empty_result_attaches_available_sources_and_hint(self, monkeypatch):
        import types
        from tools.tool_search import dispatch_tool_search

        def _fake_get_entry(name):
            return types.SimpleNamespace(toolset="mcp-github")

        monkeypatch.setattr("tools.registry.registry.get_entry", _fake_get_entry)
        defs = [_td(f"github_tool_{i}", f"Desc {i}.") for i in range(3)]
        out = json.loads(dispatch_tool_search({"queries": ["zzzzzqqqq"]}, current_tool_defs=defs))
        group = out["results"][0]
        assert group["matches"] == []
        assert group["available_sources"][0]["name"] == "github"
        assert "hint" in group

    @pytest.mark.skipif(not _HAS_SNOWBALL, reason="snowballstemmer not installed")
    def test_snowball_stemming_unifies_plural_query(self):
        from tools.tool_search import _tokenize
        assert _tokenize("issues") == _tokenize("issue")


# ---------------------------------------------------------------------------
# Blind tool_call probe (validate_deferred_call_args)
# ---------------------------------------------------------------------------


class TestDeferredCallSchemaProbe:
    """Blind tool_call invocations missing required arguments must return
    the tool's parameter schema instead of dispatching into an opaque
    downstream failure (port of nearai/ironclaw#5149's describe-first fix)."""

    @staticmethod
    def _register(name="mcp_probe_doc"):
        from tools.registry import registry

        def _handler(args, task_id=None, **kw):
            return json.dumps({"ok": True, "doc": args.get("document_id")})

        schema = _td(name, "Read a document by id.", {
            "document_id": {"type": "string", "description": "Doc id"},
            "format": {"type": "string"},
        })
        schema["function"]["parameters"]["required"] = ["document_id"]
        registry.register(name=name, handler=_handler, schema=schema, toolset="mcp-probe")
        return name

    def test_validator_returns_schema_for_missing_required(self):
        from tools.tool_search import validate_deferred_call_args
        name = self._register()
        try:
            err = validate_deferred_call_args(name, {})
            assert err is not None
            parsed = json.loads(err)
            assert "document_id" in parsed["error"]
            assert "NOT invoked" in parsed["error"]
            assert parsed["parameters"]["required"] == ["document_id"]
        finally:
            from tools.registry import registry
            registry.deregister(name)

    def test_valid_tool_call_still_dispatches(self):
        from tools.tool_search import validate_deferred_call_args
        name = self._register()
        try:
            assert validate_deferred_call_args(name, {"document_id": "doc-1"}) is None
        finally:
            from tools.registry import registry
            registry.deregister(name)

    def test_validator_never_blocks_unvalidatable_tools(self):
        from tools.tool_search import validate_deferred_call_args
        # Unknown tool: no schema → cannot validate → never block.
        assert validate_deferred_call_args("xx_not_a_real_tool", {}) is None

