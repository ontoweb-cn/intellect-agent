"""``intellect lightrag`` CLI — setup and status."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import load_config, save_config


def _intellect_home() -> str:
    return os.environ.get("INTELLECT_HOME", str(Path.home() / ".intellect"))


def _cmd_setup(args) -> None:
    from intellect_constants import display_intellect_home

    home = _intellect_home()
    print(f"\n  LightRAG setup ({display_intellect_home()}/lightrag/config.json)\n")
    base_url = input("  Server URL [http://127.0.0.1:9621]: ").strip()
    if not base_url:
        base_url = "http://127.0.0.1:9621"

    save_config({"server": {"base_url": base_url}}, home)
    cfg = load_config(home)

    try:
        from .client import LightRAGClientManager
        mgr = LightRAGClientManager(cfg)
        health = mgr.health()
        mgr.shutdown()
        print(f"  Health: OK — {health.get('status', 'ok')}")
    except Exception as exc:
        print(f"  Health check failed: {exc}")
        print("  Config saved; fix server and run `intellect lightrag status`.")

    opt = input("\n  Enable conversation summary ingest? [y/N]: ").strip().lower()
    if opt in ("y", "yes"):
        save_config({"ingest": {"auto_mode": "summary"}}, home)
        print("  ingest.auto_mode set to summary (per-turn auxiliary summary ingest).")
    else:
        print("  ingest.auto_mode remains off (recommended).")
    print(
        "\n  Tip: generate LightRAG server .env from your Intellect model with:\n"
        "       intellect lightrag sync-server-env [--docker]\n"
    )


def _cmd_sync_server_env(args) -> None:
    from intellect_constants import display_intellect_home

    from .sync_env import default_output_path, write_server_env

    out = getattr(args, "output", None) or ""
    if out:
        output = Path(out)
        path_reason = "explicit --output"
    else:
        output, path_reason = default_output_path()
    embedding_model = getattr(args, "embedding_model", "") or ""
    for_docker = bool(getattr(args, "docker", False))
    dry_run = bool(getattr(args, "dry_run", False))

    result = write_server_env(
        output,
        embedding_model=embedding_model,
        for_docker=for_docker,
        dry_run=dry_run,
    )

    print("\n  LightRAG sync-server-env")
    print(f"  Intellect home: {display_intellect_home()}")
    print(f"  Output target: {output} ({path_reason})")
    if dry_run:
        print("  (dry-run — not written)\n")
        print("\n".join(result.lines))
    else:
        print(f"  Wrote: {output}")
        print(
            f"  LLM: {result.llm_binding}/{result.llm_model}  "
            f"Embedding: {result.embedding_binding}/{result.embedding_model}"
        )
    for warn in result.warnings:
        print(f"  ⚠ {warn}")
    print(
        "\n  Next: copy to deploy/lightrag/.env if needed, then "
        "`docker compose -f deploy/lightrag/docker-compose.webui.yml "
        "up -d lightrag`\n"
    )


def _cmd_doctor(args) -> None:
    """Run the same checks as ``intellect doctor`` RAG section."""
    from intellect_cli.doctor import check_fail, check_info, check_ok, check_warn

    issues: list = []

    def _fail(text, detail, fix, iss):
        check_fail(text, detail)
        iss.append(fix)

    from .doctor import diagnose_lightrag_rag

    print("\n  LightRAG doctor\n")
    diagnose_lightrag_rag(
        _intellect_home(),
        check_ok=check_ok,
        check_warn=check_warn,
        check_info=check_info,
        fail_fn=_fail,
        issues=issues,
    )
    if issues:
        print(f"\n  {len(issues)} issue(s) — run `intellect doctor` for full report.\n")
    else:
        print("\n  LightRAG checks passed.\n")


def _cmd_health(args) -> None:
    from .client import LightRAGClientManager, LightRAGUnavailable

    cfg = load_config(_intellect_home())
    base_url = (cfg.get("server") or {}).get("base_url", "")
    out: dict = {"base_url": base_url, "healthy": False}
    if not base_url:
        if getattr(args, "json", False):
            print(json.dumps(out, indent=2))
            return
        print("\n  LightRAG health: FAIL (server.base_url not set)\n")
        return

    try:
        mgr = LightRAGClientManager(cfg)
        health = mgr.health()
        mgr.shutdown()
        out["healthy"] = True
        out["health"] = health
    except LightRAGUnavailable as exc:
        out["error"] = str(exc)

    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return

    print("\n  LightRAG health")
    print(f"  Server:  {base_url}")
    if out["healthy"]:
        status = (out.get("health") or {}).get("status", "ok")
        print(f"  Status:  OK ({status})")
    else:
        print(f"  Status:  FAIL")
        if out.get("error"):
            print(f"  Error:   {out['error']}")
    print()


def _cmd_workspaces(args) -> None:
    from .client import LightRAGClientManager, LightRAGUnavailable

    cfg = load_config(_intellect_home())
    scope = getattr(args, "scope", "all") or "all"
    out: dict = {"scope": scope, "workspaces": []}
    try:
        mgr = LightRAGClientManager(cfg)
        member_id = ""
        try:
            from intellect_cli.member_session import current_member_id
            member_id = current_member_id() or ""
        except Exception:
            pass
        mgr.bind_scope(member_id=member_id or None)
        out["workspaces"] = mgr.workspace_stats(scope=scope)
        mgr.shutdown()
    except LightRAGUnavailable as exc:
        out["error"] = str(exc)

    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return

    print(f"\n  LightRAG workspaces (scope={scope})")
    for row in out.get("workspaces") or []:
        ws = row.get("workspace", "?")
        count = row.get("document_count")
        err = row.get("error")
        if err:
            print(f"  {ws}: error — {err}")
        else:
            print(f"  {ws}: {count if count is not None else '?'} document(s)")
    if out.get("error") and not out.get("workspaces"):
        print(f"  Error: {out['error']}")
    print()


def _cmd_mcp_start(args) -> None:
    import asyncio as _asyncio
    import sys as _sys

    from .client import SCOPE_CHOICES

    scope = getattr(args, "scope", "auto")
    if scope not in SCOPE_CHOICES:
        print(f"  invalid --scope: {scope}", file=_sys.stderr)
        _sys.exit(1)

    from .mcp_server import start_mcp_server

    try:
        _asyncio.run(start_mcp_server(scope=scope))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"  lightrag mcp start failed: {exc}", file=_sys.stderr)
        _sys.exit(1)


def _cmd_mcp_config(args) -> None:
    from .mcp_server import render_mcp_config

    scope = getattr(args, "scope", "auto")
    print(render_mcp_config(scope=scope))


def _cmd_status(args) -> None:
    from intellect_constants import display_intellect_home

    cfg = load_config(_intellect_home())
    server = cfg.get("server") or {}
    base_url = server.get("base_url", "")
    ingest = (cfg.get("ingest") or {}).get("auto_mode", "off")
    out = {
        "provider": "lightrag",
        "base_url": base_url,
        "config_path": f"{display_intellect_home()}/lightrag/config.json",
        "ingest_auto_mode": ingest,
        "healthy": False,
    }
    if base_url:
        try:
            from .client import LightRAGClientManager
            mgr = LightRAGClientManager(cfg)
            mgr.health()
            mgr.shutdown()
            out["healthy"] = True
        except Exception as exc:
            out["error"] = str(exc)

    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return

    print(f"\n  LightRAG status")
    print(f"  Config:  {out['config_path']}")
    print(f"  Server:  {base_url or '(not set)'}")
    print(f"  Ingest:  {ingest}")
    print(f"  Health:  {'OK' if out['healthy'] else 'FAIL'}")
    if out.get("error"):
        print(f"  Error:   {out['error']}")
    print()


def lightrag_command(args) -> None:
    sub = getattr(args, "lightrag_command", None) or "status"
    if sub == "setup":
        _cmd_setup(args)
    elif sub == "doctor":
        _cmd_doctor(args)
    elif sub == "sync-server-env":
        _cmd_sync_server_env(args)
    elif sub == "health":
        _cmd_health(args)
    elif sub == "workspaces":
        _cmd_workspaces(args)
    elif sub == "mcp":
        mcp_sub = getattr(args, "lightrag_mcp_command", None)
        if mcp_sub == "start":
            _cmd_mcp_start(args)
        elif mcp_sub == "config":
            _cmd_mcp_config(args)
        else:
            print(f"  Unknown lightrag mcp command: {mcp_sub}")
    elif sub in (None, "status"):
        _cmd_status(args)
    else:
        print(f"  Unknown lightrag command: {sub}")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="lightrag_command")
    setup = subs.add_parser("setup", help="Configure LightRAG server URL")
    setup.set_defaults(func=lightrag_command)
    status = subs.add_parser("status", help="Server health and config summary")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=lightrag_command)
    doctor = subs.add_parser("doctor", help="LightRAG health checks (subset of intellect doctor)")
    doctor.set_defaults(func=lightrag_command)
    sync_env = subs.add_parser(
        "sync-server-env",
        help="Generate LightRAG server .env from Intellect model config",
    )
    sync_env.add_argument(
        "--output",
        "-o",
        default="",
        help="Output path (default: deploy/lightrag/.env or ~/.intellect/lightrag/server.env)",
    )
    sync_env.add_argument(
        "--embedding-model",
        default="",
        help="Override embedding model (default: bge-m3:latest for Ollama, text-embedding-3-small for OpenAI)",
    )
    sync_env.add_argument(
        "--docker",
        action="store_true",
        help="Rewrite localhost LLM/embedding hosts to host.docker.internal for compose",
    )
    sync_env.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated .env without writing",
    )
    sync_env.set_defaults(func=lightrag_command)
    health = subs.add_parser("health", help="GET /health summary")
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=lightrag_command)
    workspaces = subs.add_parser(
        "workspaces",
        help="List workspaces and document counts for current scope binding",
    )
    workspaces.add_argument(
        "--scope",
        default="all",
        choices=["auto", "member", "team", "project", "all", "session"],
    )
    workspaces.add_argument("--json", action="store_true")
    workspaces.set_defaults(func=lightrag_command)
    mcp_group = subs.add_parser("mcp", help="MCP stdio bridge for external clients")
    mcp_subs = mcp_group.add_subparsers(dest="lightrag_mcp_command")
    mcp_start = mcp_subs.add_parser("start", help="Run LightRAG MCP server on stdio")
    mcp_start.add_argument(
        "--scope",
        default="auto",
        choices=["auto", "member", "team", "project", "all", "session"],
    )
    mcp_start.set_defaults(func=lightrag_command, lightrag_command="mcp")
    mcp_config = mcp_subs.add_parser("config", help="Print MCP client config snippets")
    mcp_config.add_argument(
        "--scope",
        default="auto",
        choices=["auto", "member", "team", "project", "all", "session"],
    )
    mcp_config.set_defaults(func=lightrag_command, lightrag_command="mcp")
