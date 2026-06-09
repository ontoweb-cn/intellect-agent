"""``intellect graphiti`` CLI.

Subcommands:
  setup   — delegates to the unified ``intellect memory setup`` flow
  status  — availability, config target, and (if deps installed)
            a FalkorDB ping per scope graph
  stats   — node/edge counts per scope graph

Phase 3 will add ``mcp`` (deferred — separate release).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .config import load_config
from .tools import SCOPE_CHOICES


def _cmd_setup(args) -> None:
    print("\n  Graphiti is configured via the memory provider system.")
    print("  Running 'intellect memory setup'...\n")
    from intellect_cli.memory_setup import cmd_setup_provider

    cmd_setup_provider("graphiti")


def _build_manager_for_cli() -> Any:
    """Build a GraphitiClientManager bound to whatever scope the active
    profile / member resolves to.  Returns None if deps are missing.
    """
    try:
        from .client import GraphitiClientManager
    except ImportError:
        return None
    try:
        # CLI invocation has no agent runtime_context; derive member_id
        # from the CLI session if present (best-effort), else stay global.
        member_id: str = ""
        try:
            from intellect_cli.member_session import current_member_id
            member_id = current_member_id() or ""
        except Exception:
            pass

        mgr = GraphitiClientManager(load_config())
        mgr.bind_scope(
            member_id=member_id or None,
            team_id=None,
            project_id=None,
        )
        return mgr
    except Exception:
        return None


def _cmd_status(args) -> None:
    from . import GraphitiMemoryProvider

    provider = GraphitiMemoryProvider()
    available = provider.is_available()
    cfg = load_config()
    intellect_home = os.environ.get(
        "INTELLECT_HOME", str(Path.home() / ".intellect")
    )
    config_path = Path(intellect_home) / "graphiti" / "config.json"

    pings: Dict[str, bool] = {}
    if available:
        mgr = _build_manager_for_cli()
        if mgr is not None:
            try:
                pings = mgr.ping()
            finally:
                try:
                    mgr.shutdown()
                except Exception:
                    pass

    status = {
        "plugin": "graphiti",
        "available": available,
        "missing_deps": (
            [] if available else ["graphiti-core[falkordb]", "falkordb"]
        ),
        "backend": {
            "host": cfg.get("falkordb_host"),
            "port": cfg.get("falkordb_port"),
        },
        "config_path": str(config_path),
        "reachable_graphs": pings,
    }

    if getattr(args, "json", False):
        print(json.dumps(status, indent=2))
        return

    print("\n  Graphiti memory plugin")
    print(f"    available:  {available}")
    if not available:
        print("    install:    uv pip install 'intellect-agent[graphiti]'")
    print(f"    backend:    {status['backend']['host']}:{status['backend']['port']}")
    print(f"    config:     {config_path}")
    if pings:
        print("    graphs:")
        for g, ok in pings.items():
            marker = "✓" if ok else "✗"
            print(f"      {marker} {g}")
    print()


def _cmd_stats(args) -> None:
    mgr = _build_manager_for_cli()
    if mgr is None:
        print("  graphiti not available — install with:")
        print("    uv pip install 'intellect-agent[graphiti]'")
        return
    try:
        data = mgr.stats()
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass

    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return

    print("\n  Graphiti graph statistics\n")
    for g, info in (data.get("graphs") or {}).items():
        if "error" in info:
            print(f"    {g}: error — {info['error']}")
            continue
        nodes = info.get("nodes", "?")
        edges = info.get("edges", "?")
        print(f"    {g}: {nodes} nodes, {edges} edges")
    breakers = data.get("circuit_breakers") or {}
    if any(b.get("open") for b in breakers.values()):
        print("\n  circuit breakers OPEN:")
        for g, b in breakers.items():
            if b.get("open"):
                print(f"    {g}: failures={b['failures']}")
    print()


def _cmd_timeline(args) -> None:
    """Render the bi-temporal timeline for one entity node.

    Calls graphiti_get_node_timeline under the hood and pipes the raw
    records through plugins/memory/graphiti/timeline.py.
    """
    mgr = _build_manager_for_cli()
    if mgr is None:
        print("  graphiti not available — install with:")
        print("    uv pip install 'intellect-agent[graphiti]'")
        return
    try:
        records = mgr.get_node_timeline(
            args.node_id,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
        )
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass

    from .timeline import render_timeline_json, render_timeline_text

    if getattr(args, "json", False):
        print(render_timeline_json(records, node_id=args.node_id))
        return
    print(render_timeline_text(records, node_id=args.node_id))


def _cmd_dump(args) -> None:
    """Cypher-level export of every graph in the selected scope.

    Writes one JSON-lines file per graph under ``--out``.  Path defaults
    to ``$INTELLECT_HOME/graphiti/dumps/<YYYYMMDD-HHMMSS>/``.  Format
    is portable across FalkorDB and Neo4j (no RDB snapshot
    dependency); see GraphitiClient._dump.
    """
    import datetime as _dt
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    mgr = _build_manager_for_cli()
    if mgr is None:
        print("  graphiti not available — install with:")
        print("    uv pip install 'intellect-agent[graphiti]'")
        return

    out_dir = getattr(args, "out", None)
    if not out_dir:
        intellect_home = _os.environ.get(
            "INTELLECT_HOME", str(_Path.home() / ".intellect")
        )
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = _Path(intellect_home) / "graphiti" / "dumps" / stamp
    out_path = _Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    scope = getattr(args, "scope", "all")
    try:
        result = mgr.dump(scope=scope)
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass

    summary: dict = {}
    for graph, payload in result.items():
        if payload.get("error"):
            summary[graph] = {"error": payload["error"]}
            continue
        # One JSON-lines file per graph: first line is a header record,
        # then one node record per line, then one edge record per line.
        path = out_path / f"{graph}.jsonl"
        nodes = payload.get("nodes") or []
        edges = payload.get("edges") or []
        with open(path, "w", encoding="utf-8") as f:
            header = {
                "kind": "header",
                "graph": graph,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "format_version": 1,
            }
            f.write(_json.dumps(header) + "\n")
            for n in nodes:
                f.write(_json.dumps({"kind": "node", **n}) + "\n")
            for e in edges:
                f.write(_json.dumps({"kind": "edge", **e}) + "\n")
        summary[graph] = {
            "path": str(path),
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    if getattr(args, "json", False):
        print(_json.dumps({"out_dir": str(out_path), "graphs": summary}, indent=2))
        return

    print(f"\n  Graphiti dump → {out_path}\n")
    for graph, info in summary.items():
        if info.get("error"):
            print(f"    {graph}: error — {info['error']}")
        else:
            print(
                f"    {graph}: {info['node_count']} nodes, "
                f"{info['edge_count']} edges → {info['path']}"
            )
    print()


def _cmd_rebuild_communities(args) -> None:
    """Re-cluster nodes into communities for the selected scope graphs.

    Expensive: touches every node in each graph.  Intended for cron /
    scheduled runs; see docs/plans/graphiti-memory-plugin-dev-plan.md
    §6 (Phase 5.5).  Defaults to ``--scope all``; pass ``--scope team``
    or similar to limit the work.
    """
    mgr = _build_manager_for_cli()
    if mgr is None:
        print("  graphiti not available — install with:")
        print("    uv pip install 'intellect-agent[graphiti]'")
        return
    scope = getattr(args, "scope", "all")
    try:
        result = mgr.rebuild_communities(scope=scope)
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return

    print(f"\n  Graphiti community rebuild (scope={scope})\n")
    for graph, info in result.items():
        if info.get("built"):
            print(
                f"    {graph}: {info.get('community_count', 0)} communities, "
                f"{info.get('community_edge_count', 0)} community edges"
            )
        else:
            reason = info.get("error") or info.get("reason") or "unknown"
            print(f"    {graph}: not rebuilt ({reason})")
    print()


def _cmd_mcp_start(args) -> None:
    """Start Graphiti as an MCP stdio server.

    The MCP SDK takes over stdin/stdout — do NOT print to stdout/stderr
    after the server starts; use ``print(..., file=sys.stderr)`` for any
    startup diagnostics.
    """
    import asyncio as _asyncio
    import sys as _sys

    scope = getattr(args, "scope", "auto")
    if scope not in ("auto", "member", "team", "project", "all"):
        print(f"  invalid --scope: {scope}", file=_sys.stderr)
        _sys.exit(1)

    from .mcp_server import start_mcp_server

    try:
        _asyncio.run(start_mcp_server(scope=scope))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"  graphiti mcp start failed: {exc}", file=_sys.stderr)
        _sys.exit(1)


def _cmd_mcp_config(args) -> None:
    """Print MCP client configuration snippets."""
    scope = getattr(args, "scope", "auto")
    from .mcp_server import render_mcp_config

    print(render_mcp_config(scope=scope))


def graphiti_command(args) -> None:
    sub = getattr(args, "graphiti_command", None)
    if sub == "setup":
        _cmd_setup(args)
    elif sub == "stats":
        _cmd_stats(args)
    elif sub == "rebuild-communities":
        _cmd_rebuild_communities(args)
    elif sub == "timeline":
        _cmd_timeline(args)
    elif sub == "dump":
        _cmd_dump(args)
    elif sub == "mcp":
        mcp_sub = getattr(args, "graphiti_mcp_command", None)
        if mcp_sub == "start":
            _cmd_mcp_start(args)
        elif mcp_sub == "config":
            _cmd_mcp_config(args)
        else:
            print(f"  Unknown graphiti mcp command: {mcp_sub}")
    elif sub in (None, "status"):
        _cmd_status(args)
    else:
        print(f"  Unknown graphiti command: {sub}")


def register_cli(subparser) -> None:
    """Build the ``intellect graphiti`` subcommand tree.

    Only registered when graphiti is the ACTIVE memory provider; see
    ``plugins/memory/__init__.py:discover_plugin_cli_commands``.
    """
    subs = subparser.add_subparsers(dest="graphiti_command")

    setup = subs.add_parser("setup", help="Configure Graphiti (opens memory setup)")
    setup.set_defaults(func=graphiti_command)

    status = subs.add_parser("status", help="Availability + FalkorDB ping")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=graphiti_command)

    stats = subs.add_parser("stats", help="Node/edge counts per scope graph")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=graphiti_command)

    rebuild = subs.add_parser(
        "rebuild-communities",
        help="Re-cluster nodes into communities (expensive; cron-friendly)",
    )
    rebuild.add_argument(
        "--scope",
        choices=list(SCOPE_CHOICES),
        default="all",
        help="Which scope graphs to rebuild (default: all)",
    )
    rebuild.add_argument("--json", action="store_true")
    rebuild.set_defaults(func=graphiti_command)

    timeline = subs.add_parser(
        "timeline",
        help="Render bi-temporal timeline for an entity node",
    )
    timeline.add_argument("node_id", help="Entity node UUID")
    timeline.add_argument(
        "--since",
        help="ISO-8601 lower bound on valid_at (e.g. 2024-01-01)",
    )
    timeline.add_argument(
        "--until",
        help="ISO-8601 upper bound on valid_at",
    )
    timeline.add_argument("--json", action="store_true")
    timeline.set_defaults(func=graphiti_command)

    dump = subs.add_parser(
        "dump",
        help="Export graphs to JSON-lines (cron-friendly; FalkorDB+Neo4j portable)",
    )
    dump.add_argument(
        "--scope",
        choices=list(SCOPE_CHOICES),
        default="all",
        help="Which scope graphs to dump (default: all)",
    )
    dump.add_argument(
        "--out",
        help="Output directory (default: $INTELLECT_HOME/graphiti/dumps/<timestamp>/)",
    )
    dump.add_argument("--json", action="store_true")
    dump.set_defaults(func=graphiti_command)

    # MCP bridge subcommand group (Phase 3)
    mcp_group = subs.add_parser(
        "mcp",
        help="MCP (Model Context Protocol) bridge — serve graphiti tools over stdio",
    )
    mcp_subs = mcp_group.add_subparsers(dest="graphiti_mcp_command")

    mcp_start = mcp_subs.add_parser(
        "start",
        help="Start graphiti as an MCP stdio server (for Cursor / Claude Desktop / VS Code)",
    )
    mcp_start.add_argument(
        "--scope",
        choices=list(SCOPE_CHOICES),
        default="auto",
        help="Which scope graphs to expose (default: auto = member + team)",
    )
    mcp_start.set_defaults(func=graphiti_command, graphiti_command="mcp")

    mcp_config = mcp_subs.add_parser(
        "config",
        help="Print MCP client configuration snippets for Cursor / Claude Desktop / VS Code",
    )
    mcp_config.add_argument(
        "--scope",
        choices=list(SCOPE_CHOICES),
        default="auto",
        help="Scope to include in the config snippet (default: auto)",
    )
    mcp_config.set_defaults(func=graphiti_command, graphiti_command="mcp")

    subparser.set_defaults(func=graphiti_command)
