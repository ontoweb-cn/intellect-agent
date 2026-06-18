"""Analytics, config, and meta sub-commands.

Extracted from ``intellect_cli/main.py`` — includes ``intellect analytics``,
``intellect config``, ``intellect secrets``, ``intellect skills``,
``intellect plugins``, ``intellect memory``, ``intellect tools``, etc.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def _require_tty(command_name: str) -> None:
    """Lazy re-export from main."""
    from intellect_cli.main import _require_tty as _rt
    _rt(command_name)


def cmd_analytics(args):
    """Show skill lifecycle analytics (P6-1)."""
    import json as _json
    from agent.skill_analytics import collect_skill_analytics
    from intellect_state import SessionDB

    db = SessionDB()
    try:
        data = collect_skill_analytics(db=db)
    finally:
        db.close()

    if getattr(args, "json", False):
        print(_json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return

    s = data["summary"]
    print()
    print(f"  Skills: {s['total_skills']} total | {s['active_7d']} active (7d) | {s['active_30d']} active (30d)")
    print(f"  Usage:  {s['total_usage_count']} total tool calls | {s['total_size_kb']} KB")
    print(f"  Iterations: {s['avg_commits_per_skill']:.1f} avg commits per skill")
    print(f"  Categories: {len(s['categories'])} — {', '.join(f'{k}({v})' for k, v in sorted(s['categories'].items(), key=lambda x: -x[1])[:8])}")
    print()

    top_n = getattr(args, "top", 10)
    top = data.get("top_used", [])[:top_n]
    if top:
        print(f"  Top {len(top)} most-used skills:")
        for i, t in enumerate(top, 1):
            bar = "█" * min(int(t["total"] / max(1, top[0]["total"]) * 20), 20) if top else ""
            print(f"  {i:2d}. {t['name']:<30s} {t['total']:>5d} calls  {bar}")
    print()


# Top-level subcommands that argparse knows about WITHOUT running plugin
# discovery.  Used to short-circuit eager plugin imports (which can take
# 500ms+ pulling in google.cloud.pubsub_v1, aiohttp, grpc, etc.) when the
# user's invocation clearly doesn't need any plugin-registered subcommand.
#
# Keep this in sync with the ``subparsers.add_parser("NAME", ...)`` calls
# below in ``main()``. Missing an entry here only costs a one-time
# discovery; extra entries here would let a plugin command silently fail
# to parse.
_BUILTIN_SUBCOMMANDS = frozenset(
    {
        "acp", "analytics", "auth", "backup", "bundles", "checkpoints", "claw",
        "completion", "computer-use",
        "config", "cron", "curator", "debug", "doctor",
        "dump", "fallback", "gateway", "hooks", "import", "insights",
        "kanban", "login", "logout", "logs", "lsp", "mcp", "members", "memory", "migrate",
        "model", "pairing", "plugins", "portal", "postinstall", "profile", "proxy",
        "prompt-size",
        "send", "sessions", "setup",
        "skills", "slack", "status", "tools", "uninstall", "update",
        "vault", "version", "webhook", "whatsapp", "chat", "secrets", "security",
        # Help-ish invocations — plugin commands not being listed in
        # top-level --help is an acceptable trade-off for skipping an
        # expensive eager import of every bundled plugin module.
        "help",
    }
)


# Top-level flags that take a value. Needed by ``_first_positional_argv``
# so that in ``intellect -m gpt5 chat``, ``gpt5`` is correctly skipped as a
# flag value rather than misclassified as a subcommand. Kept in sync with
# the top-level flags declared in ``intellect_cli/_parser.py``.
#
# Correctness-safe either way: missing an entry here only makes the
# fast-path bail out too eagerly (we run plugin discovery when we didn't
# need to); extra entries would make us skip a real positional.
_TOP_LEVEL_VALUE_FLAGS = frozenset(
    {
        "-z", "--oneshot",
        "-m", "--model",
        "--provider",
        "-t", "--toolsets",
        "-r", "--resume",
        "-s", "--skills",
        # ``-c / --continue`` is nargs='?' (optional value). Treat it as
        # value-taking: if the next token is a subcommand-looking word
        # the user almost certainly meant it as the session name, and
        # either interpretation keeps us on the safe side.
        "-c", "--continue",
    }
)


def _first_positional_argv() -> str | None:
    """Return the first non-flag, non-flag-value token in ``sys.argv[1:]``.

    Used by ``main()`` to decide whether plugin discovery has to run at
    argparse-setup time. Handles common invocations like
    ``intellect -m gpt5 --provider openai chat "msg"`` by skipping the
    values attached to known top-level flags.

    Does NOT fully simulate argparse — unknown ``--foo=bar`` / ``--foo
    bar`` flags degrade gracefully (``bar`` may be wrongly classified as
    a positional, which at worst forces a one-time plugin discovery).
    """
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            # Everything after ``--`` is positional.
            if i + 1 < len(argv):
                return argv[i + 1]
            return None
        if tok.startswith("-"):
            # ``--flag=value`` carries its value inline — single token.
            if "=" in tok:
                i += 1
                continue
            if tok in _TOP_LEVEL_VALUE_FLAGS and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        return tok
    return None


def _plugin_cli_discovery_needed() -> bool:
    """True when the CLI might be invoking a plugin-registered subcommand.

    Returning False lets ``main()`` skip plugin discovery entirely during
    argparse setup, saving ~500-650ms per invocation for users whose
    enabled plugins don't contribute any CLI command.
    """
    first = _first_positional_argv()
    if first is None:
        # Bare ``intellect`` or only flags → defaults to ``chat``.
        return False
    if first in _BUILTIN_SUBCOMMANDS:
        return False
    # Unknown token — could be a plugin subcommand, OR a chat prompt
    # starting with a non-flag word. Either way we need discovery: if it
    # IS a plugin command, argparse needs the subparser; if it's a chat
    # prompt, argparse will route it via positional handling and the
    # extra discovery cost is amortized over a full agent run anyway.
    return True


_AGENT_COMMANDS = {None, "chat", "acp", "rl"}
_AGENT_SUBCOMMANDS = {
    "cron": ("cron_command", {"run", "tick"}),
    "gateway": ("gateway_command", {"run"}),
    "mcp": ("mcp_action", {"serve"}),
}


def _is_tui_chat_launch(args) -> bool:
    return bool(getattr(args, "tui", False) or os.environ.get("intellect_TUI") == "1")


def _command_has_dedicated_mcp_startup(args) -> bool:
    if args.command == "acp":
        return True
    if args.command == "gateway" and getattr(args, "gateway_command", None) == "run":
        return True
    if args.command == "cron" and getattr(args, "cron_command", None) in {"run", "tick"}:
        return True
    return False


def _should_background_mcp_startup(args) -> bool:
    if _is_tui_chat_launch(args):
        return False
    return args.command in {None, "chat", "rl"}


def _prepare_agent_startup(args) -> None:
    """Discover plugins/MCP/hooks for commands that can run an agent turn."""
    _sub_attr, _sub_set = _AGENT_SUBCOMMANDS.get(args.command, (None, None))
    if not (
        args.command in _AGENT_COMMANDS
        or (_sub_attr and getattr(args, _sub_attr, None) in _sub_set)
    ):
        return

    _accept_hooks = bool(getattr(args, "accept_hooks", False))
    try:
        from intellect_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:
        logger.warning(
            "plugin discovery failed at CLI startup",
            exc_info=True,
        )
    _run_inline_mcp_discovery = True
    if _is_tui_chat_launch(args):
        # The TUI launcher hands off to a dedicated startup path that already
        # backgrounds MCP discovery with a bounded join before the first tool
        # snapshot.
        _run_inline_mcp_discovery = False
    elif _command_has_dedicated_mcp_startup(args):
        # These entrypoints already do their own MCP startup later on the real
        # runtime path (gateway executor, ACP launcher, cron job runner).
        _run_inline_mcp_discovery = False
    elif _should_background_mcp_startup(args):
        try:
            from intellect_cli.mcp_startup import start_background_mcp_discovery

            start_background_mcp_discovery(
                logger=logger,
                thread_name="cli-mcp-discovery",
            )
        except Exception:
            logger.debug(
                "Background MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
        _run_inline_mcp_discovery = False
    if _run_inline_mcp_discovery:
        try:
            # MCP tool discovery remains synchronous for entrypoints that do
            # not own a later bounded/executor startup path.
            from tools.mcp_tool import discover_mcp_tools

            discover_mcp_tools()
        except Exception:
            logger.debug(
                "MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
    try:
        from intellect_cli.config import load_config
        from agent.shell_hooks import register_from_config

        register_from_config(load_config(), accept_hooks=_accept_hooks)
    except Exception:
        logger.debug(
            "shell-hook registration failed at CLI startup",
            exc_info=True,
        )


def _set_chat_arg_defaults(args) -> None:
    for attr, default in [
        ("query", None),
        ("model", None),
        ("provider", None),
        ("toolsets", None),
        ("verbose", False),
        ("resume", None),
        ("continue_last", None),
        ("worktree", False),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)


def _try_termux_fast_cli_launch() -> bool:
    """Run obvious Termux non-TUI chat/oneshot/version paths on a light parser."""
    if not _is_termux_startup_environment():
        return False
    if os.environ.get("INTELLECT_TERMUX_DISABLE_FAST_CLI") == "1":
        return False

    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        return False
    if os.environ.get("intellect_TUI") == "1" or "--tui" in argv:
        return False

    if _is_termux_fast_version_argv(argv):
        _print_version_info(check_updates=False)
        return True

    first = _first_positional_argv()
    has_oneshot = any(
        arg == "-z" or arg == "--oneshot" or arg.startswith("--oneshot=")
        for arg in argv
    )
    if not has_oneshot and first not in {None, "chat"}:
        return False

    from intellect_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    args = parser.parse_args(_coalesce_session_name_args(argv))

    if getattr(args, "version", False):
        _print_version_info(check_updates=False)
        return True

    if getattr(args, "oneshot", None):
        _prepare_agent_startup(args)
        from intellect_cli.oneshot import run_oneshot

        sys.exit(
            run_oneshot(
                args.oneshot,
                model=getattr(args, "model", None),
                provider=getattr(args, "provider", None),
                toolsets=getattr(args, "toolsets", None),
            )
        )

    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"

    if args.command in {None, "chat"}:
        _set_chat_arg_defaults(args)
        interactive_prompt = not getattr(args, "query", None) and not getattr(args, "image", None)
        if interactive_prompt:
            # Bare Termux CLI should reach the prompt first and do agent-only
            # discovery on the first submitted turn instead of before input.
            setattr(args, "compact", True)
            os.environ["INTELLECT_DEFER_AGENT_STARTUP"] = "1"
            os.environ["INTELLECT_FAST_STARTUP_BANNER"] = "1"
            if getattr(args, "accept_hooks", False):
                os.environ["INTELLECT_ACCEPT_HOOKS"] = "1"
        else:
            _prepare_agent_startup(args)
        cmd_chat(args)
        return True

    return False


def _try_termux_fast_tui_launch() -> bool:
    """Launch obvious Termux TUI invocations before building every subparser.

    `intellect --tui` is the hot path on phones. The full parser setup imports
    command modules for model, fallback, migrate, kanban, bundles, plugins,
    etc. even though the TUI immediately execs Node. On Termux only, parse the
    lightweight top-level/chat parser and hand off to ``cmd_chat`` when the
    invocation is unambiguously the built-in TUI/chat path.
    """
    if not _is_termux_startup_environment():
        return False

    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        return False

    wants_tui = os.environ.get("intellect_TUI") == "1" or "--tui" in sys.argv[1:]
    if not wants_tui:
        return False

    first = _first_positional_argv()
    if first not in {None, "chat"}:
        return False

    from intellect_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    args = parser.parse_args(_coalesce_session_name_args(sys.argv[1:]))

    # Preserve top-level behaviours whose semantics are not "launch chat/TUI".
    if getattr(args, "version", False) or getattr(args, "oneshot", None):
        return False
    if getattr(args, "command", None) not in {None, "chat"}:
        return False
    if not (getattr(args, "tui", False) or os.environ.get("intellect_TUI") == "1"):
        return False

    cmd_chat(args)
    return True


def main():
    """Main entry point for intellect CLI."""
    # Cosmetic: make the process show up as 'intellect' instead of 'python3.11'
    # in ps/top/htop.  Non-fatal — just a nicer UX.
    _set_process_title()

    # Force UTF-8 stdio on Windows before anything prints.  No-op elsewhere.
    try:
        from intellect_cli.stdio import configure_windows_stdio
        configure_windows_stdio()
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    # Sweep stale ``intellect.exe.old.*`` quarantine files left by previous
    # ``intellect update`` runs on Windows. Silent no-op on non-Windows or when
    # there's nothing to clean. See ``_quarantine_running_intellect_exe``.
    try:
        _cleanup_quarantined_exes()
    except Exception:
        pass  # intentionally silent — cleanup/teardown path

    if _try_termux_fast_tui_launch():
        return
    if _try_termux_fast_cli_launch():
        return

    from intellect_cli._parser import build_top_level_parser

    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help="Select default model and provider",
        description="Interactively select your inference provider and default model",
    )
    model_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Wipe the model picker disk cache and re-fetch every provider's live /v1/models list.",
    )
    model_parser.add_argument(
        "--portal-url",
        help="Portal base URL for OntoWeb login (default: production portal)",
    )
    model_parser.add_argument(
        "--inference-url",
        help="Inference API base URL for OntoWeb login (default: production inference API)",
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth client id to use for OntoWeb login (default: intellect-cli)",
    )
    model_parser.add_argument(
        "--scope", default=None, help="OAuth scope to request for OntoWeb login"
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically during OntoWeb login",
    )
    model_parser.add_argument(
        "--manual-paste",
        action="store_true",
        help=(
            "For loopback OAuth providers (xai-oauth, ...): skip the local "
            "callback listener and paste the failed callback URL from your "
            "browser instead. Use on browser-only remotes (Cloud Shell, "
            "Codespaces, EC2 Instance Connect, ...). See #26923."
        ),
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds for OntoWeb login (default: 15)",
    )
    model_parser.add_argument(
        "--ca-bundle", help="Path to CA bundle PEM file for OntoWeb TLS verification"
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for OntoWeb login (testing only)",
    )
    model_parser.set_defaults(func=cmd_model)

    # =========================================================================
    # fallback command — manage the fallback provider chain
    # =========================================================================
    from intellect_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback",
        help="Manage fallback providers (tried when the primary model fails)",
        description=(
            "Manage the fallback provider chain.  Fallback providers are tried "
            "in order when the primary model fails with rate-limit, overload, or "
            "connection errors.  See: "
            "https://intellect.ontoweb.cn/docs/user-guide/features/fallback-providers"
        ),
    )
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="Show the current fallback chain (default when no subcommand)",
    )
    fallback_subparsers.add_parser(
        "add",
        help="Pick a provider + model (same picker as `intellect model`) and append to the chain",
    )
    fallback_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help="Pick an entry to delete from the chain",
    )
    fallback_subparsers.add_parser(
        "clear",
        help="Remove all fallback entries",
    )
    fallback_parser.set_defaults(func=cmd_fallback)

    # =========================================================================
    # secrets command — external secret managers (currently: Bitwarden)
    # =========================================================================
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Manage external secret sources (Bitwarden Secrets Manager)",
        description=(
            "Pull API keys from an external secret manager at process startup "
            "instead of storing them in ~/.intellect/.env.  Currently supports "
            "Bitwarden Secrets Manager.  See: "
            "https://intellect.ontoweb.cn/docs/user-guide/secrets/bitwarden"
        ),
    )
    secrets_subparsers = secrets_parser.add_subparsers(dest="secrets_command")

    secrets_bw = secrets_subparsers.add_parser(
        "bitwarden",
        aliases=["bw"],
        help="Bitwarden Secrets Manager integration",
    )

    # Lazy import — only pays for itself when this subcommand is actually used.
    from intellect_cli import secrets_cli as _secrets_cli

    _secrets_cli.register_cli(secrets_bw)

    def _dispatch_secrets(args):  # noqa: ANN001
        sub = getattr(args, "secrets_command", None)
        bw_sub = getattr(args, "secrets_bw_command", None)
        if sub in ("bitwarden", "bw") and bw_sub is not None:
            return args.func(args)
        if sub == "store":
            return args.func(args)
        secrets_parser.print_help()
        return 0

    secrets_parser.set_defaults(func=_dispatch_secrets)

    # -- secrets store (encrypted key-value for API keys) --
    _secrets_store = secrets_subparsers.add_parser(
        "store",
        help="Encrypted key-value store for API keys and tokens",
    )
    _secrets_store_sub = _secrets_store.add_subparsers(dest="store_command")
    _ss_set = _secrets_store_sub.add_parser("set", help="Store a secret")
    _ss_set.add_argument("key", help="Secret key name")
    _ss_set.add_argument("value", help="Secret value")
    _ss_get = _secrets_store_sub.add_parser("get", help="Retrieve a secret")
    _ss_get.add_argument("key", help="Secret key name")
    _ss_list = _secrets_store_sub.add_parser("list", help="List stored secret keys")
    _ss_del = _secrets_store_sub.add_parser("delete", help="Delete a secret")
    _ss_del.add_argument("key", help="Secret key name")
    _ss_migrate = _secrets_store_sub.add_parser(
        "migrate-api-keys",
        help="Move plaintext API keys from config.yaml into encrypted SecretStore",
    )
    _ss_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing changes",
    )

    def _dispatch_secrets_store(args):  # noqa: ANN001
        from agent.secret_store import SecretStore
        from intellect_cli.api_key_secrets import cmd_migrate_api_keys

        store = SecretStore()
        sub = getattr(args, "store_command", None)
        if sub == "set":
            store.set_secret(args.key, args.value)
            print(f"Stored secret: {args.key}")
        elif sub == "get":
            val = store.get_secret(args.key)
            if val is None:
                print(f"Secret not found: {args.key}", file=__import__("sys").stderr)
                return 1
            print(val)
        elif sub == "list":
            for k in sorted(store.list_secrets()):
                print(k)
        elif sub == "delete":
            if store.delete_secret(args.key):
                print(f"Deleted: {args.key}")
            else:
                print(f"Not found: {args.key}", file=__import__("sys").stderr)
                return 1
        elif sub == "migrate-api-keys":
            return cmd_migrate_api_keys(args)
        else:
            _secrets_store.print_help()
        return 0

    _secrets_store.set_defaults(func=_dispatch_secrets_store)

    # =========================================================================
    # members command (deprecated — single-user mode)
    # =========================================================================
    members_parser = subparsers.add_parser(
        "members",
        help="[DEPRECATED] Multi-user features removed in v0.5.0",
        description=(
            "Member, team, and project management.  Requires members.enabled: true "
            "in config.yaml."
        ),
    )
    members_subparsers = members_parser.add_subparsers(dest="members_command")

    # ── members bootstrap ──────────────────────────────────────────────────
    _mem_bootstrap = members_subparsers.add_parser(
        "bootstrap", help="Bootstrap default member, team, and project",
    )
    _mem_bootstrap.add_argument("--admin-login", default=None, help="Admin member login")
    _mem_bootstrap.add_argument("--team", default="default", help="Default team slug")
    _mem_bootstrap.add_argument("--project", default="default", help="Default project slug")
    _mem_bootstrap.add_argument(
        "--storage",
        choices=("sqlite",),
        default=None,
        help="Persist storage.backend (always sqlite)",
    )

    # ── members add (owner only) ────────────────────────────────────────────
    _mem_add = members_subparsers.add_parser("add", help="Add a new member (owner only)")
    _mem_add.add_argument("login", help="Login name")
    _mem_add.add_argument("--name", default=None, help="Display name")
    _mem_add.add_argument("--email", default=None, help="Email address")
    _mem_add.add_argument("--id", default=None, help="Custom member ID (slug format, owner only)")

    # ── members invite (owner only) ────────────────────────────────────────
    _mem_invite = members_subparsers.add_parser(
        "invite", help="Create an invite code (owner or admin)"
    )
    _mem_invite.add_argument("login", nargs="?", default=None, help="Pre-assigned login name")
    _mem_invite.add_argument("--email", default=None, help="Email hint")
    _mem_invite.add_argument("--ttl", default="168h", help="Invite TTL (default: 168h = 7 days)")

    # ── members register ───────────────────────────────────────────────────
    _mem_register = members_subparsers.add_parser("register", help="Register with an invite code")
    _mem_register.add_argument("code", help="Invite code")
    _mem_register.add_argument("--oauth", default=None, const="github", nargs="?", metavar="PROVIDER",
                              help="Also bind OAuth provider after registration")

    # ── members activate / deactivate / delete (owner only) ────────────────
    _mem_activate = members_subparsers.add_parser("activate", help="Activate a member (owner only)")
    _mem_activate.add_argument("login", help="Member login name")

    _mem_deactivate = members_subparsers.add_parser("deactivate", help="Deactivate a member (owner only)")
    _mem_deactivate.add_argument("login", help="Member login name")

    _mem_delete = members_subparsers.add_parser("delete", help="Delete a member permanently (owner only)")
    _mem_delete.add_argument("login", help="Member login name")

    # ── members grant-owner (owner only) ───────────────────────────────────
    _mem_grant = members_subparsers.add_parser("grant-owner", help="Grant owner role to a member (owner only)")
    _mem_grant.add_argument("login", help="Member login name")

    # ── members audit ──────────────────────────────────────────────────────
    _mem_audit = members_subparsers.add_parser("audit", help="Member management audit log")
    _audit_sub = _mem_audit.add_subparsers(dest="audit_command")
    _audit_list = _audit_sub.add_parser("list", help="List audit entries")
    _audit_list.add_argument("--actor", default=None, help="Filter by actor member id")
    _audit_list.add_argument("--target", default=None, help="Filter by target member id")
    _audit_list.add_argument("--action", default=None, help="Filter by action type")
    _audit_list.add_argument("--limit", type=int, default=50)
    _audit_tail = _audit_sub.add_parser("tail", help="Show recent audit entries")
    _audit_tail.add_argument("-n", type=int, default=20, help="Number of lines")

    # ── members list ───────────────────────────────────────────────────────
    members_subparsers.add_parser("list", help="List members")

    # ── members show ───────────────────────────────────────────────────────
    _mem_show = members_subparsers.add_parser("show", help="Show member details")
    _mem_show.add_argument("login", help="Member login name")

    # ── members workspace ──────────────────────────────────────────────────
    _mem_ws = members_subparsers.add_parser("workspace", help="Show member workspace path")
    _mem_ws.add_argument("login", help="Member login name")

    # ── members bind ───────────────────────────────────────────────────────
    _mem_bind = members_subparsers.add_parser("bind", help="Link an OAuth provider to a member")
    _mem_bind.add_argument("--oauth", required=True, help="OAuth provider to link (github, google, etc.)")
    _mem_bind.add_argument("--login", default=None, help="Member login (defaults to current session)")

    # ── members identities ─────────────────────────────────────────────────
    _mem_ids = members_subparsers.add_parser("identities", help="List linked OAuth identities")
    _mem_ids.add_argument("login", nargs="?", help="Member login (defaults to current session)")

    # ── members login ──────────────────────────────────────────────────────
    _mem_login = members_subparsers.add_parser("login", help="Set active member for CLI session")
    _mem_login.add_argument("login", nargs="?", help="Member login name (omit for --oauth)")
    _mem_login.add_argument("--oauth", default=None, const="github", nargs="?", metavar="PROVIDER",
                            help="Login via OAuth provider (github, google, gitee, azure_ad)")
    _mem_login.add_argument("--device", action="store_true", help="Use device code flow (remote SSH)")

    members_subparsers.add_parser("logout", help="Log out current member")

    members_subparsers.add_parser("whoami", help="Show current member identity")

    _mem_role = members_subparsers.add_parser(
        "role", help="Manage custom roles (requires members.rbac.version: 2)"
    )
    _role_sub = _mem_role.add_subparsers(dest="role_command")
    _role_sub.add_parser("list", help="List role definitions")
    _role_show = _role_sub.add_parser("show", help="Show role details")
    _role_show.add_argument("role_id", help="Role id or name")
    _role_create = _role_sub.add_parser("create", help="Create a custom role")
    _role_create.add_argument("name", help="Role name")
    _role_create.add_argument(
        "--permissions", required=True,
        help="Comma-separated action ids (e.g. chat,read,team:member:list)",
    )
    _role_del = _role_sub.add_parser("delete", help="Delete a custom role")
    _role_del.add_argument("role_id", help="Role id or name")
    _role_grant = _role_sub.add_parser("grant", help="Grant role to member")
    _role_grant.add_argument("login", help="Member login")
    _role_grant.add_argument("role_id", help="Role id or name")
    _role_grant.add_argument("--scope", choices=("team", "project"), default=None)
    _role_grant.add_argument("--id", dest="scope_id", default=None, help="Team/project slug")
    _role_revoke = _role_sub.add_parser("revoke", help="Revoke role from member")
    _role_revoke.add_argument("login", help="Member login")
    _role_revoke.add_argument("role_id", help="Role id or name")
    _role_revoke.add_argument("--scope", choices=("team", "project"), default=None)
    _role_revoke.add_argument("--id", dest="scope_id", default=None, help="Team/project slug")

    members_subparsers.add_parser("passwd", help="Change your password")

    _mem_reset = members_subparsers.add_parser("reset", help="Reset a member's password (owner only)")
    _mem_reset.add_argument("login", help="Member login name")

    # ── members sessions (ownership migration) ─────────────────────────────
    _mem_sessions = members_subparsers.add_parser(
        "sessions", help="Session ownership utilities (multi-user)",
    )
    _mem_sessions_sub = _mem_sessions.add_subparsers(dest="sessions_command")
    _mem_sessions_migrate = _mem_sessions_sub.add_parser(
        "migrate-ownership",
        help="Stamp member_id on legacy NULL sessions (JSON + state.db)",
    )
    _mem_sessions_migrate.add_argument(
        "--member-id", required=True, help="Member id to assign to unowned sessions",
    )
    _mem_sessions_migrate.add_argument(
        "--team-id", default=None, help="Optional team_id when JSON/DB row lacks one",
    )
    _mem_sessions_migrate.add_argument(
        "--dry-run", action="store_true", help="Report counts without writing",
    )
    _mem_sessions_sub.add_parser(
        "audit-null",
        help="List JSON/state.db sessions missing member_id (exit 1 if any)",
    )

    # ── members teams ──────────────────────────────────────────────────────
    _team_parser = members_subparsers.add_parser(
        "teams", help="Team management",
    )
    _team_sub = _team_parser.add_subparsers(dest="teams_command")
    _team_create = _team_sub.add_parser("create", help="Create a team")
    _team_create.add_argument("slug", help="Team slug")
    _team_create.add_argument("--name", default=None, help="Display name")
    _team_list = _team_sub.add_parser("list", help="List teams")
    _team_list.add_argument("--member", default=None, help="Filter by member login")
    _team_show = _team_sub.add_parser("show", help="Show team details")
    _team_show.add_argument("slug", help="Team slug")
    _team_archive = _team_sub.add_parser("archive", help="Archive a team")
    _team_archive.add_argument("slug", help="Team slug")
    _team_join = _team_sub.add_parser("join", help="Join a team")
    _team_join.add_argument("slug", help="Team slug")
    _team_leave = _team_sub.add_parser("leave", help="Leave a team")
    _team_leave.add_argument("slug", help="Team slug")
    _team_approve = _team_sub.add_parser("approve", help="Approve a team member")
    _team_approve.add_argument("slug", help="Team slug")
    _team_approve.add_argument("member_login", help="Member login name")
    _team_admin = _team_sub.add_parser("admin", help="Manage team admins")
    _team_admin_sub = _team_admin.add_subparsers(dest="team_admin_command")
    _team_admin_add = _team_admin_sub.add_parser("add", help="Add a team admin")
    _team_admin_add.add_argument("slug", help="Team slug")
    _team_admin_add.add_argument("member_login", help="Member login name")
    _team_admin_remove = _team_admin_sub.add_parser("remove", help="Remove a team admin")
    _team_admin_remove.add_argument("slug", help="Team slug")
    _team_admin_remove.add_argument("member_login", help="Member login name")
    _team_ws = _team_sub.add_parser("workspace", help="Show team workspace path")
    _team_ws.add_argument("slug", help="Team slug")
    _team_soul = _team_sub.add_parser("soul", help="Team SOUL management")
    _team_soul_sub = _team_soul.add_subparsers(dest="team_soul_command")
    _team_soul_refresh = _team_soul_sub.add_parser("refresh", help="Synthesize team SOUL from member SOULs")
    _team_soul_refresh.add_argument("slug", help="Team slug")

    # ── members projects ───────────────────────────────────────────────────
    _proj_parser = members_subparsers.add_parser(
        "projects", help="Project management",
    )
    _proj_subparsers = _proj_parser.add_subparsers(dest="projects_command")

    _proj_bootstrap = _proj_subparsers.add_parser(
        "bootstrap",
        help="Create default project and directories",
        description="Idempotent: creates the default project in the database "
                    "and on disk if they do not exist.",
    )
    _proj_bootstrap.add_argument(
        "--slug", default="default",
        help="Project slug (default: 'default')",
    )
    _proj_bootstrap.add_argument(
        "--name", default="Default Project",
        help="Display name (default: 'Default Project')",
    )
    _proj_bootstrap.add_argument(
        "--owner", dest="owner_login", default=None,
        help="Owner member login name (default: first member found)",
    )

    _proj_list = _proj_subparsers.add_parser(
        "list", help="List projects",
    )
    _proj_list.add_argument(
        "--member", default=None,
        help="Filter by member login name",
    )
    _proj_list.add_argument(
        "--all", action="store_true",
        help="Include archived projects",
    )

    # ── CRUD ──
    _proj_create = _proj_subparsers.add_parser(
        "create", help="Create a new project",
    )
    _proj_create.add_argument("slug", help="Project slug (e.g. 'web-app')")
    _proj_create.add_argument("--name", default=None, help="Display name")
    _proj_create.add_argument("--team", default=None, help="Team slug to link")

    _proj_show = _proj_subparsers.add_parser(
        "show", help="Show project details",
    )
    _proj_show.add_argument("slug", help="Project slug")

    _proj_archive = _proj_subparsers.add_parser(
        "archive", help="Archive a project",
    )
    _proj_archive.add_argument("slug", help="Project slug")

    # ── Membership ──
    _proj_join = _proj_subparsers.add_parser(
        "join", help="Request to join a project",
    )
    _proj_join.add_argument("slug", help="Project slug")

    _proj_leave = _proj_subparsers.add_parser(
        "leave", help="Leave a project",
    )
    _proj_leave.add_argument("slug", help="Project slug")

    _proj_approve = _proj_subparsers.add_parser(
        "approve", help="Approve a membership request",
    )
    _proj_approve.add_argument("slug", help="Project slug")
    _proj_approve.add_argument("member_login", help="Member login name")

    _proj_reject = _proj_subparsers.add_parser(
        "reject", help="Reject a membership request",
    )
    _proj_reject.add_argument("slug", help="Project slug")
    _proj_reject.add_argument("member_login", help="Member login name")

    # ── Admin ──
    _proj_admin = _proj_subparsers.add_parser(
        "admin", help="Manage project administrators",
    )
    _proj_admin_sub = _proj_admin.add_subparsers(dest="admin_command")
    _proj_admin_add = _proj_admin_sub.add_parser("add", help="Add a project admin")
    _proj_admin_add.add_argument("slug", help="Project slug")
    _proj_admin_add.add_argument("member_login", help="Member login name")
    _proj_admin_remove = _proj_admin_sub.add_parser("remove", help="Remove a project admin")
    _proj_admin_remove.add_argument("slug", help="Project slug")
    _proj_admin_remove.add_argument("member_login", help="Member login name")

    # ── Team links ──
    _proj_link = _proj_subparsers.add_parser(
        "link-team", help="Link a team to a project",
    )
    _proj_link.add_argument("slug", help="Project slug")
    _proj_link.add_argument("team_slug", help="Team slug")

    _proj_unlink = _proj_subparsers.add_parser(
        "unlink-team", help="Remove a team link from a project",
    )
    _proj_unlink.add_argument("slug", help="Project slug")
    _proj_unlink.add_argument("team_slug", help="Team slug")

    # ── Env management ──
    _proj_env = _proj_subparsers.add_parser(
        "env", help="Manage project environment variables",
    )
    _proj_env_sub = _proj_env.add_subparsers(dest="env_command")
    _proj_env_set = _proj_env_sub.add_parser("set", help="Set an env var")
    _proj_env_set.add_argument("slug", help="Project slug")
    _proj_env_set.add_argument("key", help="Env var name")
    _proj_env_set.add_argument("value", help="Env var value")
    _proj_env_unset = _proj_env_sub.add_parser("unset", help="Remove an env var")
    _proj_env_unset.add_argument("slug", help="Project slug")
    _proj_env_unset.add_argument("key", help="Env var name")
    _proj_env_list = _proj_env_sub.add_parser("list", help="List env var keys")
    _proj_env_list.add_argument("slug", help="Project slug")

    # ── SOUL management ──
    _proj_soul = _proj_subparsers.add_parser(
        "soul", help="Manage project SOUL",
    )
    _proj_soul_sub = _proj_soul.add_subparsers(dest="soul_command")
    _proj_soul_show = _proj_soul_sub.add_parser("show", help="Show project SOUL.md")
    _proj_soul_show.add_argument("slug", help="Project slug")
    _proj_soul_edit = _proj_soul_sub.add_parser("edit", help="Edit project SOUL.md in $EDITOR")
    _proj_soul_edit.add_argument("slug", help="Project slug")

    # ── Git workspace ──
    _proj_clone = _proj_subparsers.add_parser(
        "clone", help="Clone or pull git repo into project workspace",
    )
    _proj_clone.add_argument("slug", help="Project slug")
    _proj_clone.add_argument("--url", default=None, help="Git repo URL (uses stored repo_url if not set)")
    _proj_clone.add_argument("--branch", default=None, help="Branch to clone")

    _proj_workspace = _proj_subparsers.add_parser(
        "workspace", help="Show project workspace path",
    )
    _proj_workspace.add_argument("slug", help="Project slug")

    # ── Project-scoped tokens ──
    _proj_token = _proj_subparsers.add_parser(
        "token", help="Manage project-scoped API tokens",
    )
    _proj_token_sub = _proj_token.add_subparsers(dest="token_command")
    _proj_token_create = _proj_token_sub.add_parser("create", help="Create a project-scoped token")
    _proj_token_create.add_argument("slug", help="Project slug")
    _proj_token_create.add_argument("--name", default="default", help="Token label")
    _proj_token_list = _proj_token_sub.add_parser("list", help="List project tokens")
    _proj_token_list.add_argument("slug", help="Project slug")
    _proj_token_revoke = _proj_token_sub.add_parser("revoke", help="Revoke a project token")
    _proj_token_revoke.add_argument("slug", help="Project slug")
    _proj_token_revoke.add_argument("token_id", help="Token ID to revoke")

    members_parser.set_defaults(func=cmd_members)

    # =========================================================================
    # oauth command
    # =========================================================================
    oauth_parser = subparsers.add_parser(
        "oauth", help="Manage OAuth providers",
        description="List, enable, disable, add, or remove OAuth providers for login and API access.",
    )
    oauth_subparsers = oauth_parser.add_subparsers(dest="oauth_command")
    oauth_subparsers.add_parser("list", help="List OAuth providers")
    _oa_enable = oauth_subparsers.add_parser("enable", help="Enable an OAuth provider")
    _oa_enable.add_argument("provider_id", help="Provider ID (e.g. github, google)")
    _oa_disable = oauth_subparsers.add_parser("disable", help="Disable an OAuth provider")
    _oa_disable.add_argument("provider_id", help="Provider ID")
    _oa_show = oauth_subparsers.add_parser("show", help="Show provider details")
    _oa_show.add_argument("provider_id", help="Provider ID")
    _oa_add = oauth_subparsers.add_parser("add", help="Add a custom OAuth provider")
    _oa_add.add_argument("--id", required=True, help="Provider ID")
    _oa_add.add_argument("--name", required=True, help="Display name")
    _oa_add.add_argument("--usage", default="login", choices=["login","model","server","both"])
    _oa_add.add_argument("--flow", default="pkce_loopback",
        choices=["pkce_loopback","device_code","oidc_discovery","trusted_header"])
    _oa_add.add_argument("--client-id", default="", help="OAuth client ID")
    _oa_add.add_argument("--client-secret", default="", help="OAuth client secret")
    _oa_add.add_argument("--authorize-url", default="")
    _oa_add.add_argument("--token-url", default="")
    _oa_add.add_argument("--userinfo-url", default="")
    _oa_add.add_argument("--scopes", default="openid", help="Comma-separated scopes")
    _oa_add.add_argument("--discovery-url", default="")
    _oa_remove = oauth_subparsers.add_parser("remove", help="Remove a custom provider")
    _oa_remove.add_argument("provider_id", help="Provider ID")
    _oa_seed = oauth_subparsers.add_parser(
        "seed-builtin",
        help="Seed built-in OAuth providers from the catalog file into state.db",
    )
    _oa_seed.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="Refresh endpoint/scope/logo metadata on existing built-in rows",
    )
    _oa_migrate = oauth_subparsers.add_parser(
        "migrate-from-config",
        help="Copy members.oauth.providers from config.yaml into state.db",
    )
    _oa_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing state.db",
    )
    _oa_migrate.add_argument(
        "--write-config",
        action="store_true",
        help="After migration, set members.oauth.providers to [] in config.yaml",
    )
    _oa_migrate.add_argument(
        "--force-secrets",
        action="store_true",
        help="Overwrite client_secret_encrypted when YAML includes a secret",
    )
    _oa_migrate.add_argument(
        "--force-client-id",
        action="store_true",
        help="Overwrite DB client_id and endpoint fields from YAML",
    )
    _oa_auth = oauth_subparsers.add_parser(
        "migrate-from-auth-json",
        help="Copy auth.json OAuth tokens into state.db oauth_tokens",
    )
    _oa_auth.add_argument(
        "--dry-run",
        action="store_true",
        help="Summarize auth.json OAuth content without writing",
    )
    _oa_auth.add_argument(
        "--prune-auth-json",
        action="store_true",
        help="After migration, clear providers and credential_pool in auth.json",
    )
    oauth_parser.set_defaults(func=cmd_oauth)

    # =========================================================================
    # migrate command
    # =========================================================================
    from intellect_cli.migrate import cmd_migrate, cmd_migrate_xai

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate configuration for retired models or deprecated settings",
        description=(
            "Diagnose and (optionally) rewrite the active config.yaml to "
            "replace references to retired models or deprecated settings."
        ),
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_type")

    migrate_xai = migrate_subparsers.add_parser(
        "xai",
        help="Migrate xAI models scheduled for retirement on May 15, 2026",
        description=(
            "Scan config.yaml for references to xAI models retiring on "
            "May 15, 2026 and, with --apply, rewrite them in-place to the "
            "official replacements per the xAI migration guide. The original "
            "config.yaml is backed up before any rewrite."
        ),
    )
    migrate_xai.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite config.yaml in-place (default: dry-run, no writes)",
    )
    migrate_xai.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the timestamped backup of config.yaml when applying",
    )
    migrate_xai.set_defaults(func=cmd_migrate_xai)
    migrate_parser.set_defaults(func=cmd_migrate)

    # =========================================================================
    # gateway command
    # =========================================================================
    gateway_parser = subparsers.add_parser(
        "gateway",
        help="Messaging gateway management",
        description="Manage the messaging gateway (Telegram, Discord, WhatsApp, Weixin, and more)",
    )
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_command")

    # gateway run (default)
    gateway_run = gateway_subparsers.add_parser(
        "run", help="Run gateway in foreground (recommended for WSL, Docker, Termux)"
    )
    gateway_run.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase stderr log verbosity (-v=INFO, -vv=DEBUG)",
    )
    gateway_run.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress all stderr log output"
    )
    gateway_run.add_argument(
        "--replace",
        action="store_true",
        help="Replace any existing gateway instance (useful for systemd)",
    )
    gateway_run.add_argument(
        "--no-supervise",
        action="store_true",
        help=(
            "Inside the s6-overlay Docker image, normally `gateway run` is "
            "automatically redirected to the supervised s6 service (so the "
            "gateway gets auto-restart on crash). Pass --no-supervise to opt out and "
            "get the historical pre-s6 foreground behavior: the gateway is "
            "the container's main process and the container exits with the "
            "gateway's exit code. No effect outside an s6 container."
        ),
    )
    _add_accept_hooks_flag(gateway_run)
    _add_accept_hooks_flag(gateway_parser)

    # gateway start
    gateway_start = gateway_subparsers.add_parser(
        "start", help="Start the installed systemd/launchd background service"
    )
    gateway_start.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_start.add_argument(
        "--all",
        action="store_true",
        help="Kill ALL stale gateway processes across all profiles before starting",
    )

    # gateway stop
    gateway_stop = gateway_subparsers.add_parser("stop", help="Stop gateway service")
    gateway_stop.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_stop.add_argument(
        "--all",
        action="store_true",
        help="Stop ALL gateway processes across all profiles",
    )

    # gateway restart
    gateway_restart = gateway_subparsers.add_parser(
        "restart", help="Restart gateway service"
    )
    gateway_restart.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )
    gateway_restart.add_argument(
        "--all",
        action="store_true",
        help="Kill ALL gateway processes across all profiles before restarting",
    )

    # gateway status
    gateway_status = gateway_subparsers.add_parser("status", help="Show gateway status")
    gateway_status.add_argument("--deep", action="store_true", help="Deep status check")
    gateway_status.add_argument(
        "-l",
        "--full",
        action="store_true",
        help="Show full, untruncated service/log output where supported",
    )
    gateway_status.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )

    # gateway install
    gateway_install = gateway_subparsers.add_parser(
        "install", help="Install gateway as a systemd/launchd background service"
    )
    gateway_install.add_argument("--force", action="store_true", help="Force reinstall")
    gateway_install.add_argument(
        "--system",
        action="store_true",
        help="Install as a Linux system-level service (starts at boot)",
    )
    gateway_install.add_argument(
        "--run-as-user",
        dest="run_as_user",
        help="User account the Linux system service should run as",
    )
    gateway_install.add_argument(
        "--start-now",
        dest="start_now",
        action="store_true",
        default=None,
        help=argparse.SUPPRESS,
    )
    gateway_install.add_argument(
        "--no-start-now",
        dest="start_now",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    gateway_install.add_argument(
        "--start-on-login",
        dest="start_on_login",
        action="store_true",
        default=None,
        help=argparse.SUPPRESS,
    )
    gateway_install.add_argument(
        "--no-start-on-login",
        dest="start_on_login",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    gateway_install.add_argument(
        "--elevated-handoff",
        dest="elevated_handoff",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # gateway uninstall
    gateway_uninstall = gateway_subparsers.add_parser(
        "uninstall", help="Uninstall gateway service"
    )
    gateway_uninstall.add_argument(
        "--system",
        action="store_true",
        help="Target the Linux system-level gateway service",
    )

    # gateway list
    gateway_subparsers.add_parser("list", help="List all profiles and their gateway status")

    # gateway setup
    gateway_subparsers.add_parser("setup", help="Configure messaging platforms")

    # gateway migrate-legacy
    gateway_migrate_legacy = gateway_subparsers.add_parser(
        "migrate-legacy",
        help="Remove legacy intellect.service units from pre-rename installs",
        description=(
            "Stop, disable, and remove legacy Intellect gateway unit files "
            "(e.g. intellect.service) left over from older installs. Profile "
            "units (intellect-gateway-<profile>.service) and unrelated "
            "third-party services are never touched."
        ),
    )
    gateway_migrate_legacy.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="List what would be removed without doing it",
    )
    gateway_migrate_legacy.add_argument(
        "-y",
        "--yes",
        dest="yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )

    # =========================================================================
    # proxy command — local OpenAI-compatible proxy that attaches the user's
    # OAuth-authenticated provider credentials to outbound requests. Lets
    # external apps (OpenViking, Karakeep, Open WebUI, ...) ride a logged-in
    # subscription without copy-pasting static API keys.
    # =========================================================================
    proxy_parser = subparsers.add_parser(
        "proxy",
        help="Local OpenAI-compatible proxy to OAuth providers",
        description=(
            "Run a local HTTP server that forwards OpenAI-compatible requests "
            "to an OAuth-authenticated provider (e.g. ONTOWEB Portal). External "
            "apps can point at the proxy with any bearer token; the proxy "
            "attaches your real credentials."
        ),
    )
    proxy_subparsers = proxy_parser.add_subparsers(dest="proxy_command")

    proxy_start = proxy_subparsers.add_parser(
        "start", help="Run the proxy in the foreground"
    )
    proxy_start.add_argument(
        "--provider",
        default="ontoweb",
        help="Upstream provider: ontoweb or xai (default: ontoweb). See `intellect proxy providers`.",
    )
    proxy_start.add_argument(
        "--host",
        default=None,
        help="Bind address (default: 127.0.0.1). Use 0.0.0.0 to expose on LAN.",
    )
    proxy_start.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: 8645)",
    )

    proxy_subparsers.add_parser(
        "status", help="Show which proxy upstreams are ready"
    )
    proxy_subparsers.add_parser(
        "providers", help="List available proxy upstream providers"
    )
    proxy_parser.set_defaults(func=cmd_proxy)
    gateway_parser.set_defaults(func=cmd_gateway)

    # =========================================================================
    # lsp command
    # =========================================================================
    try:
        from agent.lsp.cli import register_subparser as _lsp_register
        _lsp_register(subparsers)
    except Exception as _lsp_err:  # noqa: BLE001
        # LSP is optional infrastructure — never let a registration
        # failure break the CLI overall.
        logger.debug("LSP CLI registration failed: %s", _lsp_err)

    # =========================================================================
    # setup command
    # =========================================================================
    setup_parser = subparsers.add_parser(
        "setup",
        help="Interactive setup wizard",
        description="Configure Intellect Agent with an interactive wizard. "
        "Run a specific section: intellect setup model|tts|terminal|gateway|tools|agent",
    )
    setup_parser.add_argument(
        "section",
        nargs="?",
        choices=["model", "tts", "terminal", "gateway", "tools", "agent"],
        default=None,
        help="Run a specific setup section instead of the full wizard",
    )
    setup_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode (use defaults/env vars)",
    )
    setup_parser.add_argument(
        "--reset", action="store_true", help="Reset configuration to defaults"
    )
    setup_parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="(Default on existing installs.) Re-run the full wizard, "
        "showing current values as defaults. Kept for backwards "
        "compatibility — a bare 'intellect setup' now does this.",
    )
    setup_parser.add_argument(
        "--quick",
        action="store_true",
        help="On existing installs: only prompt for items that are missing "
        "or unset, instead of running the full reconfigure wizard.",
    )
    setup_parser.add_argument(
        "--portal",
        action="store_true",
        help="One-shot ONTOWEB Portal setup: log in via OAuth, set OntoWeb as the "
        "inference provider, and opt into the Tool Gateway. Skips the "
        "rest of the wizard.",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # =========================================================================
    # postinstall command
    # =========================================================================
    postinstall_parser = subparsers.add_parser(
        "postinstall",
        help="Bootstrap non-Python deps for pip installs (node, browser, ripgrep, ffmpeg)",
        description="One-shot post-install for pip users. Installs system "
        "dependencies that pip cannot provide, then runs setup if needed.",
    )
    postinstall_parser.set_defaults(func=cmd_postinstall)

    # =========================================================================
    # whatsapp command
    # =========================================================================
    whatsapp_parser = subparsers.add_parser(
        "whatsapp",
        help="Set up WhatsApp integration",
        description="Configure WhatsApp and pair via QR code",
    )
    whatsapp_parser.set_defaults(func=cmd_whatsapp)

    # =========================================================================
    # slack command
    # =========================================================================
    slack_parser = subparsers.add_parser(
        "slack",
        help="Slack integration helpers (manifest generation, etc.)",
        description="Slack integration helpers for Intellect.",
    )
    slack_sub = slack_parser.add_subparsers(dest="slack_command")
    slack_manifest = slack_sub.add_parser(
        "manifest",
        help="Print or write a Slack app manifest with every gateway command "
        "registered as a native slash (/btw, /stop, /model, ...)",
        description=(
            "Generate a Slack app manifest that registers every gateway "
            "command in COMMAND_REGISTRY as a first-class Slack slash "
            "command (matching Discord and Telegram parity). Paste the "
            "output into Slack app config → Features → App Manifest → "
            "Edit, then Save. Reinstall the app if Slack prompts for it."
        ),
    )
    slack_manifest.add_argument(
        "--write",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="Write manifest to a file instead of stdout. With no PATH "
        "writes to $INTELLECT_HOME/slack-manifest.json.",
    )
    slack_manifest.add_argument(
        "--name",
        default=None,
        help='Bot display name (default: "Intellect")',
    )
    slack_manifest.add_argument(
        "--description",
        default=None,
        help="Bot description shown in Slack's app directory.",
    )
    slack_manifest.add_argument(
        "--slashes-only",
        action="store_true",
        help="Emit only the features.slash_commands array (for merging "
        "into an existing manifest manually).",
    )
    slack_parser.set_defaults(func=cmd_slack)

    # =========================================================================
    # send command — pipe shell-script output to any configured platform
    # =========================================================================
    from intellect_cli.send_cmd import register_send_subparser
    register_send_subparser(subparsers)

    # =========================================================================
    # login command
    # =========================================================================
    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate with an inference provider",
        description="Run OAuth device authorization flow for Intellect CLI",
    )
    login_parser.add_argument(
        "--provider",
        choices=["ontoweb", "openai-codex", "xai-oauth"],
        default=None,
        help="Provider to authenticate with (default: ontoweb)",
    )
    login_parser.add_argument(
        "--portal-url", help="Portal base URL (default: production portal)"
    )
    login_parser.add_argument(
        "--inference-url",
        help="Inference API base URL (default: production inference API)",
    )
    login_parser.add_argument(
        "--client-id", default=None, help="OAuth client id to use (default: intellect-cli)"
    )
    login_parser.add_argument("--scope", default=None, help="OAuth scope to request")
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically",
    )
    login_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15)",
    )
    login_parser.add_argument(
        "--ca-bundle", help="Path to CA bundle PEM file for TLS verification"
    )
    login_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (testing only)",
    )
    login_parser.set_defaults(func=cmd_login)

    # =========================================================================
    # logout command
    # =========================================================================
    logout_parser = subparsers.add_parser(
        "logout",
        help="Clear authentication for an inference provider",
        description="Remove stored credentials and reset provider config",
    )
    logout_parser.add_argument(
        "--provider",
        choices=["ontoweb", "openai-codex", "xai-oauth", "spotify"],
        default=None,
        help="Provider to log out from (default: active provider)",
    )
    logout_parser.set_defaults(func=cmd_logout)

    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage pooled provider credentials",
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")
    auth_add = auth_subparsers.add_parser("add", help="Add a pooled credential")
    auth_add.add_argument(
        "provider",
        help="Provider id (for example: anthropic, openai-codex, openrouter)",
    )
    auth_add.add_argument(
        "--type",
        dest="auth_type",
        choices=["oauth", "api-key", "api_key"],
        help="Credential type to add",
    )
    auth_add.add_argument("--label", help="Optional display label")
    auth_add.add_argument(
        "--api-key", help="API key value (otherwise prompted securely)"
    )
    auth_add.add_argument("--portal-url", help="OntoWeb portal base URL")
    auth_add.add_argument("--inference-url", help="OntoWeb inference base URL")
    auth_add.add_argument("--client-id", help="OAuth client id")
    auth_add.add_argument("--scope", help="OAuth scope override")
    auth_add.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open a browser for OAuth login",
    )
    auth_add.add_argument(
        "--manual-paste",
        action="store_true",
        help=(
            "Skip the loopback callback listener and paste the failed "
            "callback URL from your browser instead. Use this on "
            "browser-only remotes (GCP Cloud Shell, GitHub Codespaces, "
            "EC2 Instance Connect, ...) where 127.0.0.1 on the remote "
            "isn't reachable from your laptop. See #26923."
        ),
    )
    auth_add.add_argument(
        "--timeout", type=float, help="OAuth/network timeout in seconds"
    )
    auth_add.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for OAuth login",
    )
    auth_add.add_argument("--ca-bundle", help="Custom CA bundle for OAuth login")
    auth_list = auth_subparsers.add_parser("list", help="List pooled credentials")
    auth_list.add_argument("provider", nargs="?", help="Optional provider filter")
    auth_remove = auth_subparsers.add_parser(
        "remove", help="Remove a pooled credential by index, id, or label"
    )
    auth_remove.add_argument("provider", help="Provider id")
    auth_remove.add_argument(
        "target", help="Credential index, entry id, or exact label"
    )
    auth_reset = auth_subparsers.add_parser(
        "reset", help="Clear exhaustion status for all credentials for a provider"
    )
    auth_reset.add_argument("provider", help="Provider id")
    auth_status = auth_subparsers.add_parser(
        "status", help="Show auth status for a provider"
    )
    auth_status.add_argument("provider", help="Provider id")
    auth_logout = auth_subparsers.add_parser(
        "logout", help="Log out a provider and clear stored auth state"
    )
    auth_logout.add_argument("provider", help="Provider id")
    auth_spotify = auth_subparsers.add_parser(
        "spotify", help="Authenticate Intellect with Spotify via PKCE"
    )
    auth_spotify.add_argument(
        "spotify_action",
        nargs="?",
        choices=["login", "status", "logout"],
        default="login",
    )
    auth_spotify.add_argument(
        "--client-id", help="Spotify app client_id (or set intellect_SPOTIFY_CLIENT_ID)"
    )
    auth_spotify.add_argument(
        "--redirect-uri",
        help="Allow-listed localhost redirect URI for your Spotify app",
    )
    auth_spotify.add_argument("--scope", help="Override requested Spotify scopes")
    auth_spotify.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically",
    )
    auth_spotify.add_argument(
        "--timeout", type=float, help="Callback/token exchange timeout in seconds"
    )
    auth_parser.set_defaults(func=cmd_auth)

    # =========================================================================
    # status command
    # =========================================================================
    status_parser = subparsers.add_parser(
        "status",
        help="Show status of all components",
        description="Display status of Intellect Agent components",
    )
    status_parser.add_argument(
        "--all", action="store_true", help="Show all details (redacted for sharing)"
    )
    status_parser.add_argument(
        "--deep", action="store_true", help="Run deep checks (may take longer)"
    )
    status_parser.set_defaults(func=cmd_status)

    # =========================================================================
    # cron command
    # =========================================================================
    cron_parser = subparsers.add_parser(
        "cron", help="Cron job management", description="Manage scheduled tasks"
    )
    cron_subparsers = cron_parser.add_subparsers(dest="cron_command")

    # cron list
    cron_list = cron_subparsers.add_parser("list", help="List scheduled jobs")
    cron_list.add_argument("--all", action="store_true", help="Include disabled jobs")

    # cron create/add
    cron_create = cron_subparsers.add_parser(
        "create", aliases=["add"], help="Create a scheduled job"
    )
    cron_create.add_argument(
        "schedule", help="Schedule like '30m', 'every 2h', or '0 9 * * *'"
    )
    cron_create.add_argument(
        "prompt", nargs="?", help="Optional self-contained prompt or task instruction"
    )
    cron_create.add_argument("--name", help="Optional human-friendly job name")
    cron_create.add_argument(
        "--deliver",
        help="Delivery target: origin, local, telegram, discord, signal, or platform:chat_id",
    )
    cron_create.add_argument("--repeat", type=int, help="Optional repeat count")
    cron_create.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help="Attach a skill. Repeat to add multiple skills.",
    )
    cron_create.add_argument(
        "--script",
        help=(
            "Path to a script under ~/.intellect/scripts/. Default mode: "
            "script stdout is injected into the agent's prompt each run. "
            "With --no-agent: the script IS the job and its stdout is "
            "delivered verbatim. .sh/.bash files run via bash, everything "
            "else via Python."
        ),
    )
    cron_create.add_argument(
        "--no-agent",
        dest="no_agent",
        action="store_true",
        default=False,
        help=(
            "Skip the LLM entirely — run --script on schedule and deliver "
            "its stdout directly. Empty stdout = silent. Classic watchdog "
            "pattern (memory alerts, disk alerts, CI pings)."
        ),
    )
    cron_create.add_argument(
        "--workdir",
        help="Absolute path for the job to run from. Injects AGENTS.md / CLAUDE.md / .cursorrules from that directory and uses it as the cwd for terminal/file/code_exec tools. Omit to preserve old behaviour (no project context files).",
    )
    cron_create.add_argument(
        "--profile",
        help="Intellect profile name to run the job under. Use 'default' for the root profile. Named profiles must already exist. Omit to preserve the scheduler's existing profile.",
    )

    # cron edit
    cron_edit = cron_subparsers.add_parser(
        "edit", help="Edit an existing scheduled job"
    )
    cron_edit.add_argument("job_id", help="Job ID to edit")
    cron_edit.add_argument("--schedule", help="New schedule")
    cron_edit.add_argument("--prompt", help="New prompt/task instruction")
    cron_edit.add_argument("--name", help="New job name")
    cron_edit.add_argument("--deliver", help="New delivery target")
    cron_edit.add_argument("--repeat", type=int, help="New repeat count")
    cron_edit.add_argument(
        "--skill",
        dest="skills",
        action="append",
        help="Replace the job's skills with this set. Repeat to attach multiple skills.",
    )
    cron_edit.add_argument(
        "--add-skill",
        dest="add_skills",
        action="append",
        help="Append a skill without replacing the existing list. Repeatable.",
    )
    cron_edit.add_argument(
        "--remove-skill",
        dest="remove_skills",
        action="append",
        help="Remove a specific attached skill. Repeatable.",
    )
    cron_edit.add_argument(
        "--clear-skills",
        action="store_true",
        help="Remove all attached skills from the job",
    )
    cron_edit.add_argument(
        "--script",
        help=(
            "Path to a script under ~/.intellect/scripts/. Pass empty string to clear. "
            "With --no-agent the script IS the job; otherwise its stdout is "
            "injected into the agent's prompt each run."
        ),
    )
    cron_edit.add_argument(
        "--no-agent",
        dest="no_agent",
        action="store_const",
        const=True,
        default=None,
        help=(
            "Enable no-agent mode on this job (requires --script or an "
            "existing script on the job)."
        ),
    )
    cron_edit.add_argument(
        "--agent",
        dest="no_agent",
        action="store_const",
        const=False,
        help="Disable no-agent mode on this job (reverts to LLM-driven execution).",
    )
    cron_edit.add_argument(
        "--workdir",
        help="Absolute path for the job to run from (injects AGENTS.md etc. and sets terminal cwd). Pass empty string to clear.",
    )
    cron_edit.add_argument(
        "--profile",
        help="Intellect profile name to run the job under. Use 'default' for the root profile. Pass empty string to clear.",
    )

    # lifecycle actions
    cron_pause = cron_subparsers.add_parser("pause", help="Pause a scheduled job")
    cron_pause.add_argument("job_id", help="Job ID to pause")

    cron_resume = cron_subparsers.add_parser("resume", help="Resume a paused job")
    cron_resume.add_argument("job_id", help="Job ID to resume")

    cron_run = cron_subparsers.add_parser(
        "run", help="Run a job on the next scheduler tick"
    )
    cron_run.add_argument("job_id", help="Job ID to trigger")
    _add_accept_hooks_flag(cron_run)

    cron_remove = cron_subparsers.add_parser(
        "remove", aliases=["rm", "delete"], help="Remove a scheduled job"
    )
    cron_remove.add_argument("job_id", help="Job ID to remove")

    # cron status
    cron_subparsers.add_parser("status", help="Check if cron scheduler is running")

    # cron tick (mostly for debugging)
    cron_tick = cron_subparsers.add_parser("tick", help="Run due jobs once and exit")
    _add_accept_hooks_flag(cron_tick)
    _add_accept_hooks_flag(cron_parser)
    cron_parser.set_defaults(func=cmd_cron)

    # =========================================================================
    # webhook command
    # =========================================================================
    webhook_parser = subparsers.add_parser(
        "webhook",
        help="Manage dynamic webhook subscriptions",
        description="Create, list, and remove webhook subscriptions for event-driven agent activation",
    )
    webhook_subparsers = webhook_parser.add_subparsers(dest="webhook_action")

    wh_sub = webhook_subparsers.add_parser(
        "subscribe", aliases=["add"], help="Create a webhook subscription"
    )
    wh_sub.add_argument("name", help="Route name (used in URL: /webhooks/<name>)")
    wh_sub.add_argument(
        "--prompt", default="", help="Prompt template with {dot.notation} payload refs"
    )
    wh_sub.add_argument(
        "--events", default="", help="Comma-separated event types to accept"
    )
    wh_sub.add_argument("--description", default="", help="What this subscription does")
    wh_sub.add_argument(
        "--skills", default="", help="Comma-separated skill names to load"
    )
    wh_sub.add_argument(
        "--deliver",
        default="log",
        help="Delivery target: log, telegram, discord, slack, etc.",
    )
    wh_sub.add_argument(
        "--deliver-chat-id",
        default="",
        help="Target chat ID for cross-platform delivery",
    )
    wh_sub.add_argument(
        "--secret", default="", help="HMAC secret (auto-generated if omitted)"
    )
    wh_sub.add_argument(
        "--deliver-only",
        action="store_true",
        help="Skip the agent — deliver the rendered prompt directly as the "
        "message. Zero LLM cost. Requires --deliver to be a real target "
        "(not 'log').",
    )

    webhook_subparsers.add_parser(
        "list", aliases=["ls"], help="List all dynamic subscriptions"
    )

    wh_rm = webhook_subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove a subscription"
    )
    wh_rm.add_argument("name", help="Subscription name to remove")

    wh_test = webhook_subparsers.add_parser(
        "test", help="Send a test POST to a webhook route"
    )
    wh_test.add_argument("name", help="Subscription name to test")
    wh_test.add_argument(
        "--payload", default="", help="JSON payload to send (default: test payload)"
    )

    webhook_parser.set_defaults(func=cmd_webhook)

    # =========================================================================
    # vault command
    # =========================================================================
    from intellect_cli.vault_cmd import register_subparsers as _register_vault_subparsers

    vault_parser = subparsers.add_parser(
        "vault",
        help="LLM Wiki Quartz vault tools",
        description="Trigger scheduled vault builds and inspect vault scheduler state",
    )
    _register_vault_subparsers(vault_parser)
    vault_parser.set_defaults(func=cmd_vault)

    # =========================================================================
    # portal command — ONTOWEB Portal status + Tool Gateway routing
    # =========================================================================
    from intellect_cli.portal_cli import add_parser as _add_portal_parser
    _add_portal_parser(subparsers)

    # =========================================================================
    # kanban command — multi-profile collaboration board
    # =========================================================================
    from intellect_cli.kanban import build_parser as _build_kanban_parser

    kanban_parser = _build_kanban_parser(subparsers)
    kanban_parser.set_defaults(func=cmd_kanban)

    # =========================================================================
    # hooks command — shell-hook inspection and management
    # =========================================================================
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Inspect and manage shell-script hooks",
        description=(
            "Inspect shell-script hooks declared in ~/.intellect/config.yaml, "
            "test them against synthetic payloads, and manage the first-use "
            "consent allowlist at ~/.intellect/shell-hooks-allowlist.json."
        ),
    )
    hooks_subparsers = hooks_parser.add_subparsers(dest="hooks_action")

    hooks_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="List configured hooks with matcher, timeout, and consent status",
    )

    _hk_test = hooks_subparsers.add_parser(
        "test",
        help="Fire every hook matching <event> against a synthetic payload",
    )
    _hk_test.add_argument(
        "event",
        help="Hook event name (e.g. pre_tool_call, pre_llm_call, subagent_stop)",
    )
    _hk_test.add_argument(
        "--for-tool",
        dest="for_tool",
        default=None,
        help=(
            "Only fire hooks whose matcher matches this tool name "
            "(used for pre_tool_call / post_tool_call)"
        ),
    )
    _hk_test.add_argument(
        "--payload-file",
        dest="payload_file",
        default=None,
        help=(
            "Path to a JSON file whose contents are merged into the "
            "synthetic payload before execution"
        ),
    )

    _hk_revoke = hooks_subparsers.add_parser(
        "revoke",
        aliases=["remove", "rm"],
        help="Remove a command's allowlist entries (takes effect on next restart)",
    )
    _hk_revoke.add_argument(
        "command",
        help="The exact command string to revoke (as declared in config.yaml)",
    )

    hooks_subparsers.add_parser(
        "doctor",
        help=(
            "Check each configured hook: exec bit, allowlist, mtime drift, "
            "JSON validity, and synthetic run timing"
        ),
    )

    hooks_parser.set_defaults(func=cmd_hooks)

    # =========================================================================
    # webui command — webui server management
    # =========================================================================
    webui_parser = subparsers.add_parser(
        "webui",
        help="Manage the Intellect WebUI server",
        description="Start, stop, restart, status, and logs for the WebUI server",
    )
    webui_subparsers = webui_parser.add_subparsers(dest="webui_command")

    webui_start = webui_subparsers.add_parser("start", help="Start WebUI server in background")
    webui_start.add_argument(
        "--host", default=None, metavar="HOST",
        help=f"Bind address (default: {os.getenv('INTELLECT_WEBUI_HOST', '127.0.0.1')})",
    )
    webui_start.add_argument(
        "--port", type=int, default=None, metavar="PORT",
        help=f"Listen port (default: {os.getenv('INTELLECT_WEBUI_PORT', '9119')})",
    )

    webui_subparsers.add_parser("stop", help="Stop WebUI server")

    webui_restart = webui_subparsers.add_parser("restart", help="Restart WebUI server")
    webui_restart.add_argument(
        "--host", default=None, metavar="HOST",
        help=f"Bind address (default: {os.getenv('INTELLECT_WEBUI_HOST', '127.0.0.1')})",
    )
    webui_restart.add_argument(
        "--port", type=int, default=None, metavar="PORT",
        help=f"Listen port (default: {os.getenv('INTELLECT_WEBUI_PORT', '9119')})",
    )

    webui_subparsers.add_parser("status", help="Show WebUI server status")

    webui_logs_parser = webui_subparsers.add_parser("logs", help="View WebUI server logs")
    webui_logs_parser.add_argument(
        "--lines", "-n", type=int, default=100, metavar="N",
        help="Number of lines to show (default: 100)",
    )
    webui_logs_parser.add_argument(
        "--follow", "-f", action="store_true",
        help="Follow log output in real time",
    )

    webui_parser.set_defaults(func=cmd_webui)

    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and dependencies",
        description="Diagnose issues with Intellect Agent setup",
    )
    doctor_parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues automatically"
    )
    doctor_parser.add_argument(
        "--ack",
        metavar="ADVISORY_ID",
        default=None,
        help=(
            "Acknowledge a security advisory by ID and exit. After ack, the "
            "advisory will no longer trigger startup banners. Run `intellect "
            "doctor` first to see active advisories and their IDs."
        ),
    )
    doctor_parser.add_argument(
        "--storage",
        action="store_true",
        help="Check storage.backend, state.db, PostgreSQL connectivity, and OAuth row counts",
    )
    doctor_parser.add_argument(
        "--perf",
        action="store_true",
        help="Run quick performance diagnostics (import time, DB init, config load)",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    # =========================================================================
    # security command — on-demand supply-chain audit
    # =========================================================================
    security_parser = subparsers.add_parser(
        "security",
        help="Supply-chain audit (OSV.dev) for venv, plugins, and MCP servers",
        description=(
            "On-demand vulnerability scan against OSV.dev. Covers the Intellect "
            "venv (installed PyPI dists), Python deps declared by plugins under "
            "~/.intellect/plugins/, and pinned npx/uvx MCP servers in config.yaml. "
            "Does NOT scan globally-installed packages or editor/browser extensions."
        ),
    )
    security_subparsers = security_parser.add_subparsers(
        dest="security_command",
        metavar="<subcommand>",
    )

    audit_parser = security_subparsers.add_parser(
        "audit",
        help="Run a one-shot supply-chain audit",
        description="Query OSV.dev for known vulnerabilities in installed components.",
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text",
    )
    audit_parser.add_argument(
        "--fail-on",
        default="critical",
        choices=["low", "moderate", "high", "critical"],
        help="Exit non-zero when any finding meets this severity (default: critical)",
    )
    audit_parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="Skip scanning the Intellect Python venv",
    )
    audit_parser.add_argument(
        "--skip-plugins",
        action="store_true",
        help="Skip scanning plugin requirements files",
    )
    audit_parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Skip scanning pinned MCP servers in config.yaml",
    )
    audit_parser.set_defaults(func=cmd_security)
    security_parser.set_defaults(func=cmd_security)

    # =========================================================================
    # dump command
    # =========================================================================
    dump_parser = subparsers.add_parser(
        "dump",
        help="Dump setup summary for support/debugging",
        description="Output a compact, plain-text summary of your Intellect setup "
        "that can be copy-pasted into Discord/GitHub for support context",
    )
    dump_parser.add_argument(
        "--show-keys",
        action="store_true",
        help="Show redacted API key prefixes (first/last 4 chars) instead of just set/not set",
    )
    dump_parser.set_defaults(func=cmd_dump)

    # =========================================================================
    # debug command
    # =========================================================================
    debug_parser = subparsers.add_parser(
        "debug",
        help="Debug tools — upload logs and system info for support",
        description="Debug utilities for Intellect Agent. Use 'intellect debug share' to "
        "upload a debug report (system info + recent logs) to a paste "
        "service and get a shareable URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    intellect debug share              Upload debug report and print URL
    intellect debug share --lines 500  Include more log lines
    intellect debug share --expire 30  Keep paste for 30 days
    intellect debug share --local      Print report locally (no upload)
    intellect debug share --no-redact  Disable upload-time secret redaction
    intellect debug delete <url>       Delete a previously uploaded paste
""",
    )
    debug_sub = debug_parser.add_subparsers(dest="debug_command")
    share_parser = debug_sub.add_parser(
        "share",
        help="Upload debug report to a paste service and print a shareable URL",
    )
    share_parser.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Number of log lines to include per log file (default: 200)",
    )
    share_parser.add_argument(
        "--expire",
        type=int,
        default=7,
        help="Paste expiry in days (default: 7)",
    )
    share_parser.add_argument(
        "--local",
        action="store_true",
        help="Print the report locally instead of uploading",
    )
    share_parser.add_argument(
        "--no-redact",
        action="store_true",
        help=(
            "Disable upload-time secret redaction (default: redact). Logs "
            "are normally run through agent.redact.redact_sensitive_text "
            "with force=True before upload so credentials are not leaked "
            "into the public paste service."
        ),
    )
    delete_parser = debug_sub.add_parser(
        "delete",
        help="Delete a paste uploaded by 'intellect debug share'",
    )
    delete_parser.add_argument(
        "urls",
        nargs="*",
        default=[],
        help="One or more paste URLs to delete (e.g. https://paste.rs/abc123)",
    )
    debug_parser.set_defaults(func=cmd_debug)

    # =========================================================================
    # backup command
    # =========================================================================
    backup_parser = subparsers.add_parser(
        "backup",
        help="Back up Intellect home directory to a zip file",
        description="Create a zip archive of your entire Intellect configuration, "
        "skills, sessions, and data (excludes the intellect-agent codebase). "
        "Use --quick for a fast snapshot of just critical state files.",
    )
    backup_parser.add_argument(
        "-o",
        "--output",
        help="Output path for the zip file (default: ~/intellect-backup-<timestamp>.zip)",
    )
    backup_parser.add_argument(
        "-q",
        "--quick",
        action="store_true",
        help="Quick snapshot: only critical state files (config, state.db, .env, auth, cron)",
    )
    backup_parser.add_argument(
        "-l", "--label", help="Label for the snapshot (only used with --quick)"
    )
    backup_parser.set_defaults(func=cmd_backup)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    checkpoints_parser = subparsers.add_parser(
        "checkpoints",
        help="Inspect / prune / clear ~/.intellect/checkpoints/",
        description="Manage the filesystem checkpoint store — the shadow git "
        "repo intellect uses to snapshot working directories before "
        "write_file/patch/terminal calls. Lets you see how much "
        "space checkpoints occupy, force a prune, or wipe the base.",
    )
    from intellect_cli.checkpoints import register_cli as _register_checkpoints_cli
    _register_checkpoints_cli(checkpoints_parser)

    # =========================================================================
    # import command
    # =========================================================================
    import_parser = subparsers.add_parser(
        "import",
        help="Restore an Intellect backup from a zip file",
        description="Extract a previously created Intellect backup into your "
        "Intellect home directory, restoring configuration, skills, "
        "sessions, and data",
    )
    import_parser.add_argument("zipfile", help="Path to the backup zip file")
    import_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing files without confirmation",
    )
    import_parser.set_defaults(func=cmd_import)

    # =========================================================================
    # config command
    # =========================================================================
    config_parser = subparsers.add_parser(
        "config",
        help="View and edit configuration",
        description="Manage Intellect Agent configuration",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    # config show (default)
    config_subparsers.add_parser("show", help="Show current configuration")

    # config edit
    config_subparsers.add_parser("edit", help="Open config file in editor")

    # config set
    config_set = config_subparsers.add_parser("set", help="Set a configuration value")
    config_set.add_argument(
        "key", nargs="?", help="Configuration key (e.g., model, terminal.backend)"
    )
    config_set.add_argument("value", nargs="?", help="Value to set")

    # config path
    config_subparsers.add_parser("path", help="Print config file path")

    # config env-path
    config_subparsers.add_parser("env-path", help="Print .env file path")

    # config check
    config_subparsers.add_parser("check", help="Check for missing/outdated config")

    # config migrate
    config_subparsers.add_parser("migrate", help="Update config with new options")

    config_parser.set_defaults(func=cmd_config)

    # =========================================================================
    # pairing command
    # =========================================================================
    pairing_parser = subparsers.add_parser(
        "pairing",
        help="Manage DM pairing codes for user authorization",
        description="Approve or revoke user access via pairing codes",
    )
    pairing_sub = pairing_parser.add_subparsers(dest="pairing_action")

    pairing_sub.add_parser("list", help="Show pending + approved users")

    pairing_approve_parser = pairing_sub.add_parser(
        "approve", help="Approve a pairing code"
    )
    pairing_approve_parser.add_argument(
        "platform", help="Platform name (telegram, discord, slack, whatsapp)"
    )
    pairing_approve_parser.add_argument("code", help="Pairing code to approve")

    pairing_revoke_parser = pairing_sub.add_parser("revoke", help="Revoke user access")
    pairing_revoke_parser.add_argument("platform", help="Platform name")
    pairing_revoke_parser.add_argument("user_id", help="User ID to revoke")

    pairing_sub.add_parser("clear-pending", help="Clear all pending codes")

    def cmd_pairing(args):
        from intellect_cli.pairing import pairing_command

        pairing_command(args)

    pairing_parser.set_defaults(func=cmd_pairing)

    # =========================================================================
    # skills command
    # =========================================================================
    skills_parser = subparsers.add_parser(
        "skills",
        help="Search, install, configure, and manage skills",
        description="Search, install, inspect, audit, configure, and manage skills from skills.sh, well-known agent skill endpoints, GitHub, ClawHub, and other registries.",
    )
    skills_subparsers = skills_parser.add_subparsers(dest="skills_action")

    skills_browse = skills_subparsers.add_parser(
        "browse", help="Browse all available skills (paginated)"
    )
    skills_browse.add_argument(
        "--page", type=int, default=1, help="Page number (default: 1)"
    )
    skills_browse.add_argument(
        "--size", type=int, default=20, help="Results per page (default: 20)"
    )
    skills_browse.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
            "browse-sh",
        ],
        help="Filter by source (default: all)",
    )

    skills_search = skills_subparsers.add_parser(
        "search", help="Search skill registries"
    )
    skills_search.add_argument("query", help="Search query")
    skills_search.add_argument(
        "--source",
        default="all",
        choices=[
            "all",
            "official",
            "skills-sh",
            "well-known",
            "github",
            "clawhub",
            "lobehub",
            "browse-sh",
        ],
    )
    skills_search.add_argument("--limit", type=int, default=10, help="Max results")
    skills_search.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of a table (full identifiers, scripting-friendly)",
    )

    skills_install = skills_subparsers.add_parser("install", help="Install a skill")
    skills_install.add_argument(
        "identifier",
        help="Skill identifier (e.g. openai/skills/skill-creator) or a direct HTTP(S) URL to a SKILL.md file",
    )
    skills_install.add_argument(
        "--category", default="", help="Category folder to install into"
    )
    skills_install.add_argument(
        "--name",
        default="",
        help="Override the skill name (useful when installing from a URL whose SKILL.md has no `name:` frontmatter)",
    )
    skills_install.add_argument(
        "--force", action="store_true", help="Install despite blocked scan verdict"
    )
    skills_install.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt (needed in TUI mode)",
    )

    skills_inspect = skills_subparsers.add_parser(
        "inspect", help="Preview a skill without installing"
    )
    skills_inspect.add_argument("identifier", help="Skill identifier")

    skills_list = skills_subparsers.add_parser("list", help="List installed skills")
    skills_list.add_argument(
        "--source", default="all", choices=["all", "hub", "builtin", "local"]
    )
    skills_list.add_argument(
        "--enabled-only",
        action="store_true",
        help="Hide disabled skills. Use with -p <profile> to see exactly "
        "which skills will load for that profile.",
    )

    skills_check = skills_subparsers.add_parser(
        "check", help="Check installed hub skills for updates"
    )
    skills_check.add_argument(
        "name", nargs="?", help="Specific skill to check (default: all)"
    )

    skills_update = skills_subparsers.add_parser(
        "update", help="Update installed hub skills"
    )
    skills_update.add_argument(
        "name",
        nargs="?",
        help="Specific skill to update (default: all outdated skills)",
    )

    skills_audit = skills_subparsers.add_parser(
        "audit", help="Re-scan installed hub skills"
    )
    skills_audit.add_argument(
        "name", nargs="?", help="Specific skill to audit (default: all)"
    )
    skills_audit.add_argument(
        "--deep",
        action="store_true",
        help="Run AST-level analysis on Python files (opt-in diagnostic)",
    )

    skills_uninstall = skills_subparsers.add_parser(
        "uninstall", help="Remove a hub-installed skill"
    )
    skills_uninstall.add_argument("name", help="Skill name to remove")

    skills_reset = skills_subparsers.add_parser(
        "reset",
        help="Reset a bundled skill — clears 'user-modified' tracking so updates work again",
        description=(
            "Clear a bundled skill's entry from the sync manifest (~/.intellect/skills/.bundled_manifest) "
            "so future 'intellect update' runs stop marking it as user-modified. Pass --restore to also "
            "replace the current copy with the bundled version."
        ),
    )
    skills_reset.add_argument(
        "name", help="Skill name to reset (e.g. google-workspace)"
    )
    skills_reset.add_argument(
        "--restore",
        action="store_true",
        help="Also delete the current copy and re-copy the bundled version",
    )
    skills_reset.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt when using --restore",
    )

    skills_repair_official = skills_subparsers.add_parser(
        "repair-official",
        help="Backfill or restore official optional skills from repo source",
        description=(
            "Repair official optional skill provenance. By default, only backfills "
            "hub metadata for exact matches. Pass --restore to replace missing or "
            "mutated active copies from optional-skills/, moving existing copies to "
            "a restore backup first. Use name 'all' to repair every optional skill."
        ),
    )
    skills_repair_official.add_argument(
        "name", help="Official optional skill folder/frontmatter name, or 'all'"
    )
    skills_repair_official.add_argument(
        "--restore",
        action="store_true",
        help="Restore from official optional source, backing up existing matching copies",
    )
    skills_repair_official.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt when using --restore",
    )

    skills_publish = skills_subparsers.add_parser(
        "publish", help="Publish a skill to a registry"
    )
    skills_publish.add_argument("skill_path", help="Path to skill directory")
    skills_publish.add_argument(
        "--to", default="github", choices=["github", "clawhub"], help="Target registry"
    )
    skills_publish.add_argument(
        "--repo", default="", help="Target GitHub repo (e.g. openai/skills)"
    )

    skills_snapshot = skills_subparsers.add_parser(
        "snapshot", help="Export/import skill configurations"
    )
    snapshot_subparsers = skills_snapshot.add_subparsers(dest="snapshot_action")
    snap_export = snapshot_subparsers.add_parser(
        "export", help="Export installed skills to a file"
    )
    snap_export.add_argument("output", help="Output JSON file path (use - for stdout)")
    snap_import = snapshot_subparsers.add_parser(
        "import", help="Import and install skills from a file"
    )
    snap_import.add_argument("input", help="Input JSON file path")
    snap_import.add_argument(
        "--force", action="store_true", help="Force install despite caution verdict"
    )

    skills_tap = skills_subparsers.add_parser("tap", help="Manage skill sources")
    tap_subparsers = skills_tap.add_subparsers(dest="tap_action")
    tap_subparsers.add_parser("list", help="List configured taps")
    tap_add = tap_subparsers.add_parser("add", help="Add a GitHub repo as skill source")
    tap_add.add_argument("repo", help="GitHub repo (e.g. owner/repo)")
    tap_rm = tap_subparsers.add_parser("remove", help="Remove a tap")
    tap_rm.add_argument("name", help="Tap name to remove")

    # config sub-action: interactive enable/disable
    skills_subparsers.add_parser(
        "config",
        help="Interactive skill configuration — enable/disable individual skills",
    )

    def cmd_skills(args):
        # Route 'config' action to skills_config module
        if getattr(args, "skills_action", None) == "config":
            _require_tty("skills config")
            from intellect_cli.skills_config import skills_command as skills_config_command

            skills_config_command(args)
        else:
            from intellect_cli.skills_hub import skills_command

            skills_command(args)

    skills_parser.set_defaults(func=cmd_skills)

    # =========================================================================
    # bundles command — skill bundles (alias /<name> for multiple skills)
    # =========================================================================
    bundles_parser = subparsers.add_parser(
        "bundles",
        help="Create, list, and manage skill bundles (aliases for multiple skills)",
        description=(
            "Skill bundles let you load several skills under one slash "
            "command. `/<bundle>` from the CLI or gateway loads every "
            "referenced skill at once."
        ),
    )
    from intellect_cli.bundles import register_cli as _bundles_register, bundles_command
    _bundles_register(bundles_parser)
    bundles_parser.set_defaults(func=bundles_command)

    # =========================================================================
    # plugins command
    # =========================================================================
    plugins_parser = subparsers.add_parser(
        "plugins",
        help="Manage plugins — install, update, remove, list",
        description="Install plugins from Git repositories, update, remove, or list them.",
    )
    plugins_subparsers = plugins_parser.add_subparsers(dest="plugins_action")

    plugins_install = plugins_subparsers.add_parser(
        "install", help="Install a plugin from a Git URL or owner/repo"
    )
    plugins_install.add_argument(
        "identifier",
        help="Git URL or owner/repo shorthand (e.g. anpicasso/intellect-plugin-chrome-profiles)",
    )
    plugins_install.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Remove existing plugin and reinstall",
    )
    _install_enable_group = plugins_install.add_mutually_exclusive_group()
    _install_enable_group.add_argument(
        "--enable",
        action="store_true",
        help="Auto-enable the plugin after install (skip confirmation prompt)",
    )
    _install_enable_group.add_argument(
        "--no-enable",
        action="store_true",
        help="Install disabled (skip confirmation prompt); enable later with `intellect plugins enable <name>`",
    )

    plugins_update = plugins_subparsers.add_parser(
        "update", help="Pull latest changes for an installed plugin"
    )
    plugins_update.add_argument("name", help="Plugin name to update")

    plugins_remove = plugins_subparsers.add_parser(
        "remove", aliases=["rm", "uninstall"], help="Remove an installed plugin"
    )
    plugins_remove.add_argument("name", help="Plugin directory name to remove")

    plugins_list = plugins_subparsers.add_parser(
        "list", aliases=["ls"], help="List installed plugins"
    )
    plugins_list.add_argument(
        "--enabled",
        action="store_true",
        help="Show only enabled plugins",
    )
    plugins_list.add_argument(
        "--user",
        action="store_true",
        help="Show only user-installed plugins (including git plugins)",
    )
    plugins_list.add_argument(
        "--no-bundled",
        action="store_true",
        help="Hide bundled plugins",
    )
    plugins_list.add_argument(
        "--plain",
        action="store_true",
        help="Print compact plain-text output instead of a Rich table",
    )
    plugins_list.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )

    plugins_enable = plugins_subparsers.add_parser(
        "enable", help="Enable a disabled plugin"
    )
    plugins_enable.add_argument("name", help="Plugin name to enable")

    plugins_disable = plugins_subparsers.add_parser(
        "disable", help="Disable a plugin without removing it"
    )
    plugins_disable.add_argument("name", help="Plugin name to disable")

    def cmd_plugins(args):
        from intellect_cli.plugins_cmd import plugins_command

        plugins_command(args)

    plugins_parser.set_defaults(func=cmd_plugins)

    # =========================================================================
    # Plugin CLI commands — dynamically registered by memory/general plugins.
    # Plugins provide a register_cli(subparser) function that builds their
    # own argparse tree.  No hardcoded plugin commands in main.py.
    #
    # Skipped when the invocation is already targeting a known built-in
    # subcommand — ``intellect --help``, ``intellect version``, ``intellect logs``,
    # etc.  This avoids eagerly importing every bundled plugin module
    # (google.cloud.pubsub_v1, aiohttp, grpc, PIL …) which costs
    # 500-650ms on typical installs.
    # =========================================================================
    if _plugin_cli_discovery_needed():
        try:
            # Lazy imports — each pulls ~500ms of heavy deps.
            # Only trigger when a plugin subcommand is actually invoked.
            from plugins.memory import discover_plugin_cli_commands as _mem_discover
            from plugins.rag import discover_plugin_cli_commands as _rag_discover
            from intellect_cli.plugins import discover_plugins, get_plugin_manager

            seen_plugin_commands = set()
            for cmd_info in _mem_discover():
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
                seen_plugin_commands.add(cmd_info["name"])

            for cmd_info in _rag_discover():
                if cmd_info["name"] in seen_plugin_commands:
                    continue
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
                seen_plugin_commands.add(cmd_info["name"])

            discover_plugins()
            for cmd_info in get_plugin_manager()._cli_commands.values():
                if cmd_info["name"] in seen_plugin_commands:
                    continue
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
        except Exception as _exc:
            logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)

    # =========================================================================
    # curator command — background skill maintenance
    # =========================================================================
    curator_parser = subparsers.add_parser(
        "curator",
        help="Background skill maintenance (curator) — status, run, pause, pin",
        description=(
            "The curator is an auxiliary-model background task that "
            "periodically reviews agent-created skills, prunes stale ones, "
            "consolidates overlaps, and archives obsolete skills. "
            "Bundled and hub-installed skills are never touched. "
            "Archives are recoverable; auto-deletion never happens."
        ),
    )
    try:
        from intellect_cli.curator import register_cli as _register_curator_cli

        _register_curator_cli(curator_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("curator CLI wiring failed: %s", _exc)

    # =========================================================================
    # db command — storage migration (P2)
    # =========================================================================
    db_parser = subparsers.add_parser(
        "db",
        help="Database storage — migrate SQLite to PostgreSQL, test connectivity",
    )
    try:
        from intellect_cli.db_cmd import register_cli as _register_db_cli

        _register_db_cli(db_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("db CLI wiring failed: %s", _exc)

    # =========================================================================
    # memory command
    # =========================================================================
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker",
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )

    def cmd_memory(args):
        sub = getattr(args, "memory_command", None)
        if sub == "off":
            from intellect_cli.config import load_config, save_config

            config = load_config()
            if not isinstance(config.get("memory"), dict):
                config["memory"] = {}
            config["memory"]["provider"] = ""
            save_config(config)
            print("\n  ✓ Memory provider: built-in only")
            print("  Saved to config.yaml\n")
        elif sub == "reset":
            from intellect_constants import get_intellect_home, display_intellect_home

            mem_dir = get_intellect_home() / "memories"
            target = getattr(args, "target", "all")
            files_to_reset = []
            if target in {"all", "memory"}:
                files_to_reset.append(("MEMORY.md", "agent notes"))
            if target in {"all", "user"}:
                files_to_reset.append(("USER.md", "user profile"))

            # Check what exists
            existing = [
                (f, desc) for f, desc in files_to_reset if (mem_dir / f).exists()
            ]
            if not existing:
                print(
                    f"\n  Nothing to reset — no memory files found in {display_intellect_home()}/memories/\n"
                )
                return

            print(f"\n  This will permanently erase the following memory files:")
            for f, desc in existing:
                path = mem_dir / f
                size = path.stat().st_size
                print(f"    ◆ {f} ({desc}) — {size:,} bytes")

            if not getattr(args, "yes", False):
                try:
                    answer = input("\n  Type 'yes' to confirm: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\n  Cancelled.\n")
                    return
                if answer != "yes":
                    print("  Cancelled.\n")
                    return

            for f, desc in existing:
                (mem_dir / f).unlink()
                print(f"  ✓ Deleted {f} ({desc})")

            print(
                f"\n  Memory reset complete. New sessions will start with a blank slate."
            )
            print(f"  Files were in: {display_intellect_home()}/memories/\n")
        else:
            from intellect_cli.memory_setup import memory_command

            memory_command(args)

    memory_parser.set_defaults(func=cmd_memory)

    # =========================================================================
    # tools command
    # =========================================================================
    tools_parser = subparsers.add_parser(
        "tools",
        help="Configure which tools are enabled per platform",
        description=(
            "Enable, disable, or list tools for CLI, Telegram, Discord, etc.\n\n"
            "Built-in toolsets use plain names (e.g. web, memory).\n"
            "MCP tools use server:tool notation (e.g. github:create_issue).\n\n"
            "Run 'intellect tools' with no subcommand for the interactive configuration UI."
        ),
    )
    tools_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of enabled tools per platform and exit",
    )
    tools_sub = tools_parser.add_subparsers(dest="tools_action")

    # intellect tools list [--platform cli]
    tools_list_p = tools_sub.add_parser(
        "list",
        help="Show all tools and their enabled/disabled status",
    )
    tools_list_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to show (default: cli)",
    )

    # intellect tools disable <name...> [--platform cli]
    tools_disable_p = tools_sub.add_parser(
        "disable",
        help="Disable toolsets or MCP tools",
    )
    tools_disable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help="Toolset name (e.g. web) or MCP tool in server:tool form",
    )
    tools_disable_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to apply to (default: cli)",
    )

    # intellect tools enable <name...> [--platform cli]
    tools_enable_p = tools_sub.add_parser(
        "enable",
        help="Enable toolsets or MCP tools",
    )
    tools_enable_p.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help="Toolset name or MCP tool in server:tool form",
    )
    tools_enable_p.add_argument(
        "--platform",
        default="cli",
        help="Platform to apply to (default: cli)",
    )

    def cmd_tools(args):
        action = getattr(args, "tools_action", None)
        if action in {"list", "disable", "enable"}:
            from intellect_cli.tools_config import tools_disable_enable_command

            tools_disable_enable_command(args)
        else:
            _require_tty("tools")
            from intellect_cli.tools_config import tools_command

            tools_command(args)

    tools_parser.set_defaults(func=cmd_tools)

    # =========================================================================
    # computer-use command — manage Computer Use (cua-driver) on macOS
    # =========================================================================
    computer_use_parser = subparsers.add_parser(
        "computer-use",
        help="Manage the Computer Use (cua-driver) backend (macOS)",
        description=(
            "Install or check the cua-driver binary used by the\n"
            "`computer_use` toolset. macOS-only.\n\n"
            "Use `intellect computer-use install` to fetch and run the\n"
            "upstream cua-driver installer. This is equivalent to the\n"
            "post-setup hook that `intellect tools` runs when you first\n"
            "enable the Computer Use toolset, and is a stable target\n"
            "for re-running the install if it didn't fire (e.g. when\n"
            "toggling the toolset on a returning-user setup)."
        ),
    )
    computer_use_sub = computer_use_parser.add_subparsers(dest="computer_use_action")

    computer_use_install = computer_use_sub.add_parser(
        "install",
        help="Install or repair the cua-driver binary (macOS)",
    )
    computer_use_install.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Re-run the upstream installer even if cua-driver is already on "
            "PATH. The upstream install.sh always pulls the latest release, "
            "so this performs an in-place upgrade."
        ),
    )
    computer_use_sub.add_parser(
        "status",
        help="Print whether cua-driver is installed and on PATH",
    )

    def cmd_computer_use(args):
        action = getattr(args, "computer_use_action", None)
        if action == "install":
            from intellect_cli.tools_config import install_cua_driver
            install_cua_driver(upgrade=bool(getattr(args, "upgrade", False)))
            return
        if action == "status":
            import shutil
            import subprocess
            path = shutil.which("cua-driver")
            if path:
                version = ""
                try:
                    version = subprocess.run(
                        ["cua-driver", "--version"],
                        capture_output=True, text=True, timeout=5,
                    ).stdout.strip()
                except Exception:
                    logger.debug('non-critical operation failed', exc_info=True)
                if version:
                    print(f"cua-driver: installed at {path} ({version})")
                else:
                    print(f"cua-driver: installed at {path}")
                print("  Refresh to latest: intellect computer-use install --upgrade")
                return
            print("cua-driver: not installed")
            print("  Run: intellect computer-use install")
            return
        # No subcommand → show help
        computer_use_parser.print_help()

    computer_use_parser.set_defaults(func=cmd_computer_use)
    # =========================================================================
    # mcp command — manage MCP server connections
    # =========================================================================
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Manage MCP servers and run Intellect as an MCP server",
        description=(
            "Manage MCP server connections and run Intellect as an MCP server.\n\n"
            "MCP servers provide additional tools via the Model Context Protocol.\n"
            "Use 'intellect mcp add' to connect to a new server, or\n"
            "'intellect mcp serve' to expose Intellect conversations over MCP."
        ),
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_serve_p = mcp_sub.add_parser(
        "serve",
        help="Run Intellect as an MCP server (expose conversations to other agents)",
    )
    mcp_serve_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging on stderr",
    )
    _add_accept_hooks_flag(mcp_serve_p)

    mcp_add_p = mcp_sub.add_parser(
        "add", help="Add an MCP server (discovery-first install)"
    )
    mcp_add_p.add_argument("name", help="Server name (used as config key)")
    mcp_add_p.add_argument("--url", help="HTTP/SSE endpoint URL")
    # dest="mcp_command" so this flag does not clobber the top-level
    # subparser's args.command attribute, which the dispatcher reads to
    # route to cmd_mcp.  Without an explicit dest, argparse derives
    # dest="command" from the flag name and sets it to None when the
    # flag is omitted, causing `intellect mcp add ...` to fall through to
    # interactive chat.
    mcp_add_p.add_argument(
        "--command", dest="mcp_command", help="Stdio command (e.g. npx)"
    )
    mcp_add_p.add_argument(
        "--args", nargs="*", default=[], help="Arguments for stdio command"
    )
    mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help="Auth method")
    mcp_add_p.add_argument("--preset", help="Known MCP preset name")
    mcp_add_p.add_argument(
        "--env",
        nargs="*",
        default=[],
        help="Environment variables for stdio servers (KEY=VALUE)",
    )

    mcp_rm_p = mcp_sub.add_parser("remove", aliases=["rm"], help="Remove an MCP server")
    mcp_rm_p.add_argument("name", help="Server name to remove")

    mcp_sub.add_parser("list", aliases=["ls"], help="List configured MCP servers")

    mcp_test_p = mcp_sub.add_parser("test", help="Test MCP server connection")
    mcp_test_p.add_argument("name", help="Server name to test")

    mcp_cfg_p = mcp_sub.add_parser(
        "configure", aliases=["config"], help="Toggle tool selection"
    )
    mcp_cfg_p.add_argument("name", help="Server name to configure")

    mcp_login_p = mcp_sub.add_parser(
        "login",
        help="Force re-authentication for an OAuth-based MCP server",
    )
    mcp_login_p.add_argument("name", help="Server name to re-authenticate")

    # ── Catalog (ONTOWEB-approved MCPs shipped with the repo) ─────────────────
    mcp_sub.add_parser(
        "picker",
        help="Interactive catalog picker (also the default for `intellect mcp`)",
    )
    mcp_sub.add_parser(
        "catalog",
        help="List ONTOWEB-approved MCPs available for one-click install",
    )
    mcp_install_p = mcp_sub.add_parser(
        "install",
        help="Install a catalog MCP by name (e.g. `intellect mcp install n8n`)",
    )
    mcp_install_p.add_argument(
        "identifier",
        help="Catalog entry name (or `official/<name>`)",
    )

    _add_accept_hooks_flag(mcp_parser)

    def cmd_mcp(args):
        from intellect_cli.mcp_config import mcp_command

        mcp_command(args)

    mcp_parser.set_defaults(func=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="Manage session history (list, rename, export, prune, delete)",
        description="View and manage the SQLite session store",
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
    sessions_list.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_list.add_argument(
        "--limit", type=int, default=20, help="Max sessions to show"
    )

    sessions_export = sessions_subparsers.add_parser(
        "export", help="Export sessions to a JSONL file"
    )
    sessions_export.add_argument(
        "output", help="Output JSONL file path (use - for stdout)"
    )
    sessions_export.add_argument("--source", help="Filter by source")
    sessions_export.add_argument("--session-id", help="Export a specific session")

    sessions_delete = sessions_subparsers.add_parser(
        "delete", help="Delete a specific session"
    )
    sessions_delete.add_argument("session_id", help="Session ID to delete")
    sessions_delete.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
    sessions_prune.add_argument(
        "--older-than",
        type=int,
        default=90,
        help="Delete sessions older than N days (default: 90)",
    )
    sessions_prune.add_argument("--source", help="Only prune sessions from this source")
    sessions_prune.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    sessions_subparsers.add_parser(
        "optimize",
        help="Reclaim disk space: merge FTS5 segments + VACUUM (no data change)",
    )

    sessions_subparsers.add_parser("stats", help="Show session store statistics")

    sessions_rename = sessions_subparsers.add_parser(
        "rename", help="Set or change a session's title"
    )
    sessions_rename.add_argument("session_id", help="Session ID to rename")
    sessions_rename.add_argument("title", nargs="+", help="New title for the session")

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help="Interactive session picker — browse, search, and resume sessions",
    )
    sessions_browse.add_argument(
        "--source", help="Filter by source (cli, telegram, discord, etc.)"
    )
    sessions_browse.add_argument(
        "--limit", type=int, default=500, help="Max sessions to load (default: 500)"
    )

    def _confirm_prompt(prompt: str) -> bool:
        """Prompt for y/N confirmation, safe against non-TTY environments."""
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            return False

    def cmd_sessions(args):
        import json as _json

        try:
            from intellect_state import SessionDB

            db = SessionDB()
        except Exception as e:
            print(f"Error: Could not open session database: {e}")
            return

        action = args.sessions_action

        # Hide third-party tool sessions by default, but honour explicit --source
        _source = getattr(args, "source", None)
        _exclude = None if _source else ["tool"]

        if action == "list":
            sessions = db.list_sessions_rich(
                source=args.source, exclude_sources=_exclude, limit=args.limit
            )
            if not sessions:
                print("No sessions found.")
                return
            has_titles = any(s.get("title") for s in sessions)
            if has_titles:
                print(f"{'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
                print("─" * 110)
            else:
                print(f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}")
                print("─" * 95)
            for s in sessions:
                last_active = _relative_time(s.get("last_active"))
                preview = (
                    s.get("preview", "")[:38]
                    if has_titles
                    else s.get("preview", "")[:48]
                )
                if has_titles:
                    title = (s.get("title") or "—")[:30]
                    sid = s["id"]
                    print(f"{title:<32} {preview:<40} {last_active:<13} {sid}")
                else:
                    sid = s["id"]
                    print(f"{preview:<50} {last_active:<13} {s['source']:<6} {sid}")

        elif action == "export":
            if args.session_id:
                resolved_session_id = db.resolve_session_id(args.session_id)
                if not resolved_session_id:
                    print(f"Session '{args.session_id}' not found.")
                    return
                data = db.export_session(resolved_session_id)
                if not data:
                    print(f"Session '{args.session_id}' not found.")
                    return
                line = _json.dumps(data, ensure_ascii=False) + "\n"
                if args.output == "-":

                    sys.stdout.write(line)
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(line)
                    print(f"Exported 1 session to {args.output}")
            else:
                sessions = db.export_all(source=args.source)
                if args.output == "-":

                    for s in sessions:
                        sys.stdout.write(_json.dumps(s, ensure_ascii=False) + "\n")
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        for s in sessions:
                            f.write(_json.dumps(s, ensure_ascii=False) + "\n")
                    print(f"Exported {len(sessions)} sessions to {args.output}")

        elif action == "delete":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete session '{resolved_session_id}' and all its messages? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_intellect_home() / "sessions"
            if db.delete_session(resolved_session_id, sessions_dir=sessions_dir):
                print(f"Deleted session '{resolved_session_id}'.")
            else:
                print(f"Session '{args.session_id}' not found.")

        elif action == "prune":
            days = args.older_than
            source_msg = f" from '{args.source}'" if args.source else ""
            if not args.yes:
                if not _confirm_prompt(
                    f"Delete all ended sessions older than {days} days{source_msg}? [y/N] "
                ):
                    print("Cancelled.")
                    return
            sessions_dir = get_intellect_home() / "sessions"
            count = db.prune_sessions(
                older_than_days=days, source=args.source, sessions_dir=sessions_dir
            )
            print(f"Pruned {count} session(s).")

        elif action == "rename":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            title = " ".join(args.title)
            try:
                if db.set_session_title(resolved_session_id, title):
                    print(f"Session '{resolved_session_id}' renamed to: {title}")
                else:
                    print(f"Session '{args.session_id}' not found.")
            except ValueError as e:
                print(f"Error: {e}")

        elif action == "browse":
            limit = getattr(args, "limit", 500) or 500
            source = getattr(args, "source", None)
            _browse_exclude = None if source else ["tool"]
            sessions = db.list_sessions_rich(
                source=source, exclude_sources=_browse_exclude, limit=limit
            )
            db.close()
            if not sessions:
                print("No sessions found.")
                return

            selected_id = _session_browse_picker(sessions)
            if not selected_id:
                print("Cancelled.")
                return

            # Launch intellect --resume <id> by replacing the current process
            print(f"Resuming session: {selected_id}")
            from intellect_cli.relaunch import relaunch

            relaunch(["--resume", selected_id])
            return  # won't reach here after execvp

        elif action == "optimize":
            db_path = db.db_path
            before_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            print("Optimizing session store (FTS merge + VACUUM)…")
            try:
                # vacuum() merges FTS5 segments (optimize_fts) then VACUUMs,
                # and returns the number of indexes it merged.
                n = db.vacuum()
            except Exception as e:
                print(f"Error: optimization failed: {e}")
                db.close()
                return
            after_mb = (
                os.path.getsize(db_path) / (1024 * 1024)
                if db_path.exists()
                else 0.0
            )
            saved = before_mb - after_mb
            print(f"Optimized {n} FTS index(es).")
            print(
                f"Database size: {before_mb:.1f} MB -> {after_mb:.1f} MB "
                f"(reclaimed {saved:.1f} MB)"
            )

        elif action == "stats":
            total = db.session_count()
            msgs = db.message_count()
            print(f"Total sessions: {total}")
            print(f"Total messages: {msgs}")
            for src in ["cli", "telegram", "discord", "whatsapp", "slack"]:
                c = db.session_count(source=src)
                if c > 0:
                    print(f"  {src}: {c} sessions")
            db_path = db.db_path
            if db_path.exists():
                size_mb = os.path.getsize(db_path) / (1024 * 1024)
                print(f"Database size: {size_mb:.1f} MB")

        else:
            sessions_parser.print_help()

        db.close()

    sessions_parser.set_defaults(func=cmd_sessions)

    # =========================================================================
    # insights command
    # =========================================================================
    insights_parser = subparsers.add_parser(
        "insights",
        help="Show usage insights and analytics",
        description="Analyze session history to show token usage, costs, tool patterns, and activity trends",
    )
    insights_parser.add_argument(
        "--days", type=int, default=30, help="Number of days to analyze (default: 30)"
    )
    insights_parser.add_argument(
        "--source", help="Filter by platform (cli, telegram, discord, etc.)"
    )

    def cmd_insights(args):
        try:
            from intellect_state import SessionDB
            from agent.insights import InsightsEngine

            db = SessionDB()
            engine = InsightsEngine(db)
            report = engine.generate(days=args.days, source=args.source)
            print(engine.format_terminal(report))
            db.close()
        except Exception as e:
            print(f"Error generating insights: {e}")

    insights_parser.set_defaults(func=cmd_insights)

    # =========================================================================
    # claw command (OpenClaw migration)
    # =========================================================================
    claw_parser = subparsers.add_parser(
        "claw",
        help="OpenClaw migration tools",
        description="Migrate settings, memories, skills, and API keys from OpenClaw to Intellect",
    )
    claw_subparsers = claw_parser.add_subparsers(dest="claw_action")

    # claw migrate
    claw_migrate = claw_subparsers.add_parser(
        "migrate",
        help="Migrate from OpenClaw to Intellect",
        description="Import settings, memories, skills, and API keys from an OpenClaw installation. "
        "Always shows a preview before making changes.",
    )
    claw_migrate.add_argument(
        "--source", help="Path to OpenClaw directory (default: ~/.openclaw)"
    )
    claw_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only — stop after showing what would be migrated",
    )
    claw_migrate.add_argument(
        "--preset",
        choices=["user-data", "full"],
        default="full",
        help="Migration preset (default: full). Neither preset imports secrets — "
        "pass --migrate-secrets to include API keys.",
    )
    claw_migrate.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files (default: refuse to apply when the plan has conflicts)",
    )
    claw_migrate.add_argument(
        "--migrate-secrets",
        action="store_true",
        help="Include allowlisted secrets (TELEGRAM_BOT_TOKEN, API keys, etc.). "
        "Required even under --preset full.",
    )
    claw_migrate.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-migration zip snapshot of ~/.intellect/ (by default a "
        "single restore-point archive is written to ~/.intellect/backups/ "
        "before apply; restorable with 'intellect import').",
    )
    claw_migrate.add_argument(
        "--workspace-target", help="Absolute path to copy workspace instructions into"
    )
    claw_migrate.add_argument(
        "--skill-conflict",
        choices=["skip", "overwrite", "rename"],
        default="skip",
        help="How to handle skill name conflicts (default: skip)",
    )
    claw_migrate.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompts"
    )

    # claw cleanup
    claw_cleanup = claw_subparsers.add_parser(
        "cleanup",
        aliases=["clean"],
        help="Archive leftover OpenClaw directories after migration",
        description="Scan for and archive leftover OpenClaw directories to prevent state fragmentation",
    )
    claw_cleanup.add_argument(
        "--source", help="Path to a specific OpenClaw directory to clean up"
    )
    claw_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be archived without making changes",
    )
    claw_cleanup.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompts"
    )

    def cmd_claw(args):
        from intellect_cli.claw import claw_command

        claw_command(args)

    claw_parser.set_defaults(func=cmd_claw)

    # =========================================================================
    # version command
    # =========================================================================
    version_parser = subparsers.add_parser("version", help="Show version information")
    version_parser.set_defaults(func=cmd_version)

    # =========================================================================
    # update command
    # =========================================================================
    update_parser = subparsers.add_parser(
        "update",
        help="Update Intellect Agent to the latest version",
        description="Pull the latest changes from git and reinstall dependencies",
    )
    update_parser.add_argument(
        "--gateway",
        action="store_true",
        default=False,
        help="Gateway mode: use file-based IPC for prompts instead of stdin (used internally by /update)",
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Check whether an update is available without installing anything",
    )
    update_parser.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help="Skip the pre-update backup for this run (overrides updates.pre_update_backup)",
    )
    update_parser.add_argument(
        "--backup",
        action="store_true",
        default=False,
        help="Force a pre-update backup for this run (off by default; overrides updates.pre_update_backup)",
    )
    update_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Assume yes for interactive prompts (config migration, stash restore). API-key entry is skipped; run 'intellect config migrate' separately for those.",
    )
    update_parser.add_argument(
        "--branch",
        default=None,
        metavar="NAME",
        help=(
            "Update against this branch instead of the default (main). "
            "If the local checkout is on a different branch, intellect will "
            "switch to the requested branch first (auto-stashing any "
            "uncommitted changes)."
        ),
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Windows: proceed with the update even when another intellect.exe is detected. The concurrent process will likely cause WinError 32 warnings and may leave a reboot-deferred .exe replacement.",
    )
    update_parser.set_defaults(func=cmd_update)

    # =========================================================================
    # uninstall command
    # =========================================================================
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall Intellect Agent",
        description="Remove Intellect Agent from your system. Can keep configs/data for reinstall.",
    )
    uninstall_parser.add_argument(
        "--full",
        action="store_true",
        help="Full uninstall - remove everything including configs and data",
    )
    uninstall_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompts"
    )
    uninstall_parser.set_defaults(func=cmd_uninstall)

    # =========================================================================
    # acp command
    # =========================================================================
    acp_parser = subparsers.add_parser(
        "acp",
        help="Run Intellect Agent as an ACP (Agent Client Protocol) server",
        description="Start Intellect Agent in ACP mode for editor integration (VS Code, Zed, JetBrains)",
    )
    _add_accept_hooks_flag(acp_parser)
    acp_parser.add_argument(
        "--version",
        action="store_true",
        dest="acp_version",
        help="Print Intellect ACP version and exit",
    )
    acp_parser.add_argument(
        "--check",
        action="store_true",
        help="Verify ACP dependencies and adapter imports, then exit",
    )
    acp_parser.add_argument(
        "--setup",
        action="store_true",
        help="Run interactive Intellect provider/model setup for ACP terminal auth",
    )
    acp_parser.add_argument(
        "--setup-browser",
        action="store_true",
        help="Install agent-browser + Playwright Chromium into ~/.intellect/node/ "
             "for browser tool support (idempotent).",
    )
    acp_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        dest="assume_yes",
        help="Accept all prompts (used by --setup-browser to skip the "
             "~400 MB Chromium download confirmation).",
    )

    def cmd_acp(args):
        """Launch Intellect Agent as an ACP server."""
        try:
            from acp_adapter.entry import main as acp_main

            acp_argv = []
            if getattr(args, "acp_version", False):
                acp_argv.append("--version")
            if getattr(args, "check", False):
                acp_argv.append("--check")
            if getattr(args, "setup", False):
                acp_argv.append("--setup")
            if getattr(args, "setup_browser", False):
                acp_argv.append("--setup-browser")
            if getattr(args, "assume_yes", False):
                acp_argv.append("--yes")
            acp_main(acp_argv)
        except ImportError:
            print("ACP dependencies not installed.", file=sys.stderr)
            print("Install them with:  pip install -e '.[acp]'", file=sys.stderr)
            sys.exit(1)

    acp_parser.set_defaults(func=cmd_acp)

    # =========================================================================
    # profile command
    # =========================================================================
    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage profiles — multiple isolated Intellect instances",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")

    profile_subparsers.add_parser("list", help="List all profiles")
    profile_use = profile_subparsers.add_parser(
        "use", help="Set sticky default profile"
    )
    profile_use.add_argument("profile_name", help="Profile name (or 'default')")

    profile_create = profile_subparsers.add_parser(
        "create", help="Create a new profile"
    )
    profile_create.add_argument(
        "profile_name", help="Profile name (lowercase, alphanumeric)"
    )
    profile_create.add_argument(
        "--clone",
        action="store_true",
        help="Copy config.yaml, .env, SOUL.md from active profile",
    )
    profile_create.add_argument(
        "--clone-all",
        action="store_true",
        help="Full copy of active profile (all state)",
    )
    profile_create.add_argument(
        "--clone-from",
        metavar="SOURCE",
        help="Source profile to clone from (default: active)",
    )
    profile_create.add_argument(
        "--no-alias", action="store_true", help="Skip wrapper script creation"
    )
    profile_create.add_argument(
        "--no-skills",
        action="store_true",
        help="Create an empty profile with no bundled skills (opts out of `intellect update` skill sync)",
    )
    profile_create.add_argument(
        "--description",
        default=None,
        help="One- or two-sentence description of what this profile is good at. "
             "Used by the kanban decomposer to route tasks based on role instead "
             "of profile name alone. Skip and add later via `intellect profile describe`.",
    )

    profile_delete = profile_subparsers.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("profile_name", help="Profile to delete")
    profile_delete.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt"
    )

    profile_describe = profile_subparsers.add_parser(
        "describe",
        help="Read or set a profile's description (used by the kanban orchestrator)",
    )
    profile_describe.add_argument(
        "profile_name",
        nargs="?",
        default=None,
        help="Profile to describe (omit + use --all --auto to sweep)",
    )
    profile_describe.add_argument(
        "--text",
        default=None,
        help="Set description to this exact text (overwrites any existing description)",
    )
    profile_describe.add_argument(
        "--auto",
        action="store_true",
        help="Auto-generate description via the auxiliary LLM "
             "(uses auxiliary.profile_describer)",
    )
    profile_describe.add_argument(
        "--overwrite",
        action="store_true",
        help="With --auto, replace user-authored descriptions too (default: only "
             "fill in missing or previously-auto descriptions)",
    )
    profile_describe.add_argument(
        "--all",
        dest="all_missing",
        action="store_true",
        help="With --auto, run on every profile missing a description",
    )

    profile_show = profile_subparsers.add_parser("show", help="Show profile details")
    profile_show.add_argument("profile_name", help="Profile to show")

    profile_alias = profile_subparsers.add_parser(
        "alias", help="Manage wrapper scripts"
    )
    profile_alias.add_argument("profile_name", help="Profile name")
    profile_alias.add_argument(
        "--remove", action="store_true", help="Remove the wrapper script"
    )
    profile_alias.add_argument(
        "--name",
        dest="alias_name",
        metavar="NAME",
        help="Custom alias name (default: profile name)",
    )

    profile_rename = profile_subparsers.add_parser("rename", help="Rename a profile")
    profile_rename.add_argument("old_name", help="Current profile name")
    profile_rename.add_argument("new_name", help="New profile name")

    profile_export = profile_subparsers.add_parser(
        "export", help="Export a profile to archive"
    )
    profile_export.add_argument("profile_name", help="Profile to export")
    profile_export.add_argument(
        "-o", "--output", default=None, help="Output file (default: <name>.tar.gz)"
    )

    profile_import = profile_subparsers.add_parser(
        "import", help="Import a profile from archive"
    )
    profile_import.add_argument("archive", help="Path to .tar.gz archive")
    profile_import.add_argument(
        "--name",
        dest="import_name",
        metavar="NAME",
        help="Profile name (default: inferred from archive)",
    )

    # ---------- Distribution subcommands (issue #20456) ----------
    profile_install = profile_subparsers.add_parser(
        "install",
        help="Install a profile distribution from a git URL or local directory",
        description=(
            "Install a Intellect profile distribution. SOURCE can be a git URL "
            "(github.com/user/repo, https://..., git@...) or a local "
            "directory containing distribution.yaml at its root."
        ),
    )
    profile_install.add_argument(
        "source",
        help="Distribution source (git URL or local directory)",
    )
    profile_install.add_argument(
        "--name", dest="install_name", metavar="NAME",
        help="Override profile name (default: read from manifest)",
    )
    profile_install.add_argument(
        "--alias", action="store_true",
        help="Create a shell wrapper alias for the installed profile",
    )
    profile_install.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing profile of the same name (user data preserved)",
    )
    profile_install.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip manifest preview confirmation",
    )

    profile_update = profile_subparsers.add_parser(
        "update",
        help="Re-pull a distribution and apply updates (user data preserved)",
        description=(
            "Fetch the distribution from its recorded source and overwrite "
            "distribution-owned files (SOUL.md, skills/, cron/, mcp.json). "
            "User data (memories, sessions, auth, .env) is never touched. "
            "config.yaml is preserved unless --force-config is passed."
        ),
    )
    profile_update.add_argument("profile_name", help="Profile to update")
    profile_update.add_argument(
        "--force-config", action="store_true",
        help="Also overwrite config.yaml (normally preserved to keep user overrides)",
    )
    profile_update.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation",
    )

    profile_info = profile_subparsers.add_parser(
        "info",
        help="Show a profile's distribution manifest (version, requirements, source)",
    )
    profile_info.add_argument("profile_name", help="Profile to inspect")

    profile_parser.set_defaults(func=cmd_profile)

    # =========================================================================
    # completion command
    # =========================================================================
    completion_parser = subparsers.add_parser(
        "completion",
        help="Print shell completion script (bash, zsh, or fish)",
    )
    completion_parser.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help="Shell type (default: bash)",
    )
    completion_parser.set_defaults(func=lambda args: cmd_completion(args, parser))

    # =========================================================================
    # logs command
    # =========================================================================
    logs_parser = subparsers.add_parser(
        "logs",
        help="View and filter Intellect log files",
        description="View, tail, and filter agent.log / errors.log / gateway.log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    intellect logs                    Show last 50 lines of agent.log
    intellect logs -f                 Follow agent.log in real time
    intellect logs errors             Show last 50 lines of errors.log
    intellect logs gateway -n 100     Show last 100 lines of gateway.log
    intellect logs --level WARNING    Only show WARNING and above
    intellect logs --session abc123   Filter by session ID
    intellect logs --component tools  Only show tool-related lines
    intellect logs --since 1h         Lines from the last hour
    intellect logs --since 30m -f     Follow, starting from 30 min ago
    intellect logs list               List available log files with sizes
""",
    )
    logs_parser.add_argument(
        "log_name",
        nargs="?",
        default="agent",
        help="Log to view: agent (default), errors, gateway, or 'list' to show available files",
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow the log in real time (like tail -f)",
    )
    logs_parser.add_argument(
        "--level",
        metavar="LEVEL",
        help="Minimum log level to show (DEBUG, INFO, WARNING, ERROR)",
    )
    logs_parser.add_argument(
        "--session",
        metavar="ID",
        help="Filter lines containing this session ID substring",
    )
    logs_parser.add_argument(
        "--since",
        metavar="TIME",
        help="Show lines since TIME ago (e.g. 1h, 30m, 2d)",
    )
    logs_parser.add_argument(
        "--component",
        metavar="NAME",
        help="Filter by component: gateway, agent, tools, cli, cron",
    )
    logs_parser.set_defaults(func=cmd_logs)

    # =========================================================================
    # prompt-size command
    # =========================================================================
    prompt_size_parser = subparsers.add_parser(
        "prompt-size",
        help="Show a byte breakdown of the system prompt + tool schemas",
        description=(
            "Report the fixed prompt budget for a fresh session: system "
            "prompt total, skills index, memory, user profile, and tool-schema "
            "JSON. Runs offline (no API call)."
        ),
    )
    prompt_size_parser.add_argument(
        "--platform",
        default="cli",
        help="Platform to simulate (cli, telegram, discord, ...). Default: cli",
    )
    prompt_size_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the breakdown as JSON",
    )
    prompt_size_parser.set_defaults(func=cmd_prompt_size)

    # =========================================================================
    # analytics command (P6-1)
    # =========================================================================
    analytics_parser = subparsers.add_parser(
        "analytics",
        help="Show skill lifecycle analytics",
        description="Report skill creation, usage, and iteration metrics.",
    )
    analytics_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit analytics as JSON",
    )
    analytics_parser.add_argument(
        "--top", type=int, default=10,
        help="Number of top-used skills to show (default: 10)",
    )
    analytics_parser.set_defaults(func=cmd_analytics)

    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``intellect -c Pokemon Agent Dev`` → ``intellect -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
    from intellect_cli.config import get_container_exec_info

    container_info = get_container_exec_info()
    if container_info:
        _exec_in_container(container_info, sys.argv[1:])
        # Unreachable: os.execvp never returns on success (process is replaced)
        # and raises OSError on failure (which propagates as a traceback).
        sys.exit(1)

    _processed_argv = _coalesce_session_name_args(sys.argv[1:])

    # ── Defensive subparser routing (bpo-9338 workaround) ───────────
    # On some Python versions (notably <3.11), argparse fails to route
    # subcommand tokens when the parent parser has nargs='?' optional
    # arguments (--continue).  The symptom: "unrecognized arguments: model"
    # even though 'model' is a registered subcommand.
    #
    # Fix: when argv contains a token matching a known subcommand, set
    # subparsers.required=True to force deterministic routing.  If that
    # fails (e.g. 'intellect -c model' where 'model' is consumed as the
    # session name for --continue), fall back to the default behaviour.
    import io as _io

    _known_cmds = (
        set(subparsers.choices.keys()) if hasattr(subparsers, "choices") else set()
    )
    _has_cmd_token = any(
        t in _known_cmds for t in _processed_argv if not t.startswith("-")
    )

    if _has_cmd_token:
        subparsers.required = True
        _saved_stderr = sys.stderr
        try:
            sys.stderr = _io.StringIO()
            args = parser.parse_args(_processed_argv)
            sys.stderr = _saved_stderr
        except SystemExit as exc:
            sys.stderr = _saved_stderr
            # Help/version flags (exit code 0) already printed output —
            # re-raise immediately to avoid a second parse_args printing
            # the same help text again (#10230).
            if exc.code == 0:
                raise
            # Subcommand name was consumed as a flag value (e.g. -c model).
            # Fall back to optional subparsers so argparse handles it normally.
            subparsers.required = False
            args = parser.parse_args(_processed_argv)
    else:
        subparsers.required = False
        args = parser.parse_args(_processed_argv)

    # Handle --version flag
    if args.version:
        cmd_version(args)
        return

    # Discover Python plugins and register shell hooks once, before any
    # command that can fire lifecycle hooks.  Both are idempotent; gated
    # so introspection/management commands (intellect hooks list, cron
    # list, gateway status, mcp add, ...) don't pay discovery cost or
    # trigger consent prompts for hooks the user is still inspecting.
    _prepare_agent_startup(args)

    # Handle top-level --oneshot / -z: single-shot mode, stdout = final
    # response only, nothing else. Bypasses cli.py entirely.
    if getattr(args, "oneshot", None):
        from intellect_cli.oneshot import run_oneshot

        sys.exit(
            run_oneshot(
                args.oneshot,
                model=getattr(args, "model", None),
                provider=getattr(args, "provider", None),
                toolsets=getattr(args, "toolsets", None),
            )
        )

    # Handle top-level --resume / --continue as shortcut to chat
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", None),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Default to chat if no command specified
    if args.command is None:
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", None),
            ("resume", None),
            ("continue_last", None),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Execute the command
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
