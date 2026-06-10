"""Slash command routing table for GatewayRunner.

Maps canonical command names to handler method names. Extracted from
gateway/run.py dispatch to reduce monolithic if/elif chain.
"""

# (canonical, handler_method, options)
# handler_method: name of the async method on GatewayRunner
# options: dict with optional flags (needs_confirm, is_deprecated, etc.)
COMMAND_ROUTES: list[tuple[str, str, dict]] = [
    ("topic", "_handle_topic_command", {}),
    ("help", "_handle_help_command", {}),
    ("start", None, {"silent": True}),
    ("commands", "_handle_commands_command", {}),
    ("profile", "_handle_profile_command", {}),
    ("whoami", "_handle_whoami_command", {}),
    ("status", "_handle_status_command", {}),
    ("agents", "_handle_agents_command", {}),
    ("platform", "_handle_platform_command", {}),
    ("restart", "_handle_restart_command", {}),
    ("stop", "_handle_stop_command", {}),
    ("reasoning", "_handle_reasoning_command", {}),
    ("fast", "_handle_fast_command", {}),
    ("verbose", "_handle_verbose_command", {}),
    ("footer", "_handle_footer_command", {}),
    ("yolo", "_handle_yolo_command", {}),
    ("model", "_handle_model_command", {}),
    ("codex-runtime", "_handle_codex_runtime_command", {}),
    ("personality", "_handle_personality_command", {}),
    ("kanban", "_handle_kanban_command", {}),
    ("retry", "_handle_retry_command", {}),
    ("undo", "_handle_undo_command", {"needs_confirm": True}),
    ("sethome", "_handle_set_home_command", {}),
    ("oauth", "_handle_oauth_command", {}),
    ("bind", "_handle_bind_command", {}),
]

# Multi-user commands removed in v0.5.0 — route to deprecation notice
DEPRECATED_COMMANDS: frozenset = frozenset({
    "login", "logout",
    "team", "teams", "join", "join-project", "join_project",
    "project", "projects",
})
