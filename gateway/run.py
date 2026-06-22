"""

Gateway runner - entry point for messaging platform integrations.

This module provides:

- start_gateway(): Start all configured platform adapters

- GatewayRunner: Main class managing the gateway lifecycle

Usage:

    # Start the gateway

    python -m gateway.run

    # Or from CLI

    python cli.py --gateway

"""

# IMPORTANT: intellect_bootstrap must be the very first import — UTF-8 stdio

# on Windows.  No-op on POSIX.  See intellect_bootstrap.py for full rationale.

try:

    import intellect_bootstrap  # noqa: F401

except ModuleNotFoundError:

    # Graceful fallback when intellect_bootstrap isn't registered in the venv

    # yet — happens during partial ``intellect update`` where git-reset landed

    # new code but ``uv pip install -e .`` didn't finish.  Missing bootstrap

    # means UTF-8 stdio setup is skipped on Windows; POSIX is unaffected.

    pass

import asyncio

import dataclasses

import json

import logging

import os

import re


import sys

import signal


import threading

import time


from collections import OrderedDict


from pathlib import Path

from datetime import datetime

from typing import Dict, Optional, Any, List

# account_usage imports the OpenAI SDK chain (~230 ms). Only needed by

# /usage; we still import it at module top in the gateway because test

# patches (tests/gateway/test_usage_command.py) target

# Deferred import: agent.account_usage pulls in the OpenAI SDK (~230ms).

# Imported at the point of use in the /usage handler instead.

from agent.async_utils import safe_schedule_threadsafe

from agent.i18n import t

from intellect_cli.config import cfg_get

# --- Agent cache tuning ---------------------------------------------------

# Bounds the per-session AIAgent cache to prevent unbounded growth in

# long-lived gateways (each AIAgent holds LLM clients, tool schemas,

# memory providers, etc.).  LRU order + idle TTL eviction are enforced

# from _enforce_agent_cache_cap() and _session_expiry_watcher() below.

_AGENT_CACHE_MAX_SIZE = int(

    os.environ.get("INTELLECT_AGENT_CACHE_MAX_SIZE", "64")

)

_AGENT_CACHE_IDLE_TTL_SECS = 3600.0  # evict agents idle for >1h

# Shared thread pool for agent cache eviction — avoids unbounded one-shot

# thread creation under high session churn.

from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

_AGENT_EVICT_EXECUTOR = _ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-evict")

_PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT = 30.0

_ADAPTER_DISCONNECT_TIMEOUT_SECS_DEFAULT = 5.0

# Telegram regex constants — extracted to gateway.message_helpers



_GATEWAY_PROVIDER_ERROR_RE = re.compile(

    r"("  # infrastructure/provider error preambles, not ordinary assistant prose

    r"api\s+(?:call\s+)?failed"

    r"|provider\s+authentication\s+failed"

    r"|non-retryable\s+error"

    r"|rate\s+limited\s+after\s+\d+\s+retries"

    r"|error\s+code\s*:"

    r"|\bhttp\s*\d{3}\b"

    r"|incorrect\s+api\s+key"

    r"|invalid\s+api\s+key"

    r")",

    re.IGNORECASE,

)

# Network/error regex constants — extracted to gateway.helpers






# Network error classification — extracted to gateway.helpers


from gateway.helpers import _gateway_loop_exception_handler as _gateway_loop_exception_handler  # noqa: E402

# Provider error reply sanitization — extracted to gateway.helpers




from gateway.helpers import _sanitize_gateway_final_response as _sanitize_gateway_final_response  # noqa: E402

# Status message / command mention helpers — extracted to gateway.message_helpers




# Only auto-continue interrupted gateway turns while the interruption is fresh.

# Stale tool-tail/resume markers can otherwise revive an unrelated old task

# after a gateway restart when the user's next message starts new work.

#

# The freshness signal is the timestamp of the last transcript row, which

# ``intellect_state.get_messages`` carries on every persisted message.  This

# handles the two auto-continue cases uniformly:

#   * resume_pending (gateway restart/shutdown watchdog marked the session)

#   * tool-tail     (last persisted message is a tool result the agent

#                    never got to reply to)

# In both cases "when did we last do anything on this transcript" is the

# correct freshness question, so one signal replaces two divergent ones.

# Auto-continue freshness — extracted to gateway.helpers



from gateway.helpers import float_env as _float_env  # noqa: E402

# Replay / observed context / transcript helpers — extracted to gateway.message_helpers

from gateway.message_helpers import _ASSISTANT_REPLAY_FIELDS as _ASSISTANT_REPLAY_FIELDS  # noqa: E402
from gateway.helpers import _auto_continue_freshness_window as _auto_continue_freshness_window  # noqa: E402, F401
from gateway.helpers import coerce_gateway_timestamp as _coerce_gateway_timestamp  # noqa: E402, F401
from gateway.helpers import _is_fresh_gateway_interruption as _is_fresh_gateway_interruption  # noqa: E402, F401
from gateway.helpers import _is_transient_network_error as _is_transient_network_error  # noqa: E402, F401
from gateway.message_helpers import _last_transcript_timestamp as _last_transcript_timestamp  # noqa: E402, F401
from gateway.message_helpers import _prepare_gateway_status_message as _prepare_gateway_status_message  # noqa: E402, F401

from gateway.message_helpers import _build_replay_entry as _build_replay_entry  # noqa: E402





from gateway.message_helpers import _build_gateway_agent_history as _build_gateway_agent_history  # noqa: E402

from gateway.message_helpers import _wrap_current_message_with_observed_context as _wrap_current_message_with_observed_context  # noqa: E402


# SSL certificate auto-detection — extracted to gateway.helpers

from gateway.helpers import ensure_ssl_certs as _ensure_ssl_certs  # noqa: E402

# Home-target env helpers — extracted to gateway.config_helpers

from gateway.config_helpers import _home_target_env_var as _home_target_env_var  # noqa: E402

from gateway.config_helpers import _home_thread_env_var as _home_thread_env_var  # noqa: E402

from gateway.config_helpers import _restart_notification_pending as _restart_notification_pending  # noqa: E402

# Mark this process as a gateway so cli.py's module-level load_cli_config()

# knows not to clobber TERMINAL_CWD if lazily imported.

os.environ["_INTELLECT_GATEWAY"] = "1"

_ensure_ssl_certs()

# Add parent directory to path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Resolve Intellect home directory (respects INTELLECT_HOME override)

from intellect_constants import get_intellect_home


_intellect_home = get_intellect_home()

# Load environment variables from ~/.intellect/.env first.

# User-managed env files should override stale shell exports on restart.

from dotenv import load_dotenv  # noqa: F401  # backward-compat for tests that monkeypatch this symbol

from intellect_cli.env_loader import load_intellect_dotenv

_env_path = _intellect_home / '.env'

load_intellect_dotenv(intellect_home=_intellect_home, project_env=Path(__file__).resolve().parents[1] / '.env')

from gateway.config_helpers import _reload_runtime_env_preserving_config_authority as _reload_runtime_env_preserving_config_authority  # noqa: E402

_DOCKER_VOLUME_SPEC_RE = re.compile(r"^(?P<host>.+):(?P<container>/[^:]+?)(?::(?P<options>[^:]+))?$")

_DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS = {"/output", "/outputs"}

# Bridge config.yaml values into the environment so os.getenv() picks them up.

# config.yaml is authoritative for terminal settings — overrides .env.

_config_path = _intellect_home / 'config.yaml'

if _config_path.exists():

    try:

        import yaml as _yaml

        with open(_config_path, encoding="utf-8") as _f:

            _cfg = _yaml.safe_load(_f) or {}

        # Expand ${ENV_VAR} references before bridging to env vars.

        from intellect_cli.config import _expand_env_vars

        _cfg = _expand_env_vars(_cfg)

        # Top-level simple values (fallback only — don't override .env)

        for _key, _val in _cfg.items():

            if isinstance(_val, (str, int, float, bool)) and _key not in os.environ:

                os.environ[_key] = str(_val)

        # Terminal config is nested — bridge to TERMINAL_* env vars.

        # config.yaml overrides .env for these since it's the documented config path.

        _terminal_cfg = _cfg.get("terminal", {})

        if _terminal_cfg and isinstance(_terminal_cfg, dict):

            _terminal_env_map = {

                "backend": "TERMINAL_ENV",

                "cwd": "TERMINAL_CWD",

                "timeout": "TERMINAL_TIMEOUT",

                "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",

                "docker_image": "TERMINAL_DOCKER_IMAGE",

                "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",

                "singularity_image": "TERMINAL_SINGULARITY_IMAGE",

                "modal_image": "TERMINAL_MODAL_IMAGE",

                "daytona_image": "TERMINAL_DAYTONA_IMAGE",

                "ssh_host": "TERMINAL_SSH_HOST",

                "ssh_user": "TERMINAL_SSH_USER",

                "ssh_port": "TERMINAL_SSH_PORT",

                "ssh_key": "TERMINAL_SSH_KEY",

                "container_cpu": "TERMINAL_CONTAINER_CPU",

                "container_memory": "TERMINAL_CONTAINER_MEMORY",

                "container_disk": "TERMINAL_CONTAINER_DISK",

                "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",

                "docker_volumes": "TERMINAL_DOCKER_VOLUMES",

                "docker_env": "TERMINAL_DOCKER_ENV",

                "docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",

                "docker_run_as_host_user": "TERMINAL_DOCKER_RUN_AS_HOST_USER",

                "docker_persist_across_processes": "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES",

                "docker_orphan_reaper": "TERMINAL_DOCKER_ORPHAN_REAPER",

                "sandbox_dir": "TERMINAL_SANDBOX_DIR",

                "persistent_shell": "TERMINAL_PERSISTENT_SHELL",

            }

            for _cfg_key, _env_var in _terminal_env_map.items():

                if _cfg_key in _terminal_cfg:

                    _val = _terminal_cfg[_cfg_key]

                    # Skip cwd placeholder values (".", "auto", "cwd") — the

                    # gateway resolves these to Path.home() later (line ~255).

                    # Writing the raw placeholder here would just be noise.

                    # Only bridge explicit absolute paths from config.yaml.

                    if _cfg_key == "cwd" and str(_val) in {".", "auto", "cwd"}:

                        continue

                    # Expand shell tilde in cwd so subprocess.Popen never

                    # receives a literal "~/" which the kernel rejects.

                    if _cfg_key == "cwd" and isinstance(_val, str):

                        _val = os.path.expanduser(_val)

                    if isinstance(_val, (list, dict)):

                        os.environ[_env_var] = json.dumps(_val)

                    else:

                        os.environ[_env_var] = str(_val)

        # Compression config is read directly from config.yaml by run_agent.py

        # and auxiliary_client.py — no env var bridging needed.

        # Auxiliary model/direct-endpoint overrides (vision, web_extract,

        # approval, plus any plugin-registered auxiliary tasks).

        # Each task has provider/model/base_url/api_key; bridge non-default

        # values to env vars named AUXILIARY_<KEY_UPPER>_*. The legacy

        # hard-coded list (vision/web_extract/approval) is replaced by a

        # dynamic loop so plugin-registered tasks benefit from the same

        # config→env bridging without core knowing about each one.

        _auxiliary_cfg = _cfg.get("auxiliary", {})

        if _auxiliary_cfg and isinstance(_auxiliary_cfg, dict):

            # Built-in tasks that previously had explicit env-var bridging.

            # Kept here as the canonical bridged set; plugin tasks are added

            # below via the plugin auxiliary registry.

            _aux_bridged_keys = {"vision", "web_extract", "approval"}

            try:

                from intellect_cli.plugins import get_plugin_auxiliary_tasks

                for _entry in get_plugin_auxiliary_tasks():

                    _aux_bridged_keys.add(_entry["key"])

            except Exception:

                # Plugin discovery failure must not break gateway startup;

                # built-in bridging stays intact.

                pass

            for _task_key in _aux_bridged_keys:

                _task_cfg = _auxiliary_cfg.get(_task_key, {})

                if not isinstance(_task_cfg, dict):

                    continue

                _prov = str(_task_cfg.get("provider", "")).strip()

                _model = str(_task_cfg.get("model", "")).strip()

                _base_url = str(_task_cfg.get("base_url", "")).strip()

                _api_key = str(_task_cfg.get("api_key", "")).strip()

                _upper = _task_key.upper()

                if _prov and _prov != "auto":

                    os.environ[f"AUXILIARY_{_upper}_PROVIDER"] = _prov

                if _model:

                    os.environ[f"AUXILIARY_{_upper}_MODEL"] = _model

                if _base_url:

                    os.environ[f"AUXILIARY_{_upper}_BASE_URL"] = _base_url

                if _api_key:

                    os.environ[f"AUXILIARY_{_upper}_API_KEY"] = _api_key

        # config.yaml is the documented, authoritative source for these

        # settings — it unconditionally wins over .env values. Previously

        # the guards below read `if X not in os.environ` and let stale

        # .env entries (e.g. INTELLECT_MAX_ITERATIONS=60 written by an old

        # `intellect setup` run) silently shadow the user's current config.

        # See PR #18413 / the 60-vs-500 max_turns incident.

        _agent_cfg = _cfg.get("agent", {})

        if _agent_cfg and isinstance(_agent_cfg, dict):

            if "max_turns" in _agent_cfg:

                os.environ["INTELLECT_MAX_ITERATIONS"] = str(_agent_cfg["max_turns"])

            if "gateway_timeout" in _agent_cfg:

                os.environ["intellect_AGENT_TIMEOUT"] = str(_agent_cfg["gateway_timeout"])

            if "gateway_timeout_warning" in _agent_cfg:

                os.environ["intellect_AGENT_TIMEOUT_WARNING"] = str(_agent_cfg["gateway_timeout_warning"])

            if "gateway_notify_interval" in _agent_cfg:

                os.environ["intellect_AGENT_NOTIFY_INTERVAL"] = str(_agent_cfg["gateway_notify_interval"])

            if "restart_drain_timeout" in _agent_cfg:

                os.environ["intellect_RESTART_DRAIN_TIMEOUT"] = str(_agent_cfg["restart_drain_timeout"])

            if "gateway_auto_continue_freshness" in _agent_cfg:

                os.environ["intellect_AUTO_CONTINUE_FRESHNESS"] = str(

                    _agent_cfg["gateway_auto_continue_freshness"]

                )

        _display_cfg = _cfg.get("display", {})

        if _display_cfg and isinstance(_display_cfg, dict):

            if "busy_input_mode" in _display_cfg:

                os.environ["intellect_GATEWAY_BUSY_INPUT_MODE"] = str(_display_cfg["busy_input_mode"])

            if "busy_text_mode" in _display_cfg:

                os.environ["intellect_GATEWAY_BUSY_TEXT_MODE"] = str(_display_cfg["busy_text_mode"])

            if "busy_ack_enabled" in _display_cfg:

                os.environ["intellect_GATEWAY_BUSY_ACK_ENABLED"] = str(_display_cfg["busy_ack_enabled"])

        # Timezone: bridge config.yaml → INTELLECT_TIMEZONE env var.

        _tz_cfg = _cfg.get("timezone", "")

        if _tz_cfg and isinstance(_tz_cfg, str):

            os.environ["INTELLECT_TIMEZONE"] = _tz_cfg.strip()

        # Security settings

        _security_cfg = _cfg.get("security", {})

        if isinstance(_security_cfg, dict):

            _redact = _security_cfg.get("redact_secrets")

            if _redact is not None:

                os.environ["INTELLECT_REDACT_SECRETS"] = str(_redact).lower()

        # Gateway settings (media delivery allowlist + recency trust + strict mode)

        _gateway_cfg = _cfg.get("gateway", {})

        if isinstance(_gateway_cfg, dict):

            _strict = _gateway_cfg.get("strict")

            if _strict is not None:

                os.environ["intellect_MEDIA_DELIVERY_STRICT"] = (

                    "1" if _strict else "0"

                )

            _allow_dirs = _gateway_cfg.get("media_delivery_allow_dirs")

            if _allow_dirs:

                if isinstance(_allow_dirs, str):

                    _allow_dirs_str = _allow_dirs

                elif isinstance(_allow_dirs, (list, tuple)):

                    _allow_dirs_str = os.pathsep.join(str(p) for p in _allow_dirs if p)

                else:

                    _allow_dirs_str = ""

                if _allow_dirs_str:

                    os.environ["intellect_MEDIA_ALLOW_DIRS"] = _allow_dirs_str

            _trust_recent = _gateway_cfg.get("trust_recent_files")

            if _trust_recent is not None:

                os.environ["intellect_MEDIA_TRUST_RECENT_FILES"] = (

                    "1" if _trust_recent else "0"

                )

            _trust_recent_seconds = _gateway_cfg.get("trust_recent_files_seconds")

            if _trust_recent_seconds is not None:

                os.environ["intellect_MEDIA_TRUST_RECENT_SECONDS"] = str(_trust_recent_seconds)

    except Exception as _bridge_err:

        # Previously this was silent (`except Exception: pass`), which

        # hid partial bridge failures and let .env defaults shadow

        # config.yaml values — users observed max_turns=500 in config

        # but a 60-iteration cap in practice. Surface the failure to

        # stderr so operators see it even though `logger` is not yet

        # initialized at module-import time (logger is defined further

        # down this module).

        print(

            f"  Warning: config.yaml → env bridge failed: "

            f"{type(_bridge_err).__name__}: {_bridge_err}",

            file=sys.stderr,

        )

        print(

            "  Gateway will fall back to .env values, which may not match "

            "your current config.yaml. Run `intellect doctor` to investigate.",

            file=sys.stderr,

        )

# Apply IPv4 preference if configured (before any HTTP clients are created).

try:

    from intellect_constants import apply_ipv4_preference

    _network_cfg = (_cfg if '_cfg' in dir() else {}).get("network", {})

    if isinstance(_network_cfg, dict) and _network_cfg.get("force_ipv4"):

        apply_ipv4_preference(force=True)

except Exception as _bootstrap_exc:

    print(f"  Warning: IPv4 preference application failed: {_bootstrap_exc}", file=sys.stderr)

# Validate config structure early — log warnings so gateway operators see problems

try:

    from intellect_cli.config import print_config_warnings

    print_config_warnings()

except Exception as _bootstrap_exc:

    print(f"  Warning: config validation failed: {_bootstrap_exc}", file=sys.stderr)

# Warn if user has deprecated MESSAGING_CWD / TERMINAL_CWD in .env

try:

    from intellect_cli.config import warn_deprecated_cwd_env_vars

    warn_deprecated_cwd_env_vars()

except Exception as _bootstrap_exc:

    print(f"  Warning: deprecation check failed: {_bootstrap_exc}", file=sys.stderr)

# Gateway runs in quiet mode - suppress debug output and use cwd directly (no temp dirs)

os.environ["INTELLECT_QUIET"] = "1"

# Enable interactive exec approval for dangerous commands on messaging platforms

os.environ["intellect_EXEC_ASK"] = "1"

# Set terminal working directory for messaging platforms.

# config.yaml terminal.cwd is the canonical source (bridged to TERMINAL_CWD

# by the config bridge above).  When it's unset or a placeholder, default

# to home directory.  MESSAGING_CWD is accepted as a backward-compat

# fallback (deprecated — the warning above tells users to migrate).

_configured_cwd = os.environ.get("TERMINAL_CWD", "")

if not _configured_cwd or _configured_cwd in {".", "auto", "cwd"}:

    _fallback = os.getenv("MESSAGING_CWD") or str(Path.home())

    os.environ["TERMINAL_CWD"] = _fallback

from gateway.config import (

    Platform,

    GatewayConfig,

    load_gateway_config,

)

from gateway.session import (

    SessionStore,

    SessionSource,

    build_session_context,

    build_session_context_prompt,

    is_shared_multi_user_session,

)

from gateway.delivery import DeliveryRouter

from gateway.platforms.base import (

    BasePlatformAdapter,

    EphemeralReply,

    MessageEvent,

    MessageType,

    merge_pending_message_event,

)

from gateway.restart import (

    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,

    GATEWAY_SERVICE_RESTART_EXIT_CODE,

)

from gateway.whatsapp_identity import (

    canonical_whatsapp_identifier as _canonical_whatsapp_identifier,  # noqa: F401

    )

logger = logging.getLogger(__name__)

# Non-critical error logging — extracted to gateway.helpers

from gateway.helpers import _log_non_critical as _log_non_critical  # noqa: E402

# Sentinel placed into _running_agents immediately when a session starts

# processing, *before* any await.  Prevents a second message for the same

# session from bypassing the "already running" guard during the async gap

# between the guard check and actual agent creation.

_AGENT_PENDING_SENTINEL = object()

from gateway.config_helpers import _resolve_runtime_agent_kwargs as _resolve_runtime_agent_kwargs  # noqa: E402

from gateway.config_helpers import _try_resolve_fallback_provider as _try_resolve_fallback_provider  # noqa: E402

from gateway.config_helpers import _try_resolve_fallback_provider_inner as _try_resolve_fallback_provider_inner  # noqa: E402

# Media placeholder — extracted to gateway.message_helpers


# Audio/time/misc — extracted to gateway.helpers


from gateway.helpers import _probe_audio_duration as _probe_audio_duration  # noqa: E402

# Pending event dequeuing — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _dequeue_pending_event as _dequeue_pending_event  # noqa: E402

# Interrupt reason constants — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _INTERRUPT_REASON_STOP as _INTERRUPT_REASON_STOP  # noqa: E402

from gateway.skill_session_helpers import _INTERRUPT_REASON_RESET as _INTERRUPT_REASON_RESET  # noqa: E402



from gateway.skill_session_helpers import _INTERRUPT_REASON_GATEWAY_SHUTDOWN as _INTERRUPT_REASON_GATEWAY_SHUTDOWN  # noqa: E402

from gateway.skill_session_helpers import _INTERRUPT_REASON_GATEWAY_RESTART as _INTERRUPT_REASON_GATEWAY_RESTART  # noqa: E402


from gateway.skill_session_helpers import _is_control_interrupt_message as _is_control_interrupt_message  # noqa: E402

# Skill slug/availability helpers — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _skill_slug_from_frontmatter as _skill_slug_from_frontmatter  # noqa: E402

from gateway.skill_session_helpers import _check_unavailable_skill as _check_unavailable_skill  # noqa: E402

# _check_unavailable_skill — extracted to gateway.skill_session_helpers (re-exported above with _skill_slug_from_frontmatter)

# Platform config key — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _platform_config_key as _platform_config_key  # noqa: E402

from gateway.config_helpers import _teams_pipeline_plugin_enabled as _teams_pipeline_plugin_enabled  # noqa: E402

from gateway.config_helpers import _load_gateway_config as _load_gateway_config  # noqa: E402

# Runtime config helpers — extracted to gateway.config_helpers
from gateway.config_helpers import _load_gateway_runtime_config as _load_gateway_runtime_config  # noqa: E402

from gateway.config_helpers import _resolve_gateway_model as _resolve_gateway_model  # noqa: E402

# Intellect binary resolution — extracted to gateway.config_helpers

from gateway.config_helpers import _resolve_intellect_bin as _resolve_intellect_bin  # noqa: E402

# Session key parsing — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _parse_session_key as _parse_session_key  # noqa: E402

def _format_gateway_process_notification(evt: dict) -> "str | None":

    """Format a watch pattern event from completion_queue into a [IMPORTANT:] message."""

    evt_type = evt.get("type", "completion")

    _sid = evt.get("session_id", "unknown")

    _cmd = evt.get("command", "unknown")

    if evt_type == "watch_disabled":

        return f"[IMPORTANT: {evt.get('message', '')}]"

    if evt_type == "watch_match":

        _pat = evt.get("pattern", "?")

        _out = evt.get("output", "")

        _sup = evt.get("suppressed", 0)

        text = (

            f"[IMPORTANT: Background process {_sid} matched "

            f"watch pattern \"{_pat}\".\n"

            f"Command: {_cmd}\n"

            f"Matched output:\n{_out}"

        )

        if _sup:

            text += f"\n({_sup} earlier matches were suppressed by rate limit)"

        text += "]"

        return text

    return None

# Module-level weak reference to the active GatewayRunner instance.

# Used by tools (e.g. send_message) that need to route through a live

# adapter for plugin platforms.  Set in GatewayRunner.__init__().

import weakref as _weakref

_gateway_runner_ref: _weakref.ref = lambda: None

# Agent response helpers — extracted to gateway.skill_session_helpers

from gateway.skill_session_helpers import _normalize_empty_agent_response as _normalize_empty_agent_response  # noqa: E402

from gateway.skill_session_helpers import _should_clear_resume_pending_after_turn as _should_clear_resume_pending_after_turn  # noqa: E402

from gateway.skill_session_helpers import _preserve_queued_followup_history_offset as _preserve_queued_followup_history_offset  # noqa: E402

from gateway.command_handlers import GatewayCommandHandlers  # noqa: E402

from gateway.agent_runner import GatewayAgentRunner  # noqa: E402

from gateway.platform_handlers import GatewayPlatformHandlers  # noqa: E402

from gateway.infrastructure_handlers import GatewayInfrastructureHandlers  # noqa: E402

class GatewayRunner(GatewayCommandHandlers, GatewayAgentRunner, GatewayPlatformHandlers, GatewayInfrastructureHandlers):

    """

    Main gateway controller.

    Manages the lifecycle of all platform adapters and routes

    messages to/from the agent.

    """

    # Class-level defaults so partial construction in tests doesn't

    # blow up on attribute access.

    _running_agents_ts: Dict[str, float] = {}

    _busy_input_mode: str = "interrupt"

    _busy_text_mode: str = "interrupt"

    _restart_drain_timeout: float = DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT

    _exit_code: Optional[int] = None

    _draining: bool = False

    _restart_requested: bool = False

    _restart_task_started: bool = False

    _restart_detached: bool = False

    _restart_via_service: bool = False

    _stop_task: Optional[asyncio.Task] = None

    _session_model_overrides: Dict[str, Dict[str, str]] = {}

    _session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}

    # ── Command dispatch registry ──────────────────────────────────────────

    # Maps canonical command names to handler method names on this instance.

    # Replaces a 45-branch if/elif chain with a single ``getattr`` lookup.

    # Special cases (destructive confirm, deprecated aliases, steer fallthrough)

    # are handled inline in ``_dispatch_command``.

    _COMMAND_DISPATCH: dict[str, str | None] = {

        "topic": "_handle_topic_command",

        "help": "_handle_help_command",

        "start": None,  # silently ignored (Telegram /start ping)

        "commands": "_handle_commands_command",

        "profile": "_handle_profile_command",

        "whoami": "_handle_whoami_command",

        "status": "_handle_status_command",

        "agents": "_handle_agents_command",

        "platform": "_handle_platform_command",

        "restart": "_handle_restart_command",

        "stop": "_handle_stop_command",

        "reasoning": "_handle_reasoning_command",

        "fast": "_handle_fast_command",

        "verbose": "_handle_verbose_command",

        "footer": "_handle_footer_command",

        "yolo": "_handle_yolo_command",

        "model": "_handle_model_command",

        "codex-runtime": "_handle_codex_runtime_command",

        "personality": "_handle_personality_command",

        "kanban": "_handle_kanban_command",

        "retry": "_handle_retry_command",

        "sethome": "_handle_set_home_command",

        "oauth": "_handle_oauth_command",

        "bind": "_handle_bind_command",

        "compress": "_handle_compress_command",

        "usage": "_handle_usage_command",

        "insights": "_handle_insights_command",

        "reload-mcp": "_handle_reload_mcp_command",

        "reload-skills": "_handle_reload_skills_command",

        "bundles": "_handle_bundles_command",

        "approve": "_handle_approve_command",

        "deny": "_handle_deny_command",

        "update": "_handle_update_command",

        "debug": "_handle_debug_command",

        "title": "_handle_title_command",

        "resume": "_handle_resume_command",

        "branch": "_handle_branch_command",

        "rollback": "_handle_rollback_command",

        "background": "_handle_background_command",

        "goal": "_handle_goal_command",

        "subgoal": "_handle_subgoal_command",

        "voice": "_handle_voice_command",

    }

    # Commands that require destructive-confirmation gating

    # Sentinel to distinguish "no handler found" (fall through) from

    # "handler returned None" (suppress the response).

    _DISPATCH_NOT_FOUND = object()

    async def _dispatch_command(self, canonical: str, event, source) -> "str | None | object":

        """Dispatch a recognized slash command via the registry.

        Returns the handler result for recognized commands.  Returns

        ``_DISPATCH_NOT_FOUND`` when the command is not recognized (caller

        should fall through to plugin / skill / agent processing).

        """

        # ── Destructive-confirm commands ──────────────────────────────────

        if canonical == "new":

            if self._is_telegram_topic_root_lobby(source):

                return self._telegram_topic_root_new_message()

            async def _do_reset():

                return await self._handle_reset_command(event)

            return await self._maybe_confirm_destructive_slash(

                event=event, command="new", title="/new",

                detail="This starts a fresh session and discards the current conversation history.",

                execute=_do_reset,

            )

        if canonical == "undo":

            async def _do_undo():

                return await self._handle_undo_command(event)

            return await self._maybe_confirm_destructive_slash(

                event=event, command="undo", title="/undo",

                detail="This removes the last user/assistant exchange from history.",

                execute=_do_undo,

            )

        # ── Silently-ignored pings ────────────────────────────────────────

        if canonical == "start":
            logger.info("Ignoring /start platform ping (source=%s)", source)
            return ""

        # ── Deprecated multi-user commands ─────────────────────────────────

        if canonical in ("login", "logout"):

            return await self._reply(

                event,

                "Multi-user features (login/logout) were removed in v0.5.0. "

                "Intellect now operates in single-user mode.",

            )

        if canonical in ("team", "teams", "join", "join-project", "join_project",

                         "project", "projects"):

            return await self._reply(

                event,

                "Multi-user features (teams/projects) were removed in v0.5.0. "

                "Intellect now operates in single-user mode.",

            )

        # ── Steer fallthrough: strip prefix, signal fallthrough ────────────

        if canonical == "steer":

            steer_payload = event.get_command_args().strip()

            if not steer_payload:

                return "Usage: /steer <prompt>  (no agent is running; sending as a normal message)"

            try:

                event.text = steer_payload

            except Exception:

                _log_non_critical()

            # Do NOT return — fall through to agent processing

            return self._DISPATCH_NOT_FOUND

        # ── Registry lookup ───────────────────────────────────────────────

        handler_name = self._COMMAND_DISPATCH.get(canonical)

        if handler_name is None:

            return self._DISPATCH_NOT_FOUND

        handler = getattr(self, handler_name)

        return await handler(event)

    def __init__(self, config: Optional[GatewayConfig] = None):

        global _gateway_runner_ref

        self.config = config or load_gateway_config()

        self.adapters: Dict[Platform, BasePlatformAdapter] = {}

        self._warn_if_docker_media_delivery_is_risky()

        _gateway_runner_ref = _weakref.ref(self)

        # Load ephemeral config from config.yaml / env vars.

        # Both are injected at API-call time only and never persisted.

        self._prefill_messages = self._load_prefill_messages()

        self._ephemeral_system_prompt = self._load_ephemeral_system_prompt()

        self._reasoning_config = self._load_reasoning_config()

        self._service_tier = self._load_service_tier()

        self._show_reasoning = self._load_show_reasoning()

        self._busy_input_mode = self._load_busy_input_mode()

        self._busy_text_mode = self._load_busy_text_mode()

        self._restart_drain_timeout = self._load_restart_drain_timeout()

        self._provider_routing = self._load_provider_routing()

        self._fallback_model = self._load_fallback_model()

        # Wire process registry into session store for reset protection

        from tools.process_registry import process_registry

        self.session_store = SessionStore(

            self.config.sessions_dir, self.config,

            has_active_processes_fn=lambda key: process_registry.has_active_for_session(key),

        )

        self.delivery_router = DeliveryRouter(self.config)

        self._running = False

        self._gateway_loop: Optional[asyncio.AbstractEventLoop] = None

        self._shutdown_event = asyncio.Event()

        self._exit_cleanly = False

        self._exit_with_failure = False

        self._exit_reason: Optional[str] = None

        self._exit_code: Optional[int] = None

        self._draining = False

        self._restart_requested = False

        self._restart_task_started = False

        self._restart_detached = False

        self._restart_via_service = False

        self._stop_task: Optional[asyncio.Task] = None

        # Track running agents per session for interrupt support

        # Key: session_key, Value: AIAgent instance

        self._running_agents: Dict[str, Any] = {}

        self._running_agents_ts: Dict[str, float] = {}  # start timestamp per session

        self._pending_messages: Dict[str, str] = {}  # Queued messages during interrupt

        # Last successfully-resolved (non-empty) model, keyed by session. Used

        # as a fallback when a fresh config read transiently returns an empty

        # model (e.g. an mtime-keyed config-cache miss during a post-interrupt

        # recovery turn). Without this, the agent is built with model="" and

        # every API call fails HTTP 400 "No models provided" — the session goes

        # silent until the user manually re-sends. See #35314. ``"*"`` holds a

        # process-wide last-known-good for sessions seen for the first time.

        self._last_resolved_model: Dict[str, str] = {}

        # Overflow buffer for explicit /queue commands.  The adapter-level

        # _pending_messages dict is a single slot per session (designed for

        # "next-turn" follow-ups where repeated sends collapse into one

        # event).  /queue has different semantics: each invocation must

        # produce its own full agent turn, in FIFO order, with no merging.

        # When the slot is occupied, additional /queue items land here and

        # are promoted one-at-a-time after each run's drain.  Cleared on

        # /new and /reset.  /model and other mid-session operations

        # preserve the queue.

        self._queued_events: Dict[str, List[MessageEvent]] = {}

        self._pending_native_image_paths_by_session: Dict[str, List[str]] = {}

        self._busy_ack_ts: Dict[str, float] = {}  # last busy-ack timestamp per session (debounce)

        self._session_run_generation: Dict[str, int] = {}

        # LRU cache of live SessionSources keyed by session_key. Used by

        # fallback routing paths (shutdown notifications, synthetic

        # background-process events) when the persisted origin is missing

        # and _parse_session_key can't recover thread_id. Capped so it

        # cannot grow unbounded over a long-running gateway lifetime.

        self._session_sources: "OrderedDict[str, SessionSource]" = OrderedDict()

        self._session_sources_max = 512

        # Cache AIAgent instances per session to preserve prompt caching.

        # Without this, a new AIAgent is created per message, rebuilding the

        # system prompt (including memory) every turn — breaking prefix cache

        # and costing ~10x more on providers with prompt caching (Anthropic).

        # Key: session_key, Value: (AIAgent, config_signature_str)

        #

        # OrderedDict so _enforce_agent_cache_cap() can pop the least-recently-

        # used entry (move_to_end() on cache hits, popitem(last=False) for

        # eviction).  Hard cap via _AGENT_CACHE_MAX_SIZE, idle TTL enforced

        # from _session_expiry_watcher().

        import threading as _threading

        self._agent_cache: "OrderedDict[str, tuple]" = OrderedDict()

        self._agent_cache_lock = _threading.Lock()

        # Per-session model overrides from /model command.

        # Key: session_key, Value: dict with model/provider/api_key/base_url/api_mode

        self._session_model_overrides: Dict[str, Dict[str, str]] = {}

        # Per-session reasoning effort overrides from /reasoning.

        # Key: session_key, Value: parsed reasoning config dict.

        self._session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}

        self._kanban_notifier_profile = self._active_profile_name()

        # Teams meeting pipeline runtime (bound later when msgraph_webhook adapter exists).

        self._teams_pipeline_runtime = None

        self._teams_pipeline_runtime_error: Optional[str] = None

        # Track pending exec approvals per session

        # Key: session_key, Value: {"command": str, "pattern_key": str, ...}

        self._pending_approvals: Dict[str, Dict[str, Any]] = {}

        # Track platforms that failed to connect for background reconnection.

        # Key: Platform enum, Value: {"config": platform_config, "attempts": int, "next_retry": float}

        self._failed_platforms: Dict[Platform, Dict[str, Any]] = {}

        # Track pending /update prompt responses per session.

        # Key: session_key, Value: True when a prompt is waiting for user input.

        self._update_prompt_pending: Dict[str, bool] = {}

        # Slash-confirm state lives in tools.slash_confirm (module-level),

        # so platform adapters can resolve callbacks without a backref to

        # this runner.  Keep a local counter for confirm_id generation so

        # IDs stay compact (button callback_data has a 64-byte cap on

        # some platforms).

        import itertools as _itertools

        self._slash_confirm_counter = _itertools.count(1)

        # Persistent Honcho managers keyed by gateway session key.

        # This preserves write_frequency="session" semantics across short-lived

        # per-message AIAgent instances.

        # Ensure tirith security scanner is available (downloads if needed)

        try:

            from tools.tirith_security import ensure_installed

            ensure_installed(log_failures=False)

        except Exception:

            # Non-fatal — fail-open at scan time if unavailable

            logger.debug('non-critical operation failed', exc_info=True)

        # Startup heads-up (#30882): a gateway in manual approval mode with no

        # automated risk assessor (tirith disabled AND no auxiliary.approval

        # model) can only gate dangerous commands / execute_code scripts via

        # live in-chat approval. With approval routing fixed, those actions now

        # fail closed (block) rather than silently auto-running — surface that

        # so operators knowingly enable tirith or configure auxiliary.approval

        # for unattended gateways.

        try:

            from intellect_cli.config import load_config as _load_full_config

            _appr_cfg = _load_full_config()

            _appr_mode = str(

                cfg_get(_appr_cfg, "approvals", "mode", default="manual") or "manual"

            ).strip().lower()

            _tirith_on = bool(cfg_get(_appr_cfg, "security", "tirith_enabled", default=True))

            _aux_approval = cfg_get(_appr_cfg, "auxiliary", "approval", default=None)

            if _appr_mode == "manual" and not _tirith_on and not _aux_approval:

                logger.warning(

                    "Gateway approvals.mode=manual with no automated risk "

                    "assessor (security.tirith_enabled is false and "

                    "auxiliary.approval is unset): dangerous commands and "

                    "execute_code scripts will BLOCK until a human approves "

                    "them in chat. Enable security.tirith_enabled or configure "

                    "auxiliary.approval for unattended operation."

                )

        except Exception:

            logger.debug("approvals.mode startup check skipped", exc_info=True)

        # Initialize session database for session_search tool support

        self._session_db = None

        try:

            from intellect_state import SessionDB

            self._session_db = SessionDB()

        except Exception as e:

            # WARNING (not DEBUG) so the failure appears in errors.log — matches

            # cli.py's handling of the same init path.  Users hitting NFS-mounted

            # INTELLECT_HOME silently lost /resume, /title, /history, /branch, and

            # session search without this.  The underlying cause (usually

            # "locking protocol" from NFS) is now also captured by

            # intellect_state.get_last_init_error() for slash-command error strings.

            logger.warning("SQLite session store not available: %s", e)

        # Opportunistic state.db maintenance: prune ended sessions older

        # than sessions.retention_days + optional VACUUM. Tracks last-run

        # in state_meta so it only actually executes once per

        # sessions.min_interval_hours.  Gateway is long-lived so blocking

        # a few seconds once per day is acceptable; failures are logged

        # but never raised.

        if self._session_db is not None:

            try:

                from intellect_cli.config import load_config as _load_full_config

                _sess_cfg = (_load_full_config().get("sessions") or {})

                if _sess_cfg.get("auto_prune", False):

                    self._session_db.maybe_auto_prune_and_vacuum(

                        retention_days=int(_sess_cfg.get("retention_days", 90)),

                        min_interval_hours=int(_sess_cfg.get("min_interval_hours", 24)),

                        vacuum=bool(_sess_cfg.get("vacuum_after_prune", True)),

                        sessions_dir=self.config.sessions_dir,

                    )

            except Exception as exc:

                logger.debug("state.db auto-maintenance skipped: %s", exc)

        # Opportunistic shadow-repo cleanup — deletes orphan/stale

        # checkpoint repos under ~/.intellect/checkpoints/.  Opt-in via

        # checkpoints.auto_prune, idempotent via .last_prune marker.

        try:

            from intellect_cli.config import load_config as _load_full_config

            _ckpt_cfg = (_load_full_config().get("checkpoints") or {})

            if _ckpt_cfg.get("auto_prune", False):

                from tools.checkpoint_manager import maybe_auto_prune_checkpoints

                maybe_auto_prune_checkpoints(

                    retention_days=int(_ckpt_cfg.get("retention_days", 7)),

                    min_interval_hours=int(_ckpt_cfg.get("min_interval_hours", 24)),

                    delete_orphans=bool(_ckpt_cfg.get("delete_orphans", True)),

                    max_total_size_mb=int(_ckpt_cfg.get("max_total_size_mb", 500)),

                )

        except Exception as exc:

            logger.debug("checkpoint auto-maintenance skipped: %s", exc)

        # DM pairing store for code-based user authorization

        from gateway.pairing import PairingStore

        self.pairing_store = PairingStore()

        # Event hook system

        from gateway.hooks import HookRegistry

        self.hooks = HookRegistry()

        # Per-chat voice reply mode: "off" | "voice_only" | "all"

        self._voice_mode: Dict[str, str] = self._load_voice_modes()

        # Recent voice transcripts per (guild,user) for duplicate suppression.

        # Protects against the same utterance being emitted twice by the voice

        # capture / STT pipeline, which otherwise produces a second delayed reply.

        self._recent_voice_transcripts: Dict[tuple[int, int], List[tuple[float, str]]] = {}

        # Track background tasks to prevent garbage collection mid-execution

        self._background_tasks: set = set()

    def _warn_if_docker_media_delivery_is_risky(self) -> None:

        """Warn when Docker-backed gateways lack an explicit export mount.

        MEDIA delivery happens in the gateway process, so paths emitted by the model

        must be readable from the host. A plain container-local path like

        `/workspace/report.txt` or `/output/report.txt` often exists only inside

        Docker, so users commonly need a dedicated export mount such as

        `host-dir:/output`.

        """

        if os.getenv("TERMINAL_ENV", "").strip().lower() != "docker":

            return

        connected = self.config.get_connected_platforms()

        messaging_platforms = [p for p in connected if p not in {Platform.LOCAL, Platform.API_SERVER, Platform.WEBHOOK}]

        if not messaging_platforms:

            return

        raw_volumes = os.getenv("TERMINAL_DOCKER_VOLUMES", "").strip()

        volumes: List[str] = []

        if raw_volumes:

            try:

                parsed = json.loads(raw_volumes)

                if isinstance(parsed, list):

                    volumes = [str(v) for v in parsed if isinstance(v, str)]

            except Exception:

                logger.debug("Could not parse TERMINAL_DOCKER_VOLUMES for gateway media warning", exc_info=True)

        has_explicit_output_mount = False

        for spec in volumes:

            match = _DOCKER_VOLUME_SPEC_RE.match(spec)

            if not match:

                continue

            container_path = match.group("container")

            if container_path in _DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS:

                has_explicit_output_mount = True

                break

        if has_explicit_output_mount:

            return

        logger.warning(

            "Docker backend is enabled for the messaging gateway but no explicit host-visible "

            "output mount (for example '/home/user/.intellect/cache/documents:/output') is configured. "

            "This is fine if the model already emits host-visible paths, but MEDIA file delivery can fail "

            "for container-local paths like '/workspace/...' or '/output/...'."

        )

    # -- Setup skill availability ----------------------------------------

    # -- Voice mode persistence ------------------------------------------

    _VOICE_MODE_PATH = _intellect_home / "gateway_voice_mode.json"

    @property

    def should_exit_cleanly(self) -> bool:

        return self._exit_cleanly

    @property

    def should_exit_with_failure(self) -> bool:

        return self._exit_with_failure

    @property

    def exit_reason(self) -> Optional[str]:

        return self._exit_reason

    @property

    def exit_code(self) -> Optional[int]:

        return self._exit_code

    # Telegram's General (pinned top) topic in forum-enabled private chats.

    # Bot API behavior varies: some clients omit message_thread_id for

    # General, others send "1". Treat both as "root" for lobby/lane purposes.

    _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})

    _TELEGRAM_LOBBY_REMINDER_COOLDOWN_S = 30.0

    # -------- /queue FIFO helpers --------------------------------------

    # /queue must produce one full agent turn per invocation, in FIFO

    # order, with no merging.  The adapter's _pending_messages dict is a

    # single "next-up" slot (shared with photo-burst follow-ups), so we

    # use it for the head of the queue and an overflow list for the

    # tail.  Enqueue puts new items in the slot when free, otherwise in

    # the overflow.  Promotion (called after each run's drain) moves the

    # next overflow item into the slot so the following recursion picks

    # it up.  Clearing happens on /new and /reset via

    # _handle_reset_command.

    # ------------------------------------------------------------------

    # Per-platform circuit breaker (pause/resume) — used by the reconnect

    # watcher when a retryable failure recurs past a threshold, and by the

    # /platform pause|resume slash command for manual control.

    # ------------------------------------------------------------------

    _STUCK_LOOP_THRESHOLD = 3  # restarts while active before auto-suspend

    _STUCK_LOOP_FILE = ".restart_failure_counts"

    def request_restart(self, *, detached: bool = False, via_service: bool = False) -> bool:

        if self._restart_task_started:

            return False

        self._restart_requested = True

        self._restart_detached = detached

        self._restart_via_service = via_service

        self._restart_task_started = True

        async def _run_restart() -> None:

            await asyncio.sleep(0.05)

            await self.stop(restart=True, detached_restart=detached, service_restart=via_service)

        task = asyncio.create_task(_run_restart())

        self._background_tasks.add(task)

        task.add_done_callback(self._background_tasks.discard)

        return True

    # Drain-timeout reasons set by _stop_impl() when a still-running turn is

    # force-interrupted; "restart_interrupted" is set by

    # SessionStore.suspend_recently_active() on crash recovery (no

    # .clean_shutdown marker).  All three mean "the agent was mid-turn and

    # we killed it" — eligible for startup auto-resume.

    _AUTO_RESUME_REASONS = frozenset(

        {"restart_timeout", "shutdown_timeout", "restart_interrupted"}

    )

    async def start(self) -> bool:

        """

        Start the gateway and all configured platform adapters.

        Returns True if at least one adapter connected successfully.

        """

        logger.info("Starting Intellect Gateway...")

        try:

            self._gateway_loop = asyncio.get_running_loop()

        except RuntimeError:

            self._gateway_loop = None

        logger.info("Session storage: %s", self.config.sessions_dir)

        # Sanity-check that systemd's TimeoutStopSec covers our drain

        # window.  When the user upgraded intellect-agent without re-running

        # ``intellect setup``, their unit file may still encode the old

        # default — in which case SIGKILL hits mid-drain and looks like

        # a phantom kill in the journal.  Best-effort, never raises.

        try:

            from gateway.shutdown_forensics import check_systemd_timing_alignment

            _alignment = check_systemd_timing_alignment(self._restart_drain_timeout)

            if _alignment is not None and _alignment.get("mismatch"):

                logger.warning(

                    "Stale systemd unit detected: %s has TimeoutStopSec=%.0fs but "

                    "drain_timeout=%.0fs (expected >=%.0fs). systemd may SIGKILL the "

                    "gateway mid-drain. Run `intellect gateway service install --replace` "

                    "to regenerate the unit, or shorten agent.restart_drain_timeout.",

                    _alignment.get("unit", "(unknown)"),

                    _alignment["timeout_stop_sec"],

                    _alignment["drain_timeout"],

                    _alignment["expected_min"],

                )

        except Exception as _e:

            logger.debug("check_systemd_timing_alignment failed: %s", _e)

        # Log the resolved max_iterations budget so operators can verify the

        # config.yaml → env bridge did the right thing at a glance (instead

        # of silently running at a stale .env value for weeks).

        try:

            _effective_max_iter = int(os.getenv("INTELLECT_MAX_ITERATIONS", "90"))

            logger.info(

                "Agent budget: max_iterations=%d (agent.max_turns from config.yaml, "

                "or INTELLECT_MAX_ITERATIONS from .env, or default 90)",

                _effective_max_iter,

            )

        except Exception:

            _log_non_critical()

        # Redaction status: ON by default (#17691). Surface a prominent

        # warning if an operator has explicitly opted out so they don't

        # forget the downgrade is active — the redactor snapshots its

        # state at import time, so this log line is the source of truth

        # for this process's lifetime.

        try:

            _redact_raw = os.getenv("INTELLECT_REDACT_SECRETS", "true")

            _redact_on = _redact_raw.lower() in {"1", "true", "yes", "on"}

            if _redact_on:

                logger.info(

                    "Secret redaction: ENABLED (tool output, logs, and chat "

                    "responses are scrubbed before delivery)"

                )

            else:

                logger.warning(

                    "Secret redaction: DISABLED (INTELLECT_REDACT_SECRETS=%s). "

                    "API keys and tokens may appear verbatim in chat output, "

                    "session JSONs, and logs. Set security.redact_secrets: true "

                    "in config.yaml to re-enable.",

                    _redact_raw,

                )

        except Exception:

            _log_non_critical()

        try:

            from intellect_cli.profiles import get_active_profile_name

            _profile = get_active_profile_name()

            if _profile and _profile != "default":

                logger.info("Active profile: %s", _profile)

        except Exception:

            _log_non_critical()

        try:

            from gateway.status import write_runtime_status

            write_runtime_status(gateway_state="starting", exit_reason=None)

        except Exception:

            _log_non_critical()

        # Log any active supply-chain security advisories. Operators see this

        # in gateway.log and `intellect status` surfaces it; we do NOT block

        # startup or surface it inline to user messages, since the gateway

        # operator is the one who can act on it (uninstall the package,

        # rotate credentials).  See intellect_cli/security_advisories.py.

        try:

            from intellect_cli.security_advisories import (

                detect_compromised,

                gateway_log_message,

            )

            _adv_hits = detect_compromised()

            _adv_msg = gateway_log_message(_adv_hits)

            if _adv_msg:

                logger.warning("%s", _adv_msg)

                logger.warning(

                    "Run `intellect doctor` on the gateway host for full "

                    "remediation steps."

                )

        except Exception:

            logger.debug(

                "security advisory check failed at gateway startup",

                exc_info=True,

            )

        # Warn if no user allowlists are configured and open access is not opted in

        _builtin_allowed_vars = (

            "TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS",

            "WHATSAPP_ALLOWED_USERS", "SLACK_ALLOWED_USERS",

            "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",

            "TELEGRAM_GROUP_ALLOWED_USERS",

            "TELEGRAM_GROUP_ALLOWED_CHATS",

            "EMAIL_ALLOWED_USERS",

            "SMS_ALLOWED_USERS", "MATTERMOST_ALLOWED_USERS",

            "MATRIX_ALLOWED_USERS", "DINGTALK_ALLOWED_USERS",

            "FEISHU_ALLOWED_USERS",

            "WECOM_ALLOWED_USERS",

            "WECOM_CALLBACK_ALLOWED_USERS",

            "WEIXIN_ALLOWED_USERS",

            "BLUEBUBBLES_ALLOWED_USERS",

            "QQ_ALLOWED_USERS",

            "YUANBAO_ALLOWED_USERS",

            "GATEWAY_ALLOWED_USERS",

        )

        _builtin_allow_all_vars = (

            "TELEGRAM_ALLOW_ALL_USERS", "DISCORD_ALLOW_ALL_USERS",

            "WHATSAPP_ALLOW_ALL_USERS", "SLACK_ALLOW_ALL_USERS",

            "SIGNAL_ALLOW_ALL_USERS", "EMAIL_ALLOW_ALL_USERS",

            "SMS_ALLOW_ALL_USERS", "MATTERMOST_ALLOW_ALL_USERS",

            "MATRIX_ALLOW_ALL_USERS", "DINGTALK_ALLOW_ALL_USERS",

            "FEISHU_ALLOW_ALL_USERS",

            "WECOM_ALLOW_ALL_USERS",

            "WECOM_CALLBACK_ALLOW_ALL_USERS",

            "WEIXIN_ALLOW_ALL_USERS",

            "BLUEBUBBLES_ALLOW_ALL_USERS",

            "QQ_ALLOW_ALL_USERS",

            "YUANBAO_ALLOW_ALL_USERS",

        )

        # Also pick up plugin-registered platforms — each entry can declare

        # its own allowed_users_env / allow_all_env, so the warning stays

        # accurate as plugins like IRC come online.

        _plugin_allowed_vars: tuple = ()

        _plugin_allow_all_vars: tuple = ()

        try:

            from gateway.platform_registry import platform_registry

            _plugin_allowed_vars = tuple(

                e.allowed_users_env for e in platform_registry.plugin_entries()

                if e.allowed_users_env

            )

            _plugin_allow_all_vars = tuple(

                e.allow_all_env for e in platform_registry.plugin_entries()

                if e.allow_all_env

            )

        except Exception:

            _log_non_critical()

        _any_allowlist = any(

            os.getenv(v) for v in _builtin_allowed_vars + _plugin_allowed_vars

        )

        _allow_all = os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in {"true", "1", "yes"} or any(

            os.getenv(v, "").lower() in {"true", "1", "yes"}

            for v in _builtin_allow_all_vars + _plugin_allow_all_vars

        )

        if not _any_allowlist and not _allow_all:

            logger.warning(

                "No user allowlists configured. All unauthorized users will be denied. "

                "Set GATEWAY_ALLOW_ALL_USERS=true in ~/.intellect/.env to allow open access, "

                "or configure platform allowlists (e.g., TELEGRAM_ALLOWED_USERS=your_id)."

            )

        # Discover Python plugins before shell hooks so plugin block

        # decisions take precedence in tie cases.  The CLI startup path

        # does this via an explicit call in intellect_cli/main.py; the

        # gateway lazily imports run_agent inside per-request handlers,

        # so the discover_plugins() side-effect in model_tools.py is NOT

        # guaranteed to have run by the time we reach this point.

        try:

            from intellect_cli.plugins import discover_plugins

            discover_plugins()

        except Exception:

            logger.warning(

                "plugin discovery failed at gateway startup", exc_info=True,

            )

        # Register declarative shell hooks from cli-config.yaml.  Gateway

        # has no TTY, so consent has to come from one of the three opt-in

        # channels (--accept-hooks on launch, INTELLECT_ACCEPT_HOOKS env var,

        # or hooks_auto_accept: true in config.yaml).  We pass

        # accept_hooks=False here and let register_from_config resolve

        # the effective value from env + config itself — the CLI-side

        # registration already honored --accept-hooks, and re-reading

        # hooks_auto_accept here would just duplicate that lookup.

        # Failures are logged but must never block gateway startup.

        try:

            from intellect_cli.config import load_config

            from agent.shell_hooks import register_from_config

            register_from_config(load_config(), accept_hooks=False)

        except Exception:

            logger.debug(

                "shell-hook registration failed at gateway startup",

                exc_info=True,

            )

        # Discover and load event hooks

        self.hooks.discover_and_load()

        # Recover background processes from checkpoint (crash recovery)

        try:

            from tools.process_registry import process_registry

            recovered = process_registry.recover_from_checkpoint()

            if recovered:

                logger.info("Recovered %s background process(es) from previous run", recovered)

        except Exception as e:

            logger.warning("Process checkpoint recovery: %s", e)

        # Suspend sessions that were active when the gateway last exited.

        # This prevents stuck sessions from being blindly resumed on restart,

        # which can create an unrecoverable loop (#7536).  Suspended sessions

        # auto-reset on the next incoming message, giving the user a clean start.

        #

        # SKIP suspension after a clean (graceful) shutdown — the previous

        # process already drained active agents, so sessions aren't stuck.

        # This prevents unwanted auto-resets after `intellect update`,

        # `intellect gateway restart`, or `/restart`.

        _clean_marker = _intellect_home / ".clean_shutdown"

        if _clean_marker.exists():

            logger.info("Previous gateway exited cleanly — skipping session suspension")

            try:

                _clean_marker.unlink()

            except Exception:

                _log_non_critical()

        else:

            try:

                suspended = self.session_store.suspend_recently_active()

                if suspended:

                    logger.info("Marked %d in-flight session(s) as resumable from previous run", suspended)

            except Exception as e:

                logger.warning("Session suspension on startup failed: %s", e)

        # Stuck-loop detection (#7536): if a session has been active across

        # 3+ consecutive restarts, it's probably stuck in a loop (the same

        # history keeps causing the agent to hang).  Auto-suspend it so the

        # user gets a clean slate on the next message.

        try:

            stuck = self._suspend_stuck_loop_sessions()

            if stuck:

                logger.warning("Auto-suspended %d stuck-loop session(s)", stuck)

        except Exception as e:

            logger.debug("Stuck-loop detection failed: %s", e)

        connected_count = 0

        enabled_platform_count = 0

        startup_nonretryable_errors: list[str] = []

        startup_retryable_errors: list[str] = []

        # Initialize and connect each configured platform

        for platform, platform_config in self.config.platforms.items():

            if not platform_config.enabled:

                continue

            enabled_platform_count += 1

            adapter = self._create_adapter(platform, platform_config)

            if not adapter:

                # Distinguish between missing builtin deps and missing plugin

                _pval = platform.value

                _builtin_names = {m.value for m in Platform.__members__.values()}

                if _pval not in _builtin_names:

                    logger.warning(

                        "No adapter for '%s' — is the plugin installed? "

                        "(platform is enabled in config.yaml but no plugin registered it)",

                        _pval,

                    )

                else:

                    logger.warning("No adapter available for %s", _pval)

                continue

            # Set up message + fatal error handlers

            adapter.set_message_handler(self._handle_message)

            adapter.set_fatal_error_handler(self._handle_adapter_fatal_error)

            adapter.set_session_store(self.session_store)

            adapter.set_busy_session_handler(self._handle_active_session_busy_message)

            adapter.set_topic_recovery_fn(self._recover_telegram_topic_thread_id)

            adapter._busy_text_mode = self._busy_text_mode

            # Try to connect

            logger.info("Connecting to %s...", platform.value)

            self._update_platform_runtime_status(

                platform.value,

                platform_state="connecting",

                error_code=None,

                error_message=None,

            )

            try:

                success = await self._connect_adapter_with_timeout(adapter, platform)

                if success:

                    self.adapters[platform] = adapter

                    self._sync_voice_mode_state_to_adapter(adapter)

                    connected_count += 1

                    self._update_platform_runtime_status(

                        platform.value,

                        platform_state="connected",

                        error_code=None,

                        error_message=None,

                    )

                    logger.info("✓ %s connected", platform.value)

                else:

                    logger.warning("✗ %s failed to connect", platform.value)

                    # Defensive cleanup: a failed connect() may have

                    # allocated resources (aiohttp.ClientSession, poll

                    # tasks, bridge subprocesses) before giving up.

                    # Without this call, those resources are orphaned

                    # and Python logs "Unclosed client session" at

                    # process exit. Adapter disconnect() implementations

                    # are expected to be idempotent and tolerate

                    # partial-init state.

                    await self._safe_adapter_disconnect(adapter, platform)

                    if adapter.has_fatal_error:

                        self._update_platform_runtime_status(

                            platform.value,

                            platform_state="retrying" if adapter.fatal_error_retryable else "fatal",

                            error_code=adapter.fatal_error_code,

                            error_message=adapter.fatal_error_message,

                        )

                        target = (

                            startup_retryable_errors

                            if adapter.fatal_error_retryable

                            else startup_nonretryable_errors

                        )

                        target.append(

                            f"{platform.value}: {adapter.fatal_error_message}"

                        )

                        # Queue for reconnection if the error is retryable

                        if adapter.fatal_error_retryable:

                            self._failed_platforms[platform] = {

                                "config": platform_config,

                                "attempts": 1,

                                "next_retry": time.monotonic() + 30,

                            }

                    else:

                        self._update_platform_runtime_status(

                            platform.value,

                            platform_state="retrying",

                            error_code=None,

                            error_message="failed to connect",

                        )

                        startup_retryable_errors.append(

                            f"{platform.value}: failed to connect"

                        )

                        # No fatal error info means likely a transient issue — queue for retry

                        self._failed_platforms[platform] = {

                            "config": platform_config,

                            "attempts": 1,

                            "next_retry": time.monotonic() + 30,

                        }

            except Exception as e:

                logger.error("✗ %s error: %s", platform.value, e)

                # Same defensive cleanup path for exceptions — an adapter

                # that raised mid-connect may still have a live

                # aiohttp.ClientSession or child subprocess.

                await self._safe_adapter_disconnect(adapter, platform)

                self._update_platform_runtime_status(

                    platform.value,

                    platform_state="retrying",

                    error_code=None,

                    error_message=str(e),

                )

                startup_retryable_errors.append(f"{platform.value}: {e}")

                # Unexpected exceptions are typically transient — queue for retry

                self._failed_platforms[platform] = {

                    "config": platform_config,

                    "attempts": 1,

                    "next_retry": time.monotonic() + 30,

                }

        if connected_count == 0:

            if startup_nonretryable_errors:

                reason = "; ".join(startup_nonretryable_errors)

                logger.error("Gateway hit a non-retryable startup conflict: %s", reason)

                try:

                    from gateway.status import write_runtime_status

                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)

                except Exception:

                    _log_non_critical()

                self._request_clean_exit(reason)

                return True

            if enabled_platform_count > 0:

                if startup_retryable_errors:

                    # All enabled platforms hit retryable failures (network

                    # blip, bridge not paired, npm install timeout, etc.).

                    # Keep the gateway alive so:

                    #   • cron jobs still run

                    #   • the reconnect watcher gets a chance to recover the

                    #     failing platforms once the underlying problem is

                    #     fixed (e.g. user runs `intellect whatsapp`, fixes

                    #     proxy, etc.)

                    # Exiting here used to convert a single misconfigured

                    # platform into an infinite systemd restart loop.

                    reason = "; ".join(startup_retryable_errors)

                    logger.warning(

                        "Gateway started with no connected platforms — "

                        "%d platform(s) queued for retry: %s",

                        len(self._failed_platforms), reason,

                    )

                    try:

                        from gateway.status import write_runtime_status

                        write_runtime_status(

                            gateway_state="degraded",

                            exit_reason=None,

                        )

                    except Exception:

                        _log_non_critical()

                    # Fall through to the normal "running" state — reconnect

                    # watcher takes it from here.

                # All enabled platforms had no adapter (missing library or credentials).

                # In fleet deployments the same config.yaml is shared across nodes that

                # may only have credentials for a subset of platforms.  Rather than

                # failing hard, degrade gracefully and allow cron jobs to run (#5196).

                logger.warning(

                    "No adapter could be created for any of the %d configured platform(s). "

                    "Check that required dependencies are installed and credentials are set. "

                    "Gateway will continue for cron job execution.",

                    enabled_platform_count,

                )

            else:

                logger.warning("No messaging platforms enabled.")

                logger.info("Gateway will continue running for cron job execution.")

        # Update delivery router with adapters

        self.delivery_router.adapters = self.adapters

        self._wire_teams_pipeline_runtime()

        self._running = True

        self._update_runtime_status("running")

        # Emit gateway:startup hook

        hook_count = len(self.hooks.loaded_hooks)

        if hook_count:

            logger.info("%s hook(s) loaded", hook_count)

        await self.hooks.emit("gateway:startup", {

            "platforms": [p.value for p in self.adapters.keys()],

        })

        if connected_count > 0:

            logger.info("Gateway running with %s platform(s)", connected_count)

        # Build initial channel directory for send_message name resolution

        try:

            from gateway.channel_directory import build_channel_directory

            directory = await build_channel_directory(self.adapters)

            ch_count = sum(len(chs) for chs in directory.get("platforms", {}).values())

            logger.info("Channel directory built: %d target(s)", ch_count)

        except Exception as e:

            logger.warning("Channel directory build failed: %s", e)

        # Check if we're restarting after a /update command. If the update is

        # still running, keep watching so we notify once it actually finishes.

        notified = await self._send_update_notification()

        if not notified and any(

            path.exists()

            for path in (

                _intellect_home / ".update_pending.json",

                _intellect_home / ".update_pending.claimed.json",

            )

        ):

            self._schedule_update_notification_watch()

        # Give freshly connected platform adapters a brief moment to settle

        # before sending restart/startup lifecycle messages. In practice this

        # helps Discord thread deliveries right after reconnect.

        if connected_count > 0:

            await asyncio.sleep(1.0)

        # Notify the chat that initiated /restart that the gateway is back.

        restart_notification_pending = _restart_notification_pending()

        delivered_restart_target = await self._send_restart_notification()

        # Broadcast a lightweight "gateway is back" message to configured

        # home channels only when this startup is resuming from /restart. If a

        # /restart requester already received a direct completion notice in the

        # same chat, skip the generic broadcast there to avoid duplicates while

        # still allowing a home-channel fallback when the direct send fails.

        if restart_notification_pending or delivered_restart_target is not None:

            skip_home_targets = (

                {delivered_restart_target} if delivered_restart_target else None

            )

            await self._send_home_channel_startup_notifications(

                skip_targets=skip_home_targets,

            )

        # Automatically continue fresh sessions that were interrupted by the

        # previous gateway restart/shutdown.  The resume_pending flag is cleared

        # by the normal successful-turn path, so a failed auto-resume remains

        # visible for manual recovery on the next user message.

        self._schedule_resume_pending_sessions()

        # Drain any recovered process watchers (from crash recovery checkpoint)

        try:

            from tools.process_registry import process_registry

            # Detach the current batch atomically: reassigning to a fresh list

            # takes ownership of exactly the watchers present now, so any watcher

            # appended concurrently during the yield below isn't silently dropped

            # by a clear() on the shared list.

            watchers = process_registry.pending_watchers

            process_registry.pending_watchers = []

            # Process in batches of 100 with event-loop yield points to avoid

            # O(n^2) event-loop blocking when recovering thousands of watchers.

            for i, watcher in enumerate(watchers):

                asyncio.create_task(self._run_process_watcher(watcher))

                logger.info("Resumed watcher for recovered process %s", watcher.get("session_id"))

                if i % 100 == 99:

                    await asyncio.sleep(0)

        except Exception as e:

            logger.error("Recovered watcher setup error: %s", e)

        # Start background session expiry watcher to finalize expired sessions

        asyncio.create_task(self._session_expiry_watcher())

        # Start background kanban notifier — delivers `completed`, `blocked`,

        # `spawn_auto_blocked`, and `crashed` events to gateway subscribers

        # so human-in-the-loop workflows hear back without polling.

        asyncio.create_task(self._kanban_notifier_watcher())

        # Start background kanban dispatcher — spawns workers for ready

        # tasks. Gated by `kanban.dispatch_in_gateway` (default True).

        # When false, users run `intellect kanban daemon` externally or

        # simply don't use kanban; this loop becomes a no-op.

        asyncio.create_task(self._kanban_dispatcher_watcher())

        # Start background reconnection watcher for platforms that failed at startup

        if self._failed_platforms:

            logger.info(

                "Starting reconnection watcher for %d failed platform(s): %s",

                len(self._failed_platforms),

                ", ".join(p.value for p in self._failed_platforms),

            )

        asyncio.create_task(self._platform_reconnect_watcher())

        # Start background handoff watcher — picks up CLI sessions marked

        # handoff_state='pending' in state.db and re-binds them to the

        # destination platform's home channel, then forges a synthetic user

        # turn so the agent kicks off the new chat.

        asyncio.create_task(self._handoff_watcher())

        logger.info("Press Ctrl+C to stop")

        return True

    async def stop(

        self,

        *,

        restart: bool = False,

        detached_restart: bool = False,

        service_restart: bool = False,

    ) -> None:

        """Stop the gateway and disconnect all adapters."""

        if restart:

            self._restart_requested = True

            self._restart_detached = detached_restart

            self._restart_via_service = service_restart

        if self._stop_task is not None:

            await self._stop_task

            return

        async def _stop_impl() -> None:

            def _kill_tool_subprocesses(phase: str) -> None:

                """Kill tool subprocesses + tear down terminal envs + browsers.

                Called twice in the shutdown path: once eagerly after a

                drain timeout forces agent interrupt (so we reclaim bash/

                sleep children before systemd TimeoutStopSec escalates to

                SIGKILL on the cgroup — #8202), and once as a final

                catch-all at the end of _stop_impl() for the graceful

                path or anything respawned mid-teardown.

                All steps are best-effort; exceptions are swallowed so

                one subsystem's failure doesn't block the rest.

                """

                try:

                    from tools.process_registry import process_registry

                    _killed = process_registry.kill_all()

                    if _killed:

                        logger.info(

                            "Shutdown (%s): killed %d tool subprocess(es)",

                            phase, _killed,

                        )

                except Exception as _e:

                    logger.debug("process_registry.kill_all (%s) error: %s", phase, _e)

                try:

                    from tools.terminal_tool import cleanup_all_environments

                    cleanup_all_environments()

                except Exception as _e:

                    logger.debug("cleanup_all_environments (%s) error: %s", phase, _e)

                try:

                    from tools.browser_tool import cleanup_all_browsers

                    cleanup_all_browsers()

                except Exception as _e:

                    logger.debug("cleanup_all_browsers (%s) error: %s", phase, _e)

            logger.info(

                "Stopping gateway%s...",

                " for restart" if self._restart_requested else "",

            )

            _stop_started_at = time.monotonic()

            def _phase_elapsed() -> float:

                return time.monotonic() - _stop_started_at

            self._running = False

            self._draining = True

            # Notify all chats with active agents BEFORE draining.

            # Adapters are still connected here, so messages can be sent.

            await self._notify_active_sessions_of_shutdown()

            logger.info(

                "Shutdown phase: notify_active_sessions done at +%.2fs",

                _phase_elapsed(),

            )

            timeout = self._restart_drain_timeout

            # Pre-mark sessions as resume_pending BEFORE the drain wait.

            # If the process is killed by the service manager during the

            # drain, the durable marker is already written so the next

            # gateway boot can recover in-flight sessions (#27856).

            _pre_drain_keys: list[str] = []

            for _sk, _agent in list(self._running_agents.items()):

                if _agent is _AGENT_PENDING_SENTINEL:

                    continue

                try:

                    self.session_store.mark_resume_pending(

                        _sk,

                        "restart_timeout" if self._restart_requested else "shutdown_timeout",

                    )

                    _pre_drain_keys.append(_sk)

                except Exception as _e:

                    logger.debug("pre-drain mark_resume_pending failed for %s: %s", _sk, _e)

            _drain_started_at = time.monotonic()

            active_agents, timed_out = await self._drain_active_agents(timeout)

            logger.info(

                "Shutdown phase: drain done at +%.2fs (drain took %.2fs, "

                "timed_out=%s, active_at_start=%d, active_now=%d)",

                _phase_elapsed(),

                time.monotonic() - _drain_started_at,

                timed_out,

                len(active_agents),

                self._running_agent_count(),

            )

            if not timed_out:

                # Drain completed gracefully — all running sessions finished.

                # Clear the pre-drain resume_pending markers so sessions that

                # completed during the drain window don't carry a stale flag.

                for _sk in _pre_drain_keys:

                    if _sk not in self._running_agents:

                        try:

                            self.session_store.clear_resume_pending(_sk)

                        except Exception as _e:

                            logger.debug(

                                "clear_resume_pending after drain failed for %s: %s",

                                _sk, _e,

                            )

            if timed_out:

                logger.warning(

                    "Gateway drain timed out after %.1fs with %d active agent(s); interrupting remaining work.",

                    timeout,

                    self._running_agent_count(),

                )

                # Mark forcibly-interrupted sessions as resume_pending BEFORE

                # interrupting the agents.  This preserves each session's

                # session_id + transcript so the next message on the same

                # session_key auto-resumes from the existing conversation

                # instead of getting routed through suspend_recently_active()

                # and converted into a fresh session.  Terminal escalation

                # for genuinely stuck sessions still flows through the

                # existing ``.restart_failure_counts`` stuck-loop counter

                # (incremented below, threshold 3), which sets

                # ``suspended=True`` and overrides resume_pending.

                #

                # Iterate self._running_agents (current) rather than the

                # drain-start ``active_agents`` snapshot — the snapshot

                # may include sessions that finished gracefully during

                # the drain window, and marking those falsely would give

                # them a stray restart-interruption system note on their

                # next turn even though their previous turn completed

                # cleanly.  Skip pending sentinels for the same reason

                # _interrupt_running_agents() does: their agent hasn't

                # started yet, there's nothing to interrupt, and the

                # session shouldn't carry a misleading resume flag.

                _resume_reason = (

                    "restart_timeout" if self._restart_requested else "shutdown_timeout"

                )

                for _sk, _agent in list(self._running_agents.items()):

                    if _agent is _AGENT_PENDING_SENTINEL:

                        continue

                    try:

                        self.session_store.mark_resume_pending(_sk, _resume_reason)

                    except Exception as _e:

                        logger.debug(

                            "mark_resume_pending failed for %s: %s",

                            _sk, _e,

                        )

                self._interrupt_running_agents(

                    _INTERRUPT_REASON_GATEWAY_RESTART if self._restart_requested else _INTERRUPT_REASON_GATEWAY_SHUTDOWN

                )

                interrupt_deadline = asyncio.get_running_loop().time() + 5.0

                while self._running_agents and asyncio.get_running_loop().time() < interrupt_deadline:

                    self._update_runtime_status("draining")

                    await asyncio.sleep(0.1)

                # Kill lingering tool subprocesses NOW, before we spend more

                # budget on adapter disconnect / session DB close.  Under

                # systemd (TimeoutStopSec bounded by drain_timeout+headroom),

                # deferring this to the end of stop() risks systemd escalating

                # to SIGKILL on the cgroup first — at which point bash/sleep

                # children left behind by an interrupted terminal tool get

                # killed by systemd instead of us (issue #8202).  The final

                # catch-all cleanup below still runs for the graceful path.

                _kill_tool_subprocesses("post-interrupt")

                logger.info(

                    "Shutdown phase: post-interrupt tool kill done at +%.2fs",

                    _phase_elapsed(),

                )

            if self._restart_requested and self._restart_detached:

                try:

                    await self._launch_detached_restart_command()

                except Exception as e:

                    logger.error("Failed to launch detached gateway restart: %s", e)

            self._finalize_shutdown_agents(active_agents)

            # Also shut down memory providers on idle cached agents.

            # _finalize_shutdown_agents only handles agents that were

            # mid-turn at drain time; the _agent_cache may still hold

            # idle agents whose MemoryProviders never received

            # on_session_end().

            _cache_lock = getattr(self, "_agent_cache_lock", None)

            _cache = getattr(self, "_agent_cache", None)

            if _cache_lock is not None and _cache is not None:

                with _cache_lock:

                    _idle_agents = list(_cache.values())

                    _cache.clear()

                for _entry in _idle_agents:

                    _agent = (

                        _entry[0] if isinstance(_entry, tuple) else _entry

                    )

                    self._cleanup_agent_resources(_agent)

            for platform, adapter in list(self.adapters.items()):

                _adapter_started_at = time.monotonic()

                try:

                    await adapter.cancel_background_tasks()

                except Exception as e:

                    logger.debug("✗ %s background-task cancel error: %s", platform.value, e)

                try:

                    await adapter.disconnect()

                    logger.info(

                        "✓ %s disconnected (%.2fs)",

                        platform.value,

                        time.monotonic() - _adapter_started_at,

                    )

                except Exception as e:

                    logger.error(

                        "✗ %s disconnect error after %.2fs: %s",

                        platform.value,

                        time.monotonic() - _adapter_started_at,

                        e,

                    )

            logger.info(

                "Shutdown phase: all adapters disconnected at +%.2fs",

                _phase_elapsed(),

            )

            for _task in list(self._background_tasks):

                if _task is self._stop_task:

                    continue

                _task.cancel()

            self._background_tasks.clear()

            self.adapters.clear()

            self._running_agents.clear()

            self._running_agents_ts.clear()

            self._pending_messages.clear()

            self._pending_approvals.clear()

            if hasattr(self, '_busy_ack_ts'):

                self._busy_ack_ts.clear()

            self._shutdown_event.set()

            # Global cleanup: kill any remaining tool subprocesses not tied

            # to a specific agent (catch-all for zombie prevention). On the

            # drain-timeout path we already did this earlier after agent

            # interrupt — this second call catches (a) the graceful path

            # where drain succeeded without interrupt, and (b) anything

            # that got respawned between the earlier call and adapter

            # disconnect (defense in depth; safe to call repeatedly).

            _kill_tool_subprocesses("final-cleanup")

            logger.info(

                "Shutdown phase: final-cleanup tool kill done at +%.2fs",

                _phase_elapsed(),

            )

            # Reap the process-global auxiliary-client cache once at the very

            # end of teardown.  Per-turn cleanup runs in _cleanup_agent_resources

            # for each active agent, but clients bound to worker-thread loops

            # that died with their ThreadPoolExecutor (notably cron ticks) only

            # get swept here.  Without this, long-running gateways accumulate

            # async httpx transports until they hit EMFILE on macOS's default

            # RLIMIT_NOFILE=256.  See #14210.

            try:

                from agent.auxiliary_client import shutdown_cached_clients

                shutdown_cached_clients()

            except Exception as _e:

                logger.debug("shutdown_cached_clients error: %s", _e)

            # Close SQLite session DBs so the WAL write lock is released.

            # Without this, --replace and similar restart flows leave the

            # old gateway's connection holding the WAL lock until Python

            # actually exits — causing 'database is locked' errors when

            # the new gateway tries to open the same file.

            for _db_holder in (self, getattr(self, "session_store", None)):

                _db = getattr(_db_holder, "_db", None) if _db_holder else None

                if _db is None or not hasattr(_db, "close"):

                    continue

                try:

                    _db.close()

                except Exception as _e:

                    logger.debug("SessionDB close error: %s", _e)

            logger.info(

                "Shutdown phase: SessionDB close done at +%.2fs",

                _phase_elapsed(),

            )

            from gateway.status import remove_pid_file, release_gateway_runtime_lock

            remove_pid_file()

            release_gateway_runtime_lock()

            # Write a clean-shutdown marker so the next startup knows this

            # wasn't a crash.  suspend_recently_active() only needs to run

            # after unexpected exits.  However, if the drain timed out and

            # agents were force-interrupted, their sessions may be in an

            # incomplete state (trailing tool response, no final assistant

            # message).  Skip the marker in that case so the next startup

            # suspends those sessions — giving users a clean slate instead

            # of resuming a half-finished tool loop.

            if not timed_out:

                try:

                    (_intellect_home / ".clean_shutdown").touch()

                except Exception:

                    _log_non_critical()

            else:

                logger.info(

                    "Skipping .clean_shutdown marker — drain timed out with "

                    "interrupted agents; next startup will suspend recently "

                    "active sessions."

                )

            # Track sessions that were active at shutdown for stuck-loop

            # detection (#7536).  On each restart, the counter increments

            # for sessions that were running.  If a session hits the

            # threshold (3 consecutive restarts while active), the next

            # startup auto-suspends it — breaking the loop.

            if active_agents:

                self._increment_restart_failure_counts(set(active_agents.keys()))

            if self._restart_requested and self._restart_via_service:

                self._exit_code = GATEWAY_SERVICE_RESTART_EXIT_CODE

                self._exit_reason = self._exit_reason or "Gateway restart requested"

            self._draining = False

            self._update_runtime_status("stopped", self._exit_reason)

            logger.info("Gateway stopped (total teardown %.2fs)", _phase_elapsed())

        self._stop_task = asyncio.create_task(_stop_impl())

        await self._stop_task

    async def wait_for_shutdown(self) -> None:

        """Wait for shutdown signal."""

        await self._shutdown_event.wait()

    async def _handle_message(self, event: MessageEvent) -> Optional[str]:

        """

        Handle an incoming message from any platform.

        This is the core message processing pipeline:

        1. Check user authorization

        2. Check for commands (/new, /reset, etc.)

        3. Check for running agent and interrupt if needed

        4. Get or create session

        5. Build context for agent

        6. Run agent conversation

        7. Return response

        """

        source = event.source

        # Internal events (e.g. background-process completion notifications)

        # are system-generated and must skip user authorization.

        is_internal = bool(getattr(event, "internal", False))

        # Fire pre_gateway_dispatch plugin hook for user-originated messages.

        # Plugins receive the MessageEvent and may return a dict influencing flow:

        #   {"action": "skip",    "reason": ...}    -> drop (no reply, plugin handled)

        #   {"action": "rewrite", "text":  ...}     -> replace event.text, continue

        #   {"action": "allow"}   /   None          -> normal dispatch

        # Hook runs BEFORE auth so plugins can handle unauthorized senders

        # (e.g. customer handover ingest) without triggering the pairing flow.

        if not is_internal:

            try:

                from intellect_cli.plugins import invoke_hook as _invoke_hook

                _hook_results = _invoke_hook(

                    "pre_gateway_dispatch",

                    event=event,

                    gateway=self,

                    session_store=self.session_store,

                )

            except Exception as _hook_exc:

                logger.warning("pre_gateway_dispatch invocation failed: %s", _hook_exc)

                _hook_results = []

            for _result in _hook_results:

                if not isinstance(_result, dict):

                    continue

                _action = _result.get("action")

                if _action == "skip":

                    logger.info(

                        "pre_gateway_dispatch skip: reason=%s platform=%s chat=%s",

                        _result.get("reason"),

                        source.platform.value if source.platform else "unknown",

                        source.chat_id or "unknown",

                    )

                    return None

                if _action == "rewrite":

                    _new_text = _result.get("text")

                    if isinstance(_new_text, str):

                        event = dataclasses.replace(event, text=_new_text)

                        source = event.source

                    break

                if _action == "allow":

                    break

        if is_internal:

            pass

        elif source.user_id is None:

            # Messages with no user identity (Telegram service messages,

            # channel forwards, anonymous admin posts, sender_chat) can't

            # be paired, but they can still be authorized via a

            # chat-scoped allowlist (e.g. TELEGRAM_GROUP_ALLOWED_CHATS

            # authorizes every member of the listed chat regardless of

            # sender). Defer to _is_user_authorized so that path runs.

            if not self._is_user_authorized(source):

                logger.debug("Ignoring message with no user_id from %s", source.platform.value)

                return None

        elif not self._is_user_authorized(source):

            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)

            # In DMs: offer pairing code. In groups: silently ignore.

            if source.chat_type == "dm" and self._get_unauthorized_dm_behavior(source.platform) == "pair":

                platform_name = source.platform.value if source.platform else "unknown"

                # Rate-limit ALL pairing responses (code or rejection) to

                # prevent spamming the user with repeated messages when

                # multiple DMs arrive in quick succession.

                if self.pairing_store._is_rate_limited(platform_name, source.user_id):

                    return None

                code = self.pairing_store.generate_code(

                    platform_name, source.user_id, source.user_name or ""

                )

                if code:

                    adapter = self.adapters.get(source.platform)

                    if adapter:

                        await adapter.send(

                            source.chat_id,

                            f"Hi~ I don't recognize you yet!\n\n"

                            f"Here's your pairing code: `{code}`\n\n"

                            f"Ask the bot owner to run:\n"

                            f"`intellect pairing approve {platform_name} {code}`"

                        )

                else:

                    adapter = self.adapters.get(source.platform)

                    if adapter:

                        await adapter.send(

                            source.chat_id,

                            "Too many pairing requests right now~ "

                            "Please try again later!"

                        )

                    # Record rate limit so subsequent messages are silently ignored

                    self.pairing_store._record_rate_limit(platform_name, source.user_id)

            return None

        # Intercept messages that are responses to a pending /update prompt.

        # The update process (detached) wrote .update_prompt.json; the watcher

        # forwarded it to the user; now the user's reply goes back via

        # .update_response so the update process can continue.

        #

        # IMPORTANT: recognized slash commands must bypass this interception.

        # Otherwise control/session commands like /new or /help get silently

        # consumed as update answers instead of being dispatched normally.

        _quick_key = self._session_key_for_source(source)

        _update_prompts = getattr(self, "_update_prompt_pending", {})

        if _update_prompts.get(_quick_key):

            raw = (event.text or "").strip()

            # Accept /approve and /deny as shorthand for yes/no

            cmd = event.get_command()

            if cmd in {"approve", "yes"}:

                response_text = "y"

            elif cmd in {"deny", "no"}:

                response_text = "n"

            else:

                _recognized_cmd = None

                if cmd:

                    try:

                        from intellect_cli.commands import resolve_command as _resolve_update_cmd

                    except Exception:

                        _resolve_update_cmd = None

                    if _resolve_update_cmd is not None:

                        try:

                            _cmd_def = _resolve_update_cmd(cmd)

                            _recognized_cmd = _cmd_def.name if _cmd_def else None

                        except Exception:

                            _recognized_cmd = None

                if _recognized_cmd:

                    response_text = ""

                else:

                    response_text = raw

            if response_text:

                response_path = _intellect_home / ".update_response"

                prompt_path = _intellect_home / ".update_prompt.json"

                try:

                    tmp = response_path.with_suffix(".tmp")

                    tmp.write_text(response_text)

                    tmp.replace(response_path)

                    prompt_path.unlink(missing_ok=True)

                except OSError as e:

                    logger.warning("Failed to write update response: %s", e)

                    return f"✗ Failed to send response to update process: {e}"

                _update_prompts.pop(_quick_key, None)

                label = response_text if len(response_text) <= 20 else response_text[:20] + "…"

                return f"✓ Sent `{label}` to the update process."

            # Recognized slash command during a pending update prompt:

            # unblock the detached update subprocess by writing a blank

            # response so ``_gateway_prompt`` returns the prompt's default

            # (typically a safe "n" / skip) and exits cleanly instead of

            # blocking on stdin until the 30-minute watcher timeout.

            # The slash command then falls through to normal dispatch.

            if _recognized_cmd:

                response_path = _intellect_home / ".update_response"

                prompt_path = _intellect_home / ".update_prompt.json"

                try:

                    tmp = response_path.with_suffix(".tmp")

                    tmp.write_text("")

                    tmp.replace(response_path)

                    prompt_path.unlink(missing_ok=True)

                    logger.info(

                        "Recognized /%s during pending update prompt for %s; "

                        "cancelled prompt with default and dispatching command",

                        _recognized_cmd,

                        _quick_key,

                    )

                except OSError as e:

                    logger.warning(

                        "Failed to write cancel response for pending update prompt: %s",

                        e,

                    )

                _update_prompts.pop(_quick_key, None)

        # Intercept messages that are responses to a pending clarify

        # request that is awaiting free-form text (either an open-ended

        # clarify with no choices, or one where the user picked the

        # "Other" button).  The first non-empty user message in the

        # session resolves the clarify and unblocks the agent thread —

        # we do NOT route it to the agent as a new turn.

        try:

            from tools import clarify_gateway as _clarify_mod

            _pending_clarify = _clarify_mod.get_pending_for_session(_quick_key)

        except Exception:

            _pending_clarify = None

        if _pending_clarify is not None:

            _raw_clarify_reply = (event.text or "").strip()

            # Skip slash commands — the user clearly wanted to issue a

            # command, not answer the clarify.  Leave the clarify pending

            # so the user can retry; if it times out, the agent unblocks

            # with an empty response.

            if _raw_clarify_reply and not _raw_clarify_reply.startswith("/"):

                _resolved = _clarify_mod.resolve_gateway_clarify(

                    _pending_clarify.clarify_id, _raw_clarify_reply,

                )

                if _resolved:

                    logger.info(

                        "Gateway intercepted clarify text response (session=%s, id=%s)",

                        _quick_key, _pending_clarify.clarify_id,

                    )

                    # Acknowledge with empty string so adapters that emit

                    # the agent's response don't double-post.  The agent

                    # itself will produce the next user-facing message.

                    return ""

        # Intercept messages that are responses to a pending /reload-mcp

        # (or future) slash-confirm prompt.  Recognized confirm replies are

        # /approve, /always, /cancel (plus short aliases).  Anything else

        # falls through to normal dispatch — a stale pending confirm does

        # NOT block other commands.

        #

        # Important: if a dangerous-command approval is ALSO pending (agent

        # blocked inside tools/approval.py), the tool approval takes

        # precedence — /approve there unblocks the waiting tool thread.

        # Slash-confirm only catches /approve when no tool approval is live.

        from tools import slash_confirm as _slash_confirm_mod

        _pending_confirm = _slash_confirm_mod.get_pending(_quick_key)

        _tool_approval_live = False

        try:

            from tools.approval import has_blocking_approval

            _tool_approval_live = has_blocking_approval(_quick_key)

        except Exception:

            _tool_approval_live = False

        if _pending_confirm and not _tool_approval_live:

            _raw_reply = (event.text or "").strip()

            _cmd_reply = event.get_command()

            _confirm_choice = None

            if _cmd_reply in {"approve", "yes", "ok", "confirm"}:

                _confirm_choice = "once"

            elif _cmd_reply in {"always", "remember"}:

                _confirm_choice = "always"

            elif _cmd_reply in {"cancel", "no", "deny", "nevermind"}:

                _confirm_choice = "cancel"

            elif _raw_reply.lower() in {"approve", "approve once", "once"}:

                _confirm_choice = "once"

            elif _raw_reply.lower() in {"always", "always approve"}:

                _confirm_choice = "always"

            elif _raw_reply.lower() in {"cancel", "nevermind", "no"}:

                _confirm_choice = "cancel"

            if _confirm_choice is not None:

                _resolved = await _slash_confirm_mod.resolve(

                    _quick_key, _pending_confirm.get("confirm_id"), _confirm_choice,

                )

                return _resolved or ""

            # Stale pending + unrelated command: drop the pending state so

            # the confirm doesn't block normal usage indefinitely.  The user

            # clearly moved on.

            _slash_confirm_mod.clear_if_stale(_quick_key)

        # PRIORITY handling when an agent is already running for this session.

        # Default behavior is to interrupt immediately so user text/stop messages

        # are handled with minimal latency.

        #

        # Special case: Telegram/photo bursts often arrive as multiple near-

        # simultaneous updates. Do NOT interrupt for photo-only follow-ups here;

        # let the adapter-level batching/queueing logic absorb them.

        # Staleness eviction: detect leaked locks from hung/crashed handlers.

        # With inactivity-based timeout, active tasks can run for hours, so

        # wall-clock age alone isn't sufficient.  Evict only when the agent

        # has been *idle* beyond the inactivity threshold (or when the agent

        # object has no activity tracker and wall-clock age is extreme).

        _raw_stale_timeout = _float_env("intellect_AGENT_TIMEOUT", 1800)

        _stale_ts = self._running_agents_ts.get(_quick_key, 0)

        if _quick_key in self._running_agents and _stale_ts:

            _stale_age = time.time() - _stale_ts

            _stale_agent = self._running_agents.get(_quick_key)

            # Never evict the pending sentinel — it was just placed moments

            # ago during the async setup phase before the real agent is

            # created.  Sentinels have no get_activity_summary(), so the

            # idle check below would always evaluate to inf >= timeout and

            # immediately evict them, racing with the setup path.

            _stale_idle = float("inf")  # assume idle if we can't check

            _stale_detail = ""

            if _stale_agent and hasattr(_stale_agent, "get_activity_summary"):

                try:

                    _sa = _stale_agent.get_activity_summary()

                    _stale_idle = _sa.get("seconds_since_activity", float("inf"))

                    _stale_detail = (

                        f" | last_activity={_sa.get('last_activity_desc', 'unknown')} "

                        f"({_stale_idle:.0f}s ago) "

                        f"| iteration={_sa.get('api_call_count', 0)}/{_sa.get('max_iterations', 0)}"

                    )

                except Exception:

                    _log_non_critical()

            # Evict if: agent is idle beyond timeout, OR wall-clock age is

            # extreme (10x timeout or 2h, whichever is larger — catches

            # cases where the agent object was garbage-collected).

            _wall_ttl = max(_raw_stale_timeout * 10, 7200) if _raw_stale_timeout > 0 else float("inf")

            _should_evict = (

                _stale_agent is not _AGENT_PENDING_SENTINEL

                and (

                    (_raw_stale_timeout > 0 and _stale_idle >= _raw_stale_timeout)

                    or _stale_age > _wall_ttl

                )

            )

            if _should_evict:

                logger.warning(

                    "Evicting stale _running_agents entry for %s "

                    "(age: %.0fs, idle: %.0fs, timeout: %.0fs)%s",

                    _quick_key, _stale_age, _stale_idle,

                    _raw_stale_timeout, _stale_detail,

                )

                self._invalidate_session_run_generation(

                    _quick_key,

                    reason="stale_running_agent_eviction",

                )

                self._release_running_agent_state(_quick_key)

        if _quick_key in self._running_agents:

            if event.get_command() == "status":

                return await self._handle_status_command(event)

            # Resolve the command once for all early-intercept checks below.

            from intellect_cli.commands import (

                ACTIVE_SESSION_BYPASS_COMMANDS as _DEDICATED_HANDLERS,

                resolve_command as _resolve_cmd_inner,

            )

            _evt_cmd = event.get_command()

            _cmd_def_inner = _resolve_cmd_inner(_evt_cmd) if _evt_cmd else None

            # Slash command access control on the running-agent fast-path.

            # Mirrors the cold-path gate further below so non-admin users

            # can't bypass gating just because an agent happens to be busy.

            # /status above is intentionally pre-gate so users always see

            # session state. /help and /whoami fall under the always-allowed

            # floor inside _check_slash_access.

            if _evt_cmd and _cmd_def_inner is not None:

                _denied = self._check_slash_access(source, _cmd_def_inner.name)

                if _denied is not None:

                    return _denied

            # Telegram sends /start for bot launches/deep-links. Treat it as a

            # platform ping, not a user command: no help dump, no agent

            # interrupt, no queued text.

            if _cmd_def_inner and _cmd_def_inner.name == "start":

                logger.info("Ignoring /start platform ping for active session %s", _quick_key)

                return ""

            if _cmd_def_inner and _cmd_def_inner.name == "restart":

                return await self._handle_restart_command(event)

            # /stop must hard-kill the session when an agent is running.

            # A soft interrupt (agent.interrupt()) doesn't help when the agent

            # is truly hung — the executor thread is blocked and never checks

            # _interrupt_requested.  Force-clean _running_agents so the session

            # is unlocked and subsequent messages are processed normally.

            if _cmd_def_inner and _cmd_def_inner.name == "stop":

                await self._interrupt_and_clear_session(

                    _quick_key,

                    source,

                    interrupt_reason=_INTERRUPT_REASON_STOP,

                    invalidation_reason="stop_command",

                )

                logger.info("STOP for session %s — agent interrupted, session lock released", _quick_key)

                return EphemeralReply(t("gateway.stop.stopped"))

            # /reset and /new must bypass the running-agent guard so they

            # actually dispatch as commands instead of being queued as user

            # text (which would be fed back to the agent with the same

            # broken history — #2170).  Interrupt the agent first, then

            # clear the adapter's pending queue so the stale "/reset" text

            # doesn't get re-processed as a user message after the

            # interrupt completes.

            if _cmd_def_inner and _cmd_def_inner.name == "new":

                # Clear any pending messages so the old text doesn't replay

                await self._interrupt_and_clear_session(

                    _quick_key,

                    source,

                    interrupt_reason=_INTERRUPT_REASON_RESET,

                    invalidation_reason="new_command",

                )

                # Clean up the running agent entry so the reset handler

                # doesn't think an agent is still active.

                return await self._handle_reset_command(event)

            # /queue <prompt> — queue without interrupting.

            # Semantics: each /queue invocation produces its own full agent

            # turn, processed in FIFO order after the current run (and any

            # earlier /queue items) finishes.  Messages are NOT merged.

            if event.get_command() in {"queue", "q"}:

                queued_text = event.get_command_args().strip()

                if not queued_text:

                    return "Usage: /queue <prompt>"

                adapter = self.adapters.get(source.platform)

                if adapter:

                    queued_event = MessageEvent(

                        text=queued_text,

                        message_type=MessageType.TEXT,

                        source=event.source,

                        message_id=event.message_id,

                        channel_prompt=event.channel_prompt,

                    )

                    self._enqueue_fifo(_quick_key, queued_event, adapter)

                depth = self._queue_depth(_quick_key, adapter=self.adapters.get(source.platform))

                if depth <= 1:

                    return "Queued for the next turn."

                return f"Queued for the next turn. ({depth} queued)"

            # /steer <prompt> — inject mid-run after the next tool call.

            # Unlike /queue (turn boundary), /steer lands BETWEEN tool-call

            # iterations inside the same agent run, by appending to the

            # last tool result's content. No interrupt, no new user turn,

            # no role-alternation violation.

            if _cmd_def_inner and _cmd_def_inner.name == "steer":

                steer_text = event.get_command_args().strip()

                if not steer_text:

                    return "Usage: /steer <prompt>"

                running_agent = self._running_agents.get(_quick_key)

                if running_agent is _AGENT_PENDING_SENTINEL:

                    # Agent hasn't started yet — queue as turn-boundary fallback.

                    adapter = self.adapters.get(source.platform)

                    if adapter:

                        queued_event = MessageEvent(

                            text=steer_text,

                            message_type=MessageType.TEXT,

                            source=event.source,

                            message_id=event.message_id,

                            channel_prompt=event.channel_prompt,

                        )

                        adapter._pending_messages[_quick_key] = queued_event

                    return "Agent still starting — /steer queued for the next turn."

                if running_agent and hasattr(running_agent, "steer"):

                    try:

                        accepted = running_agent.steer(steer_text)

                    except Exception as exc:

                        logger.warning("Steer failed for session %s: %s", _quick_key, exc)

                        return f"⚠️ Steer failed: {exc}"

                    if accepted:

                        preview = steer_text[:60] + ("..." if len(steer_text) > 60 else "")

                        return f"⏩ Steer queued — arrives after the next tool call: '{preview}'"

                    return "Steer rejected (empty payload)."

                # Running agent is missing or lacks steer() — fall back to queue.

                adapter = self.adapters.get(source.platform)

                if adapter:

                    queued_event = MessageEvent(

                        text=steer_text,

                        message_type=MessageType.TEXT,

                        source=event.source,

                        message_id=event.message_id,

                        channel_prompt=event.channel_prompt,

                    )

                    adapter._pending_messages[_quick_key] = queued_event

                return "No active agent — /steer queued for the next turn."

            # /model must not be used while the agent is running.

            if _cmd_def_inner and _cmd_def_inner.name == "model":

                return "Agent is running — wait or /stop first, then switch models."

            # /codex-runtime must not be used while the agent is running.

            # Switching mid-turn would split a turn across two transports.

            if _cmd_def_inner and _cmd_def_inner.name == "codex-runtime":

                return ("Agent is running — wait or /stop first, then "

                        "change runtime.")

            # /approve and /deny must bypass the running-agent interrupt path.

            # The agent thread is blocked on a threading.Event inside

            # tools/approval.py — sending an interrupt won't unblock it.

            # Route directly to the approval handler so the event is signalled.

            if _cmd_def_inner and _cmd_def_inner.name in {"approve", "deny"}:

                if _cmd_def_inner.name == "approve":

                    return await self._handle_approve_command(event)

                return await self._handle_deny_command(event)

            # /agents (/tasks alias) should be query-only and never interrupt.

            if _cmd_def_inner and _cmd_def_inner.name == "agents":

                return await self._handle_agents_command(event)

            # /background must bypass the running-agent guard — it starts a

            # parallel task and must never interrupt the active conversation.

            # /btw is an alias of /background and resolves to the same canonical

            # name, so this branch handles both commands.

            if _cmd_def_inner and _cmd_def_inner.name == "background":

                return await self._handle_background_command(event)

            # /kanban must bypass the guard. It writes to a profile-agnostic

            # DB (kanban.db), not to the running agent's state. In fact

            # /kanban unblock is often the only way to free a worker that

            # has blocked waiting for a peer — letting that be dispatched

            # mid-run is the whole point of the board.

            if _cmd_def_inner and _cmd_def_inner.name == "kanban":

                return await self._handle_kanban_command(event)

            # /goal is safe mid-run for status/pause/clear (inspection and

            # control-plane only — doesn't interrupt the running turn).

            # Setting a new goal text mid-run is rejected with the same

            # "wait or /stop" message as /model so we don't race a second

            # continuation prompt against the current turn.

            if _cmd_def_inner and _cmd_def_inner.name == "goal":

                _goal_arg = (event.get_command_args() or "").strip().lower()

                if not _goal_arg or _goal_arg in {"status", "pause", "resume", "clear", "stop", "done"}:

                    return await self._handle_goal_command(event)

                return "Agent is running — use /goal status / pause / clear mid-run, or /stop before setting a new goal."

            # /subgoal is safe mid-run — it only modifies the goal's

            # subgoals list, which the judge reads at the next turn

            # boundary. No race with the running turn.

            if _cmd_def_inner and _cmd_def_inner.name == "subgoal":

                return await self._handle_subgoal_command(event)

            # Session-level toggles that are safe to run mid-agent —

            # /yolo can unblock a pending approval prompt, /verbose cycles

            # the tool-progress display mode for the ongoing stream.

            # Both modify session state without needing agent interaction

            # and must not be queued (the safety net would discard them).

            # /fast and /reasoning are config-only and take effect next

            # message, so they fall through to the catch-all busy response

            # below — users should wait and set them between turns.

            if _cmd_def_inner and _cmd_def_inner.name in {"yolo", "verbose"}:

                if _cmd_def_inner.name == "yolo":

                    return await self._handle_yolo_command(event)

                if _cmd_def_inner.name == "verbose":

                    return await self._handle_verbose_command(event)

                if _cmd_def_inner.name == "footer":

                    return await self._handle_footer_command(event)

            # Gateway-handled info/control commands with dedicated

            # running-agent handlers.

            if _cmd_def_inner and _cmd_def_inner.name in _DEDICATED_HANDLERS:

                if _cmd_def_inner.name == "help":

                    return await self._handle_help_command(event)

                if _cmd_def_inner.name == "commands":

                    return await self._handle_commands_command(event)

                if _cmd_def_inner.name == "profile":

                    return await self._handle_profile_command(event)

                if _cmd_def_inner.name == "update":

                    return await self._handle_update_command(event)

            # Catch-all: any other recognized slash command reached the

            # running-agent guard. Reject gracefully rather than falling

            # through to interrupt + discard. Without this, commands

            # like /model, /reasoning, /voice, /insights, /title,

            # /resume, /retry, /undo, /compress, /usage,

            # /reload-mcp, /sethome, /reset (all registered as Discord

            # slash commands) would interrupt the agent AND get

            # silently discarded by the slash-command safety net,

            # producing a zero-char response. See #5057, #6252, #10370.

            if _cmd_def_inner:

                return (

                    f"⏳ Agent is running — `/{_cmd_def_inner.name}` can't run "

                    f"mid-turn. Wait for the current response or `/stop` first."

                )

            if event.message_type == MessageType.PHOTO:

                logger.debug("PRIORITY photo follow-up for session %s — queueing without interrupt", _quick_key)

                adapter = self.adapters.get(source.platform)

                if adapter:

                    merge_pending_message_event(adapter._pending_messages, _quick_key, event)

                return None

            _telegram_followup_grace = float(

                os.getenv("intellect_TELEGRAM_FOLLOWUP_GRACE_SECONDS", "3.0")

            )

            _started_at = self._running_agents_ts.get(_quick_key, 0)

            if (

                source.platform == Platform.TELEGRAM

                and event.message_type == MessageType.TEXT

                and _telegram_followup_grace > 0

                and _started_at

                and (time.time() - _started_at) <= _telegram_followup_grace

            ):

                logger.debug(

                    "Telegram follow-up arrived %.2fs after run start for %s — queueing without interrupt",

                    time.time() - _started_at,

                    _quick_key,

                )

                adapter = self.adapters.get(source.platform)

                if adapter:

                    merge_pending_message_event(

                        adapter._pending_messages,

                        _quick_key,

                        event,

                        merge_text=True,

                    )

                return None

            running_agent = self._running_agents.get(_quick_key)

            if running_agent is _AGENT_PENDING_SENTINEL:

                # Agent is being set up but not ready yet.

                if event.get_command() == "stop":

                    # Force-clean the sentinel so the session is unlocked.

                    self._release_running_agent_state(_quick_key)

                    logger.info("HARD STOP (pending) for session %s — sentinel cleared", _quick_key)

                    return EphemeralReply("⚡ Force-stopped. The agent was still starting — session unlocked.")

                # Queue the message so it will be picked up after the

                # agent starts.

                adapter = self.adapters.get(source.platform)

                if adapter:

                    merge_pending_message_event(

                        adapter._pending_messages,

                        _quick_key,

                        event,

                        merge_text=True,

                    )

                return None

            if self._draining:

                if self._queue_during_drain_enabled():

                    self._queue_or_replace_pending_event(_quick_key, event)

                return (

                    f"⏳ Gateway {self._status_action_gerund()} — queued for the next turn after it comes back."

                    if self._queue_during_drain_enabled()

                    else f"⏳ Gateway is {self._status_action_gerund()} and is not accepting another turn right now."

                )

            if self._busy_input_mode == "queue":

                logger.debug("PRIORITY queue follow-up for session %s", _quick_key)

                self._queue_or_replace_pending_event(_quick_key, event)

                return None

            if self._busy_input_mode == "steer":

                # Steer mode: inject text into the running agent mid-run via

                # agent.steer().  Falls back to queue semantics if the payload

                # is empty, the agent lacks steer(), or steer() rejects.

                steer_text = (event.text or "").strip()

                steered = False

                if steer_text and hasattr(running_agent, "steer"):

                    try:

                        steered = bool(running_agent.steer(steer_text))

                    except Exception as exc:

                        logger.warning("PRIORITY steer failed for session %s: %s", _quick_key, exc)

                        steered = False

                if steered:

                    logger.debug("PRIORITY steer for session %s", _quick_key)

                    return None

                logger.debug("PRIORITY steer-fallback-to-queue for session %s", _quick_key)

                self._queue_or_replace_pending_event(_quick_key, event)

                return None

            # #30170 — Subagent protection (PRIORITY path). Same rationale

            # as ``_handle_active_session_busy_message``: an interrupt

            # cascades through ``_active_children`` and aborts in-flight

            # delegate_task work. Demote to queue semantics when the

            # parent is currently driving subagents so a conversational

            # follow-up doesn't destroy minutes of subagent progress.

            # /stop reaches its dedicated handler above, so the operator

            # still has a clean escape hatch.

            if self._agent_has_active_subagents(running_agent):

                logger.info(

                    "PRIORITY interrupt demoted to queue for session %s "

                    "because the running agent has active subagents (#30170)",

                    _quick_key,

                )

                self._queue_or_replace_pending_event(_quick_key, event)

                return None

            logger.debug("PRIORITY interrupt for session %s", _quick_key)

            running_agent.interrupt(event.text)

            # NOTE: self._pending_messages was write-only (never consumed).

            # The actual interrupt message is delivered via adapter._pending_messages

            # which is read by _run_agent. Removed to prevent unbounded growth.

            return None

        # Check for commands

        command = event.get_command()

        from intellect_cli.commands import (

            GATEWAY_KNOWN_COMMANDS,

            is_gateway_known_command,

            resolve_command as _resolve_cmd,

        )

        # Resolve aliases to canonical name so dispatch and hook names

        # don't depend on the exact alias the user typed.

        _cmd_def = _resolve_cmd(command) if command else None

        canonical = _cmd_def.name if _cmd_def else command

        # Expand alias quick commands before built-in dispatch so targets like

        # /model openai/gpt-5.5 --provider openrouter reach the /model handler.

        # Preserve built-in precedence; aliases only need early handling when

        # the typed command is not already known.

        if command and _cmd_def is None:

            if isinstance(self.config, dict):

                quick_commands = self.config.get("quick_commands", {}) or {}

            else:

                quick_commands = getattr(self.config, "quick_commands", {}) or {}

            if isinstance(quick_commands, dict) and command in quick_commands:

                qcmd = quick_commands[command]

                if qcmd.get("type") == "alias":

                    target = qcmd.get("target", "").strip()

                    if target:

                        target = target if target.startswith("/") else f"/{target}"

                        target_command = target.lstrip("/")

                        user_args = event.get_command_args().strip()

                        event.text = f"{target} {user_args}".strip()

                        command = target_command.split()[0] if target_command else target_command

                        _cmd_def = _resolve_cmd(command) if command else None

                        canonical = _cmd_def.name if _cmd_def else command

        # Per-platform slash command access control. Only kicks in when the

        # operator has set ``allow_admin_from`` for the source's scope (DM

        # vs group). When unset → backward-compat: every allowed user can

        # run every command. When set → non-admins can run only commands in

        # ``user_allowed_commands`` (plus the always-allowed floor: /help,

        # /whoami). Plain chat is unaffected — only slash commands gate.

        if command and canonical and is_gateway_known_command(canonical):

            _denied = self._check_slash_access(source, canonical)

            if _denied is not None:

                return _denied

        # Fire the ``command:<canonical>`` hook for any recognized slash

        # command — built-in OR plugin-registered. Handlers can return a

        # dict with ``{"decision": "deny" | "handled" | "rewrite", ...}``

        # to intercept dispatch before core handling runs. This replaces

        # the previous fire-and-forget emit(): return values are now

        # honored, but handlers that return nothing behave exactly as

        # before (telemetry-style hooks keep working).

        if command and is_gateway_known_command(canonical):

            raw_args = event.get_command_args().strip()

            hook_ctx = {

                "platform": source.platform.value if source.platform else "",

                "user_id": source.user_id,

                "command": canonical,

                "raw_command": command,

                "args": raw_args,

                "raw_args": raw_args,

            }

            try:

                hook_results = await self.hooks.emit_collect(

                    f"command:{canonical}", hook_ctx

                )

            except Exception as _hook_err:

                logger.debug(

                    "command:%s hook dispatch failed (non-fatal): %s",

                    canonical, _hook_err,

                )

                hook_results = []

            for hook_result in hook_results:

                if not isinstance(hook_result, dict):

                    continue

                decision = str(hook_result.get("decision", "")).strip().lower()

                if not decision or decision == "allow":

                    continue

                if decision == "deny":

                    message = hook_result.get("message")

                    if isinstance(message, str) and message:

                        return message

                    return f"Command `/{command}` was blocked by a hook."

                if decision == "handled":

                    message = hook_result.get("message")

                    return message if isinstance(message, str) and message else None

                if decision == "rewrite":

                    new_command = str(

                        hook_result.get("command_name", "")

                    ).strip().lstrip("/")

                    if not new_command:

                        continue

                    new_args = str(hook_result.get("raw_args", "")).strip()

                    event.text = f"/{new_command} {new_args}".strip()

                    command = event.get_command()

                    _cmd_def = _resolve_cmd(command) if command else None

                    canonical = _cmd_def.name if _cmd_def else command

                    break

        # ── Slash command dispatch ──────────────────────────────────────

        _dispatched = await self._dispatch_command(canonical, event, source)

        if _dispatched is not self._DISPATCH_NOT_FOUND:

            return _dispatched

        if self._draining:

            return f"⏳ Gateway is {self._status_action_gerund()} and is not accepting new work right now."

        # User-defined quick commands (bypass agent loop, no LLM call)

        if command:

            if isinstance(self.config, dict):

                quick_commands = self.config.get("quick_commands", {}) or {}

            else:

                quick_commands = getattr(self.config, "quick_commands", {}) or {}

            if not isinstance(quick_commands, dict):

                quick_commands = {}

            if command in quick_commands:

                qcmd = quick_commands[command]

                if qcmd.get("type") == "exec":

                    exec_cmd = qcmd.get("command", "")

                    if exec_cmd:

                        try:

                            # Sanitize env to prevent credential leakage —

                            # quick commands run in the gateway process which

                            # has all API keys in os.environ.

                            from tools.environments.local import _sanitize_subprocess_env

                            sanitized_env = _sanitize_subprocess_env(os.environ.copy())

                            proc = await asyncio.create_subprocess_shell(

                                exec_cmd,

                                stdout=asyncio.subprocess.PIPE,

                                stderr=asyncio.subprocess.PIPE,

                                env=sanitized_env,

                            )

                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

                            output = (stdout or stderr).decode().strip()

                            # Redact any remaining sensitive patterns in output

                            if output:

                                from agent.redact import redact_sensitive_text

                                output = redact_sensitive_text(output)

                            return output if output else "Command returned no output."

                        except asyncio.TimeoutError:

                            return "Quick command timed out (30s)."

                        except Exception as e:

                            return f"Quick command error: {e}"

                    else:

                        return f"Quick command '/{command}' has no command defined."

                elif qcmd.get("type") == "alias":

                    target = qcmd.get("target", "").strip()

                    if target:

                        target = target if target.startswith("/") else f"/{target}"

                        target_command = target.lstrip("/")

                        user_args = event.get_command_args().strip()

                        event.text = f"{target} {user_args}".strip()

                        command = target_command.split()[0] if target_command else target_command

                        # Fall through to normal command dispatch below

                    else:

                        return f"Quick command '/{command}' has no target defined."

                else:

                    return f"Quick command '/{command}' has unsupported type (supported: 'exec', 'alias')."

        # Plugin-registered slash commands

        if command:

            try:

                from intellect_cli.plugins import get_plugin_command_handler

                # Normalize underscores to hyphens so Telegram's underscored

                # autocomplete form matches plugin commands registered with

                # hyphens. See intellect_cli/commands.py:_build_telegram_menu.

                plugin_handler = get_plugin_command_handler(command.replace("_", "-"))

                if plugin_handler:

                    user_args = event.get_command_args().strip()

                    result = plugin_handler(user_args)

                    if asyncio.iscoroutine(result):

                        result = await result

                    return str(result) if result else None

            except Exception as e:

                logger.warning("Plugin command dispatch failed: %s", e)

        # Skill slash commands: /skill-name loads the skill and sends to agent.

        # resolve_skill_command_key() handles the Telegram underscore/hyphen

        # round-trip so /claude_code from Telegram autocomplete still resolves

        # to the claude-code skill.

        if command:

            # Skill bundles take precedence over individual skill commands —

            # /<bundle> loads multiple skills at once. Mirrors CLI dispatch.

            _bundle_handled = False

            try:

                from agent.skill_bundles import (

                    build_bundle_invocation_message,

                    resolve_bundle_command_key,

                )

                bundle_key = resolve_bundle_command_key(command)

                if bundle_key is not None:

                    user_instruction = event.get_command_args().strip()

                    bundle_result = build_bundle_invocation_message(

                        bundle_key, user_instruction, task_id=_quick_key

                    )

                    if bundle_result:

                        msg, _loaded, missing = bundle_result

                        event.text = msg

                        _bundle_handled = True

                        if missing:

                            logger.info(

                                "Bundle %s skipped missing skills: %s",

                                bundle_key, ", ".join(missing),

                            )

                        # Fall through to normal message processing with bundle content

            except Exception as exc:

                logger.warning("Bundle dispatch failed: %s", exc)

        if command and not locals().get("_bundle_handled", False):

            try:

                from agent.skill_commands import (

                    get_skill_commands,

                    build_skill_invocation_message,

                    resolve_skill_command_key,

                )

                skill_cmds = get_skill_commands()

                cmd_key = resolve_skill_command_key(command)

                if cmd_key is not None:

                    # Check per-platform disabled status before executing.

                    # get_skill_commands() only applies the *global* disabled

                    # list at scan time; per-platform overrides need checking

                    # here because the cache is process-global across platforms.

                    _skill_name = skill_cmds[cmd_key].get("name", "")

                    _plat = source.platform.value if source.platform else None

                    if _plat and _skill_name:

                        from agent.skill_utils import get_disabled_skill_names as _get_plat_disabled

                        if _skill_name in _get_plat_disabled(platform=_plat):

                            return (

                                f"The **{_skill_name}** skill is disabled for {_plat}.\n"

                                f"Enable it with: `intellect skills config`"

                            )

                    user_instruction = event.get_command_args().strip()

                    msg = build_skill_invocation_message(

                        cmd_key, user_instruction, task_id=_quick_key

                    )

                    if msg:

                        event.text = msg

                        # Fall through to normal message processing with skill content

                else:

                    # Not an active skill — check if it's a known-but-disabled or

                    # uninstalled skill and give actionable guidance.

                    _unavail_msg = _check_unavailable_skill(command)

                    if _unavail_msg:

                        return _unavail_msg

                    # Genuinely unrecognized /command: not a built-in, not a

                    # plugin, not a skill, not a known-inactive skill. Warn

                    # the user instead of silently forwarding it to the LLM

                    # as free text (which leads to silent-failure behavior

                    # like the model inventing a delegate_task call).

                    # Normalize to hyphenated form before checking known

                    # built-ins (command may be an alias target set by the

                    # quick-command block above, so _cmd_def can be stale).

                    if command.replace("_", "-") not in GATEWAY_KNOWN_COMMANDS:

                        logger.warning(

                            "Unrecognized slash command /%s from %s — "

                            "replying with unknown-command notice",

                            command,

                            source.platform.value if source.platform else "?",

                        )

                        return (

                            f"Unknown command `/{command}`. "

                            f"Type /commands to see what's available, "

                            f"or resend without the leading slash to send "

                            f"as a regular message."

                        )

            except Exception as e:

                logger.debug("Skill command check failed (non-fatal): %s", e)

        # Pending exec approvals are handled by /approve and /deny commands above.

        # No bare text matching — "yes" in normal conversation must not trigger

        # execution of a dangerous command.

        if self._is_telegram_topic_root_lobby(source):

            # Debounce the lobby reminder so a user who forgets about

            # topic mode and fires ten prompts doesn't get ten copies.

            if self._should_send_telegram_lobby_reminder(source):

                return self._telegram_topic_root_lobby_message()

            return None

        # Member RBAC — after slash-command dispatch, before agent turn.

        _member_denied = self._check_member_chat_for_agent(source)

        if _member_denied:

            return _member_denied

        # ── Claim this session before any await ───────────────────────

        # Between here and _run_agent registering the real AIAgent, there

        # are numerous await points (hooks, vision enrichment, STT,

        # session hygiene compression).  Without this sentinel a second

        # message arriving during any of those yields would pass the

        # "already running" guard and spin up a duplicate agent for the

        # same session — corrupting the transcript.

        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL

        self._running_agents_ts[_quick_key] = time.time()

        _run_generation = self._begin_session_run_generation(_quick_key)

        try:

            _agent_result = await self._handle_message_with_agent(event, source, _quick_key, _run_generation)

            # Goal continuation: after the agent returns a final response

            # for this turn, check any standing /goal — the judge will

            # either mark it done, pause it (budget), or enqueue a

            # continuation prompt back through the adapter FIFO so the

            # next turn makes more progress. Wrapped in try/except so a

            # broken judge never breaks normal message handling.

            try:

                _final_text = ""

                if isinstance(_agent_result, dict):

                    _final_text = str(_agent_result.get("final_response") or "")

                elif isinstance(_agent_result, str):

                    _final_text = _agent_result

                # Skip for empty responses (interrupted / errored) — the

                # judge would almost always say "continue" and we'd loop

                # on error. Let the user drive the next turn.

                if _final_text.strip():

                    try:

                        session_entry = self.session_store.get_or_create_session(source)

                    except Exception:

                        session_entry = None

                    if session_entry is not None:

                        await self._post_turn_goal_continuation(

                            session_entry=session_entry,

                            source=source,

                            final_response=_final_text,

                        )

            except Exception as _goal_exc:

                logger.debug("goal continuation hook failed: %s", _goal_exc)

            return _agent_result

        finally:

            # If _run_agent replaced the sentinel with a real agent and

            # then cleaned it up, this is a no-op.  If we exited early

            # (exception, command fallthrough, etc.) the sentinel must

            # not linger or the session would be permanently locked out.

            if self._running_agents.get(_quick_key) is _AGENT_PENDING_SENTINEL:

                self._release_running_agent_state(_quick_key)

            else:

                # Agent path already cleaned _running_agents; make sure

                # the paired metadata dicts are gone too.

                self._running_agents_ts.pop(_quick_key, None)

                if hasattr(self, "_busy_ack_ts"):

                    self._busy_ack_ts.pop(_quick_key, None)

    async def _prepare_inbound_message_text(

        self,

        *,

        event: MessageEvent,

        source: SessionSource,

        history: List[Dict[str, Any]],

    ) -> Optional[str]:

        """Prepare inbound event text for the agent.

        Keep the normal inbound path and the queued follow-up path on the same

        preprocessing pipeline so sender attribution, image enrichment, STT,

        document notes, reply context, and @ references all behave the same.

        Side effect: buffers per-session native image paths when the active

        model supports native vision AND the user has images attached. The

        caller consumes and clears that session-scoped buffer at the

        ``run_conversation`` site to build a multimodal user turn. When the

        list is empty, the ``_enrich_message_with_vision`` text path has

        already run and images are represented in-text.

        """

        history = history or []

        message_text = event.text or ""

        _group_sessions_per_user = getattr(self.config, "group_sessions_per_user", True)

        _thread_sessions_per_user = getattr(self.config, "thread_sessions_per_user", False)

        # Use the same helper every other call site uses so the write key here

        # matches the consume key at the run_conversation site — even if the

        # session store overrides build_session_key's default behavior.

        session_key = self._session_key_for_source(source)

        # Reset only this session's per-call buffer; other sessions may be

        # concurrently preparing multimodal turns on the same runner.

        self._consume_pending_native_image_paths(session_key)

        _is_shared_multi_user = is_shared_multi_user_session(

            source,

            group_sessions_per_user=_group_sessions_per_user,

            thread_sessions_per_user=_thread_sessions_per_user,

        )

        if _is_shared_multi_user and source.user_name:

            message_text = f"[{source.user_name}] {message_text}"

        # Prepend channel context from history backfill (if any).  This

        # happens after sender-prefix so the prefix only applies to the

        # trigger message, not the backfill block.

        if getattr(event, "channel_context", None):

            message_text = f"{event.channel_context}\n\n[New message]\n{message_text}"

        # Declare at outer scope so the audio-file-paths handling block below

        # remains safe when ``event.media_urls`` is empty (no inner block runs).

        audio_file_paths: list[str] = []

        if event.media_urls:

            image_paths = []

            audio_paths = []

            for i, path in enumerate(event.media_urls):

                mtype = event.media_types[i] if i < len(event.media_types) else ""

                if mtype.startswith("image/") or event.message_type == MessageType.PHOTO:

                    image_paths.append(path)

                # MessageType.AUDIO = audio file attachment (e.g. .mp3, .m4a) — never STT

                # MessageType.VOICE = voice message (Opus/OGG) — always STT

                if event.message_type == MessageType.AUDIO:

                    audio_file_paths.append(path)

                elif event.message_type == MessageType.VOICE or (

                    mtype.startswith("audio/")

                    and event.message_type not in {MessageType.AUDIO, MessageType.DOCUMENT}

                ):

                    audio_paths.append(path)

            if image_paths:

                # Decide routing: native (attach pixels) vs text (vision_analyze

                # pre-run + prepend description).  See agent/image_routing.py.

                _img_mode = self._decide_image_input_mode()

                if _img_mode == "native":

                    # Defer attachment to the run_conversation call site.

                    pending_native = getattr(self, "_pending_native_image_paths_by_session", None)

                    if pending_native is None:

                        pending_native = {}

                        self._pending_native_image_paths_by_session = pending_native

                    pending_native[session_key] = list(image_paths)

                    logger.info(

                        "Image routing: native (model supports vision). %d image(s) will be attached inline.",

                        len(image_paths),

                    )

                else:

                    logger.info(

                        "Image routing: text (mode=%s). Pre-analyzing %d image(s) via vision_analyze.",

                        _img_mode, len(image_paths),

                    )

                    message_text = await self._enrich_message_with_vision(

                        message_text,

                        image_paths,

                    )

            if audio_paths:

                message_text = await self._enrich_message_with_transcription(

                    message_text,

                    audio_paths,

                )

                _stt_fail_markers = (

                    "No STT provider",

                    "STT is disabled",

                    "can't listen",

                    "VOICE_TOOLS_OPENAI_KEY",

                )

                if any(marker in message_text for marker in _stt_fail_markers):

                    _stt_adapter = self.adapters.get(source.platform)

                    _stt_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))

                    if _stt_adapter:

                        try:

                            _stt_msg = (

                                "🎤 I received your voice message but can't transcribe it — "

                                "no speech-to-text provider is configured.\n\n"

                                "To enable voice: install faster-whisper "

                                "(`uv pip install faster-whisper` in the Intellect venv; "

                                "`pip install faster-whisper` also works if pip is on PATH) "

                                "and set `stt.enabled: true` in config.yaml, "

                                "then /restart the gateway."

                            )

                            if self._has_setup_skill():

                                _stt_msg += "\n\nFor full setup instructions, type: `/skill intellect-agent-setup`"

                            await _stt_adapter.send(

                                source.chat_id,

                                _stt_msg,

                                metadata=_stt_meta,

                            )

                        except Exception:

                            _log_non_critical()

        if audio_file_paths:

            from tools.credential_files import to_agent_visible_cache_path as _to_agent_path

            for _apath in audio_file_paths:

                _basename = os.path.basename(_apath)

                _parts = _basename.split("_", 2)

                _display = _parts[2] if len(_parts) >= 3 else _basename

                _display = re.sub(r'[^\w.\- ]', '_', _display)

                _agent_path = _to_agent_path(_apath)

                _note = (

                    f"[The user sent an audio file attachment: '{_display}'. "

                    f"It is saved at: {_agent_path}. "

                    f"Ask the user what they'd like you to do with it, or pass the path to a transcription or media tool.]"

                )

                message_text = f"{_note}\n\n{message_text}"

        if event.media_urls and event.message_type == MessageType.DOCUMENT:

            import mimetypes as _mimetypes

            from tools.credential_files import to_agent_visible_cache_path

            _TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}

            for i, path in enumerate(event.media_urls):

                mtype = event.media_types[i] if i < len(event.media_types) else ""

                if mtype in {"", "application/octet-stream"}:

                    _ext = os.path.splitext(path)[1].lower()

                    if _ext in _TEXT_EXTENSIONS:

                        mtype = "text/plain"

                    else:

                        guessed, _ = _mimetypes.guess_type(path)

                        if guessed:

                            mtype = guessed

                if not mtype.startswith(("application/", "text/")):

                    continue

                basename = os.path.basename(path)

                parts = basename.split("_", 2)

                display_name = parts[2] if len(parts) >= 3 else basename

                display_name = re.sub(r'[^\w.\- ]', '_', display_name)

                # Translate host cache path to in-container path if running under Docker backend.

                # This ensures the agent receives a path it can open inside its sandbox, as the

                # cache directories are auto-mounted at /root/.intellect/cache/* by get_cache_directory_mounts().

                agent_path = to_agent_visible_cache_path(path)

                if mtype.startswith("text/"):

                    context_note = (

                        f"[The user sent a text document: '{display_name}'. "

                        f"Its content has been included below. "

                        f"The file is also saved at: {agent_path}]"

                    )

                else:

                    context_note = (

                        f"[The user sent a document: '{display_name}'. "

                        f"The file is saved at: {agent_path}. "

                        f"Ask the user what they'd like you to do with it.]"

                    )

                message_text = f"{context_note}\n\n{message_text}"

        if getattr(event, "reply_to_text", None) and event.reply_to_message_id:

            # Always inject the reply-to pointer — even when the quoted text

            # already appears in history. The prefix isn't deduplication, it's

            # disambiguation: it tells the agent *which* prior message the user

            # is referencing. History can contain the same or similar text

            # multiple times, and without an explicit pointer the agent has to

            # guess (or answer for both subjects). Token overhead is minimal.

            reply_snippet = event.reply_to_text[:500]

            message_text = f'[Replying to: "{reply_snippet}"]\n\n{message_text}'

        if "@" in message_text:

            try:

                from agent.context_references import preprocess_context_references_async

                from agent.model_metadata import get_model_context_length

                _msg_cwd = os.environ.get("TERMINAL_CWD", os.path.expanduser("~"))

                _msg_runtime = _resolve_runtime_agent_kwargs()

                _msg_config_ctx = None

                try:

                    _msg_cfg = _load_gateway_config()

                    _msg_model_cfg = _msg_cfg.get("model", {})

                    if isinstance(_msg_model_cfg, dict):

                        _msg_raw_ctx = _msg_model_cfg.get("context_length")

                        if _msg_raw_ctx is not None:

                            _msg_config_ctx = int(_msg_raw_ctx)

                except Exception:

                    _log_non_critical()

                _msg_ctx_len = get_model_context_length(

                    self._model,

                    base_url=self._base_url or _msg_runtime.get("base_url") or "",

                    api_key=_msg_runtime.get("api_key") or "",

                    config_context_length=_msg_config_ctx,

                )

                _ctx_result = await preprocess_context_references_async(

                    message_text,

                    cwd=_msg_cwd,

                    context_length=_msg_ctx_len,

                    allowed_root=_msg_cwd,

                )

                if _ctx_result.blocked:

                    _adapter = self.adapters.get(source.platform)

                    if _adapter:

                        await _adapter.send(

                            source.chat_id,

                            "\n".join(_ctx_result.warnings) or "Context injection refused.",

                        )

                    return None

                if _ctx_result.expanded:

                    message_text = _ctx_result.message

            except Exception as exc:

                logger.debug("@ context reference expansion failed: %s", exc)

        return message_text

    async def _handle_message_with_agent(self, event, source, _quick_key: str, run_generation: int):

        """Inner handler that runs under the _running_agents sentinel guard."""

        _msg_start_time = time.time()

        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)

        _msg_preview = (event.text or "")[:80].replace("\n", " ")

        logger.info(

            "inbound message: platform=%s user=%s chat=%s msg=%r",

            _platform_name, source.user_name or source.user_id or "unknown",

            source.chat_id or "unknown", _msg_preview,

        )

        # Get or create session

        # Topic-mode DMs: rewrite a stale/foreign thread_id to the user's

        # last-active topic so a cross-topic Reply or stripped plain reply

        # doesn't fragment the conversation across sessions.

        recovered = self._recover_telegram_topic_thread_id(source)

        if recovered is not None:

            logger.info(

                "telegram topic recovery: chat=%s user=%s %r -> %s",

                source.chat_id, source.user_id, source.thread_id, recovered,

            )

            source = dataclasses.replace(source, thread_id=recovered)

            try:

                event.source = source

            except Exception:

                _log_non_critical()

        session_entry = self.session_store.get_or_create_session(source)

        session_key = session_entry.session_key

        self._cache_session_source(session_key, source)

        if self._is_telegram_topic_lane(source):

            try:

                binding = self._session_db.get_telegram_topic_binding(

                    chat_id=str(source.chat_id),

                    thread_id=str(source.thread_id),

                ) if self._session_db else None

            except Exception:

                logger.debug("Failed to read Telegram topic binding", exc_info=True)

                binding = None

            if binding:

                bound_session_id = str(binding.get("session_id") or "")

                # Heal bindings that point at a pre-compression parent: walk

                # the compression-continuation chain forward to its tip so the

                # next message resumes the compressed child instead of

                # reloading the oversized parent transcript (#20470/#29712/

                # #33414). Returns the input unchanged when the session isn't

                # a compression parent, so this is cheap and safe.

                if bound_session_id and self._session_db is not None:

                    try:

                        canonical_session_id = self._session_db.get_compression_tip(

                            bound_session_id,

                        )

                    except Exception:

                        logger.debug(

                            "compression-tip lookup failed for %s",

                            bound_session_id, exc_info=True,

                        )

                        canonical_session_id = bound_session_id

                    if (

                        canonical_session_id

                        and canonical_session_id != bound_session_id

                    ):

                        bound_session_id = canonical_session_id

                if bound_session_id and bound_session_id != session_entry.session_id:

                    # Route the override through SessionStore so the session_key

                    # → session_id mapping is persisted to disk and the previous

                    # lane session is ended cleanly. Mutating session_entry in

                    # place here created a split-brain state where the JSON

                    # index pointed at one id but code downstream used another.

                    switched = self.session_store.switch_session(session_key, bound_session_id)

                    if switched is not None:

                        session_entry = switched

                # If the stored binding pointed at a parent, rewrite it to the

                # canonical descendant now that we've followed the chain.

                if (

                    bound_session_id

                    and bound_session_id != str(binding.get("session_id") or "")

                ):

                    self._sync_telegram_topic_binding(

                        source, session_entry, reason="compression-tip-walk",

                    )

            else:

                try:

                    self._record_telegram_topic_binding(source, session_entry)

                except Exception:

                    logger.debug("Failed to record Telegram topic binding", exc_info=True)

        if getattr(session_entry, "was_auto_reset", False):

            # Treat auto-reset as a full conversation boundary — drop every

            # session-scoped transient state so the fresh session does not

            # inherit the previous conversation's model/reasoning overrides

            # or a queued "/model switched" note.

            self._session_model_overrides.pop(session_key, None)

            self._set_session_reasoning_override(session_key, None)

            if hasattr(self, "_pending_model_notes"):

                self._pending_model_notes.pop(session_key, None)

        # Emit session:start for new or auto-reset sessions

        _is_new_session = (

            session_entry.created_at == session_entry.updated_at

            or getattr(session_entry, "was_auto_reset", False)

            or getattr(session_entry, "is_fresh_reset", False)

        )

        # Consume the is_fresh_reset flag immediately so it doesn't leak

        # onto subsequent messages in the same session (issue #6508).

        if getattr(session_entry, "is_fresh_reset", False):

            session_entry.is_fresh_reset = False

        if _is_new_session:

            await self.hooks.emit("session:start", {

                "platform": source.platform.value if source.platform else "",

                "user_id": source.user_id,

                "session_id": session_entry.session_id,

                "session_key": session_key,

            })

        # Build session context

        context = build_session_context(source, self.config, session_entry)

        # Set session context variables for tools (task-local, concurrency-safe)

        _session_env_tokens = self._set_session_env(context)

        # Read privacy.redact_pii from config (re-read per message)

        _redact_pii = False

        try:

            _pcfg = _load_gateway_config()

            _redact_pii = bool((_pcfg.get("privacy") or {}).get("redact_pii", False))

        except Exception:

            _log_non_critical()

        # Build the context prompt to inject

        context_prompt = build_session_context_prompt(context, redact_pii=_redact_pii)

        # If the previous session expired and was auto-reset, prepend a notice

        # so the agent knows this is a fresh conversation (not an intentional /reset).

        if getattr(session_entry, 'was_auto_reset', False):

            reset_reason = getattr(session_entry, 'auto_reset_reason', None) or 'idle'

            if reset_reason == "suspended":

                context_note = "[System note: The user's previous session was stopped and suspended. This is a fresh conversation with no prior context.]"

            elif reset_reason == "daily":

                context_note = "[System note: The user's session was automatically reset by the daily schedule. This is a fresh conversation with no prior context.]"

            else:

                context_note = "[System note: The user's previous session expired due to inactivity. This is a fresh conversation with no prior context.]"

            context_prompt = context_note + "\n\n" + context_prompt

            # Send a user-facing notification explaining the reset, unless:

            # - notifications are disabled in config

            # - the platform is excluded (e.g. api_server, webhook)

            # - the expired session had no activity (nothing was cleared)

            try:

                policy = self.session_store.config.get_reset_policy(

                    platform=source.platform,

                    session_type=getattr(source, 'chat_type', 'dm'),

                )

                platform_name = source.platform.value if source.platform else ""

                had_activity = getattr(session_entry, 'reset_had_activity', False)

                # Suspended sessions always notify (they were explicitly stopped

                # or crashed mid-operation) — skip the policy check.

                should_notify = reset_reason == "suspended" or (

                    policy.notify

                    and had_activity

                    and platform_name not in policy.notify_exclude_platforms

                )

                if should_notify:

                    adapter = self.adapters.get(source.platform)

                    if adapter:

                        if reset_reason == "suspended":

                            reason_text = "previous session was stopped or interrupted"

                        elif reset_reason == "daily":

                            reason_text = f"daily schedule at {policy.at_hour}:00"

                        else:

                            hours = policy.idle_minutes // 60

                            mins = policy.idle_minutes % 60

                            duration = f"{hours}h" if not mins else f"{hours}h {mins}m" if hours else f"{mins}m"

                            reason_text = f"inactive for {duration}"

                        notice = (

                            f"◐ Session automatically reset ({reason_text}). "

                            f"Conversation history cleared.\n"

                            f"Use /resume to browse and restore a previous session.\n"

                            f"Adjust reset timing in config.yaml under session_reset."

                        )

                        try:

                            session_info = self._format_session_info()

                            if session_info:

                                notice = f"{notice}\n\n{session_info}"

                        except Exception:

                            _log_non_critical()

                        await adapter.send(

                            source.chat_id, notice,

                            metadata=self._thread_metadata_for_source(source),

                        )

            except Exception as e:

                logger.debug("Auto-reset notification failed (non-fatal): %s", e)

            session_entry.was_auto_reset = False

            session_entry.auto_reset_reason = None

        # Auto-load skill(s) for topic/channel bindings (Telegram DM Topics,

        # Discord channel_skill_bindings).  Supports a single name or ordered list.

        # Only inject on NEW sessions — ongoing conversations already have the

        # skill content in their conversation history from the first message.

        _auto = getattr(event, "auto_skill", None)

        if _is_new_session and _auto:

            _skill_names = [_auto] if isinstance(_auto, str) else list(_auto)

            try:

                from agent.skill_commands import _load_skill_payload, _build_skill_message

                _combined_parts: list[str] = []

                _loaded_names: list[str] = []

                for _sname in _skill_names:

                    _loaded = _load_skill_payload(_sname, task_id=_quick_key)

                    if _loaded:

                        _loaded_skill, _skill_dir, _display_name = _loaded

                        _note = (

                            f'[IMPORTANT: The "{_display_name}" skill is auto-loaded. '

                            f"Follow its instructions for this session.]"

                        )

                        _part = _build_skill_message(_loaded_skill, _skill_dir, _note)

                        if _part:

                            _combined_parts.append(_part)

                            _loaded_names.append(_sname)

                    else:

                        logger.warning("[Gateway] Auto-skill '%s' not found", _sname)

                if _combined_parts:

                    # Append the user's original text after all skill payloads

                    _combined_parts.append(event.text)

                    event.text = "\n\n".join(_combined_parts)

                    logger.info(

                        "[Gateway] Auto-loaded skill(s) %s for session %s",

                        _loaded_names, session_key,

                    )

            except Exception as e:

                logger.warning("[Gateway] Failed to auto-load skill(s) %s: %s", _skill_names, e)

        # Load conversation history from transcript

        history = self.session_store.load_transcript(session_entry.session_id)

        # -----------------------------------------------------------------

        # Session hygiene: auto-compress pathologically large transcripts

        #

        # Long-lived gateway sessions can accumulate enough history that

        # every new message rehydrates an oversized transcript, causing

        # repeated truncation/context failures.  Detect this early and

        # compress proactively — before the agent even starts.  (#628)

        #

        # Token source priority:

        # 1. Actual API-reported prompt_tokens from the last turn

        #    (stored in session_entry.last_prompt_tokens)

        # 2. Rough char-based estimate (str(msg)//4). Overestimates

        #    by 30-50% on code/JSON-heavy sessions, but that just

        #    means hygiene fires a bit early — safe and harmless.

        # -----------------------------------------------------------------

        if history and len(history) >= 4:

            from agent.model_metadata import (

                estimate_messages_tokens_rough,

                get_model_context_length,

            )

            # Read model + compression config from config.yaml.

            # NOTE: hygiene threshold is intentionally HIGHER than the agent's

            # own compressor (0.85 vs 0.50).  Hygiene is a safety net for

            # sessions that grew too large between turns — it fires pre-agent

            # to prevent API failures.  The agent's own compressor handles

            # normal context management during its tool loop with accurate

            # real token counts.  Having hygiene at 0.50 caused premature

            # compression on every turn in long gateway sessions.

            _hyg_model = "anthropic/claude-sonnet-4.6"

            _hyg_threshold_pct = 0.85

            _hyg_compression_enabled = True

            _hyg_hard_msg_limit = 400

            _hyg_config_context_length = None

            _hyg_provider = None

            _hyg_base_url = None

            _hyg_api_key = None

            _hyg_data = {}

            try:

                _hyg_data = _load_gateway_config()

                if _hyg_data:

                    # Resolve model name (same logic as run_sync)

                    _model_cfg = _hyg_data.get("model", {})

                    if isinstance(_model_cfg, str):

                        _hyg_model = _model_cfg

                    elif isinstance(_model_cfg, dict):

                        _hyg_model = _model_cfg.get("default") or _model_cfg.get("model") or _hyg_model

                        # Read explicit context_length override from model config

                        # (same as run_agent.py lines 995-1005)

                        _raw_ctx = _model_cfg.get("context_length")

                        if _raw_ctx is not None:

                            try:

                                _hyg_config_context_length = int(_raw_ctx)

                            except (TypeError, ValueError):

                                pass

                        # Read provider for accurate context detection

                        _hyg_provider = _model_cfg.get("provider") or None

                        _hyg_base_url = _model_cfg.get("base_url") or None

                    # Read compression settings — only use enabled flag.

                    # The threshold is intentionally separate from the agent's

                    # compression.threshold (hygiene runs higher).

                    _comp_cfg = _hyg_data.get("compression", {})

                    if isinstance(_comp_cfg, dict):

                        _hyg_compression_enabled = str(

                            _comp_cfg.get("enabled", True)

                        ).lower() in {"true", "1", "yes"}

                        _raw_hard_limit = _comp_cfg.get("hygiene_hard_message_limit")

                        if _raw_hard_limit is not None:

                            try:

                                _parsed = int(_raw_hard_limit)

                                if _parsed > 0:

                                    _hyg_hard_msg_limit = _parsed

                            except (TypeError, ValueError):

                                pass

                try:

                    _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(

                        source=source,

                        session_key=session_key,

                        user_config=_hyg_data if isinstance(_hyg_data, dict) else None,

                    )

                    _hyg_provider = _hyg_runtime.get("provider") or _hyg_provider

                    _hyg_base_url = _hyg_runtime.get("base_url") or _hyg_base_url

                    _hyg_api_key = _hyg_runtime.get("api_key") or _hyg_api_key

                except Exception:

                    _log_non_critical()

                # Check custom_providers per-model context_length

                # (same fallback as run_agent.py lines 1171-1189).

                # Must run after runtime resolution so _hyg_base_url is set.

                if _hyg_config_context_length is None and _hyg_base_url:

                    try:

                        try:

                            from intellect_cli.config import get_compatible_custom_providers as _gw_gcp

                            _hyg_custom_providers = _gw_gcp(_hyg_data)

                        except Exception:

                            _hyg_custom_providers = _hyg_data.get("custom_providers")

                            if not isinstance(_hyg_custom_providers, list):

                                _hyg_custom_providers = []

                        for _cp in _hyg_custom_providers:

                            if not isinstance(_cp, dict):

                                continue

                            _cp_url = (_cp.get("base_url") or "").rstrip("/")

                            if _cp_url and _cp_url == _hyg_base_url.rstrip("/"):

                                _cp_models = _cp.get("models", {})

                                if isinstance(_cp_models, dict):

                                    _cp_model_cfg = _cp_models.get(_hyg_model, {})

                                    if isinstance(_cp_model_cfg, dict):

                                        _cp_ctx = _cp_model_cfg.get("context_length")

                                        if _cp_ctx is not None:

                                            _hyg_config_context_length = int(_cp_ctx)

                                break

                    except (TypeError, ValueError):

                        pass

            except Exception:

                _log_non_critical()

            if _hyg_compression_enabled:

                _hyg_context_length = get_model_context_length(

                    _hyg_model,

                    base_url=_hyg_base_url or "",

                    api_key=_hyg_api_key or "",

                    config_context_length=_hyg_config_context_length,

                    provider=_hyg_provider or "",

                )

                _compress_token_threshold = int(

                    _hyg_context_length * _hyg_threshold_pct

                )

                _warn_token_threshold = int(_hyg_context_length * 0.95)

                _msg_count = len(history)

                # Prefer actual API-reported tokens from the last turn

                # (stored in session entry) over the rough char-based estimate.

                _stored_tokens = session_entry.last_prompt_tokens

                if _stored_tokens > 0:

                    _approx_tokens = _stored_tokens

                    _token_source = "actual"

                else:

                    _approx_tokens = estimate_messages_tokens_rough(history)

                    _token_source = "estimated"

                    # Note: rough estimates overestimate by 30-50% for code/JSON-heavy

                    # sessions, but that just means hygiene fires a bit early — which

                    # is safe and harmless.  The 85% threshold already provides ample

                    # headroom (agent's own compressor runs at 50%).  A previous 1.4x

                    # multiplier tried to compensate by inflating the threshold, but

                    # 85% * 1.4 = 119% of context — which exceeds the model's limit

                    # and prevented hygiene from ever firing for ~200K models (GLM-5).

                # Hard safety valve: force compression if message count is

                # extreme, regardless of token estimates.  This breaks the

                # death spiral where API disconnects prevent token data

                # collection, which prevents compression, which causes more

                # disconnects.  400 messages is well above normal sessions

                # but catches runaway growth before it becomes unrecoverable.

                # Threshold is configurable via

                # compression.hygiene_hard_message_limit.

                # (#2153)

                _HARD_MSG_LIMIT = _hyg_hard_msg_limit

                _needs_compress = (

                    _approx_tokens >= _compress_token_threshold

                    or _msg_count >= _HARD_MSG_LIMIT

                )

                if _needs_compress:

                    logger.info(

                        "Session hygiene: %s messages, ~%s tokens (%s) — auto-compressing "

                        "(threshold: %s%% of %s = %s tokens)",

                        _msg_count, f"{_approx_tokens:,}", _token_source,

                        int(_hyg_threshold_pct * 100),

                        f"{_hyg_context_length:,}",

                        f"{_compress_token_threshold:,}",

                    )

                    _hyg_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))

                    try:

                        from run_agent import AIAgent

                        _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(

                            source=source,

                            session_key=session_key,

                            user_config=_hyg_data if isinstance(_hyg_data, dict) else None,

                        )

                        if _hyg_runtime.get("api_key"):

                            _hyg_msgs = [

                                {"role": m.get("role"), "content": m.get("content")}

                                for m in history

                                if m.get("role") in {"user", "assistant"}

                                and m.get("content")

                            ]

                            if len(_hyg_msgs) >= 4:

                                _hyg_agent = AIAgent(

                                    **_hyg_runtime,

                                    model=_hyg_model,

                                    max_iterations=4,

                                    quiet_mode=True,

                                    skip_memory=True,

                                    enabled_toolsets=["memory"],

                                    session_id=session_entry.session_id,

                                )

                                try:

                                    _hyg_agent._print_fn = lambda *a, **kw: None

                                    loop = asyncio.get_running_loop()

                                    _compressed, _ = await loop.run_in_executor(

                                        None,

                                        lambda: _hyg_agent._compress_context(

                                            _hyg_msgs, "",

                                            approx_tokens=_approx_tokens,

                                        ),

                                    )

                                    # _compress_context ends the old session and creates

                                    # a new session_id.  Write compressed messages into

                                    # the NEW session so the old transcript stays intact

                                    # and searchable via session_search.

                                    _hyg_new_sid = _hyg_agent.session_id

                                    if _hyg_new_sid != session_entry.session_id:

                                        session_entry.session_id = _hyg_new_sid

                                        self.session_store._save()

                                        self._sync_telegram_topic_binding(

                                            source, session_entry,

                                            reason="hygiene-compression",

                                        )

                                    self.session_store.rewrite_transcript(

                                        session_entry.session_id, _compressed

                                    )

                                    # Reset stored token count — transcript was rewritten

                                    session_entry.last_prompt_tokens = 0

                                    history = _compressed

                                    _new_count = len(_compressed)

                                    _new_tokens = estimate_messages_tokens_rough(

                                        _compressed

                                    )

                                    logger.info(

                                        "Session hygiene: compressed %s → %s msgs, "

                                        "~%s → ~%s tokens",

                                        _msg_count, _new_count,

                                        f"{_approx_tokens:,}", f"{_new_tokens:,}",

                                    )

                                    if _new_tokens >= _warn_token_threshold:

                                        logger.warning(

                                            "Session hygiene: still ~%s tokens after "

                                            "compression",

                                            f"{_new_tokens:,}",

                                        )

                                    # If summary generation failed, the

                                    # compressor aborts entirely and returns

                                    # messages unchanged — nothing is dropped.

                                    # Surface a visible warning to the gateway

                                    # user — agent.log alone is invisible on

                                    # TG/Discord/etc. — so they know the chat

                                    # is "frozen" at the current size and can

                                    # /compress to retry or /reset to start

                                    # fresh.

                                    _comp = getattr(_hyg_agent, "context_compressor", None)

                                    if _comp is not None and getattr(_comp, "_last_compress_aborted", False):

                                        _err = getattr(_comp, "_last_summary_error", None) or "unknown error"

                                        _warn_msg = (

                                            "⚠️ Context compression aborted "

                                            f"({_err}). No messages were dropped — "

                                            "conversation is unchanged. Run /compress "

                                            "to retry, /reset for a clean session, or "

                                            "check your auxiliary.compression model "

                                            "configuration."

                                        )

                                        try:

                                            _adapter = self.adapters.get(source.platform)

                                            if _adapter and source.chat_id:

                                                await _adapter.send(source.chat_id, _warn_msg, metadata=_hyg_meta)

                                        except Exception as _werr:

                                            logger.warning(

                                                "Failed to deliver compression-failure warning to user: %s",

                                                _werr,

                                            )

                                    # Separately: if the user's CONFIGURED aux

                                    # model failed and we recovered by falling

                                    # back to the main model, tell them — a

                                    # misconfigured auxiliary.compression.model

                                    # is something only they can fix, and

                                    # silent recovery would hide it.

                                    elif _comp is not None and getattr(_comp, "_last_aux_model_failure_model", None):

                                        _aux_model = getattr(_comp, "_last_aux_model_failure_model", "")

                                        _aux_err = getattr(_comp, "_last_aux_model_failure_error", None) or "unknown error"

                                        _aux_msg = (

                                            f"ℹ️ Configured compression model `{_aux_model}` "

                                            f"failed ({_aux_err}). Recovered using your main "

                                            "model — context is intact — but you may want to "

                                            "check `auxiliary.compression.model` in config.yaml."

                                        )

                                        try:

                                            _adapter = self.adapters.get(source.platform)

                                            if _adapter and source.chat_id:

                                                await _adapter.send(source.chat_id, _aux_msg, metadata=_hyg_meta)

                                        except Exception as _werr:

                                            logger.warning(

                                                "Failed to deliver aux-model-fallback notice to user: %s",

                                                _werr,

                                            )

                                finally:

                                    # Evict the cached agent so the next turn

                                    # rebuilds its system prompt from current

                                    # SOUL.md, memory, and skills.

                                    self._evict_cached_agent(session_key)

                                    self._cleanup_agent_resources(_hyg_agent)

                    except Exception as e:

                        logger.warning(

                            "Session hygiene auto-compress failed: %s", e

                        )

        # First-message onboarding -- only on the very first interaction ever

        if not history and not self.session_store.has_any_sessions():

            context_prompt += (

                "\n\n[System note: This is the user's very first message ever. "

                "Briefly introduce yourself and mention that /help shows available commands. "

                "Keep the introduction concise -- one or two sentences max.]"

            )

        # One-time prompt if no home channel is set for this platform

        # Skip for webhooks - they deliver directly to configured targets (github_comment, etc.)

        if not history and source.platform and source.platform != Platform.LOCAL and source.platform != Platform.WEBHOOK:

            platform_name = source.platform.value

            env_key = _home_target_env_var(platform_name)

            if not os.getenv(env_key):

                # Slack dispatches all Intellect commands through a single

                # parent slash command `/intellect`; bare `/sethome` is not

                # registered and would fail with "app did not respond".

                sethome_cmd = (

                    "/intellect sethome"

                    if source.platform == Platform.SLACK

                    else "/sethome"

                )

                notice = (

                    f"📬 No home channel is set for {platform_name.title()}. "

                    f"A home channel is where Intellect delivers cron job results "

                    f"and cross-platform messages.\n\n"

                    f"Type {sethome_cmd} to make this chat your home channel, "

                    f"or ignore to skip."

                )

                await self._deliver_platform_notice(source, notice)

        # -----------------------------------------------------------------

        # Voice channel awareness — inject current voice channel state

        # into context so the agent knows who is in the channel and who

        # is speaking, without needing a separate tool call.

        # -----------------------------------------------------------------

        if source.platform == Platform.DISCORD:

            adapter = self.adapters.get(Platform.DISCORD)

            guild_id = self._get_guild_id(event)

            if guild_id and adapter and hasattr(adapter, "get_voice_channel_context"):

                vc_context = adapter.get_voice_channel_context(guild_id)

                if vc_context:

                    context_prompt += f"\n\n{vc_context}"

        # -----------------------------------------------------------------

        # Auto-analyze images sent by the user

        #

        # If the user attached image(s), we run the vision tool eagerly so

        # the conversation model always receives a text description.  The

        # local file path is also included so the model can re-examine the

        # image later with a more targeted question via vision_analyze.

        #

        # We filter to image paths only (by media_type) so that non-image

        # attachments (documents, audio, etc.) are not sent to the vision

        # tool even when they appear in the same message.

        # -----------------------------------------------------------------

        message_text = await self._prepare_inbound_message_text(

            event=event,

            source=source,

            history=history,

        )

        if message_text is None:

            return

        # Bind this gateway run generation to the adapter's active-session

        # event so deferred post-delivery callbacks can be released by the

        # same run that registered them.

        self._bind_adapter_run_generation(

            self.adapters.get(source.platform),

            session_key,

            run_generation,

        )

        try:

            # Emit agent:start hook

            hook_ctx = {

                "platform": source.platform.value if source.platform else "",

                "user_id": source.user_id,

                "chat_id": source.chat_id or "",

                "session_id": session_entry.session_id,

                "message": message_text[:500],

            }

            await self.hooks.emit("agent:start", hook_ctx)

            # Run the agent

            agent_result = await self._run_agent(

                message=message_text,

                context_prompt=context_prompt,

                history=history,

                source=source,

                session_id=session_entry.session_id,

                session_key=session_key,

                run_generation=run_generation,

                event_message_id=self._reply_anchor_for_event(event),

                channel_prompt=event.channel_prompt,

            )

            # Stop persistent typing indicator now that the agent is done

            try:

                _typing_adapter = self.adapters.get(source.platform)

                if _typing_adapter and hasattr(_typing_adapter, "stop_typing"):

                    await _typing_adapter.stop_typing(source.chat_id)

            except Exception:

                _log_non_critical()

            if not self._is_session_run_current(_quick_key, run_generation):

                logger.info(

                    "Discarding stale agent result for %s — generation %d is no longer current",

                    _quick_key or "?",

                    run_generation,

                )

                _stale_adapter = self.adapters.get(source.platform)

                if getattr(type(_stale_adapter), "pop_post_delivery_callback", None) is not None:

                    _stale_adapter.pop_post_delivery_callback(

                        _quick_key,

                        generation=run_generation,

                    )

                elif _stale_adapter and hasattr(_stale_adapter, "_post_delivery_callbacks"):

                    _stale_adapter._post_delivery_callbacks.pop(_quick_key, None)

                return None

            response = agent_result.get("final_response") or ""

            # Convert the agent's internal "(empty)" sentinel into a

            # user-friendly message.  "(empty)" means the model failed to

            # produce visible content after exhausting all retries (nudge,

            # prefill, empty-retry, fallback).  Sending the raw sentinel

            # looks like a bug; a short explanation is more helpful.

            if response == "(empty)":

                response = (

                    "⚠️ The model returned no response after processing tool "

                    "results. This can happen with some models — try again or "

                    "rephrase your question."

                )

            agent_messages = agent_result.get("messages", [])

            _response_time = time.time() - _msg_start_time

            _api_calls = agent_result.get("api_calls", 0)

            _resp_len = len(response)

            logger.info(

                "response ready: platform=%s chat=%s time=%.1fs api_calls=%d response=%d chars",

                _platform_name, source.chat_id or "unknown",

                _response_time, _api_calls, _resp_len,

            )

            # Successful turn — clear any stuck-loop counter for this session.

            # This ensures the counter only accumulates across CONSECUTIVE

            # restarts where the session was active (never completed).

            #

            # Also clear the resume_pending flag (set by drain-timeout

            # shutdown) — the turn ran to completion, so recovery

            # succeeded and subsequent messages should no longer receive

            # the restart-interruption system note.

            if session_key and _should_clear_resume_pending_after_turn(agent_result):

                self._clear_restart_failure_count(session_key)

                try:

                    self.session_store.clear_resume_pending(session_key)

                except Exception as _e:

                    logger.debug(

                        "clear_resume_pending failed for %s: %s",

                        session_key, _e,

                    )

            # Normalize empty responses: surface errors, partial failures, and

            # the case where agent did work but returned no text. Fix for #18765.

            response = _normalize_empty_agent_response(

                agent_result, response, history_len=len(history),

            )

            response = _sanitize_gateway_final_response(source.platform, response)

            # If the agent's session_id changed during compression, update

            # session_entry so transcript writes below go to the right session.

            if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:

                session_entry.session_id = agent_result["session_id"]

                self.session_store._save()

                self._sync_telegram_topic_binding(

                    source, session_entry, reason="agent-result-compression",

                )

            # Prepend reasoning/thinking if display is enabled (per-platform)

            try:

                from gateway.display_config import resolve_display_setting as _rds

                _show_reasoning_effective = _rds(

                    _load_gateway_config(),

                    _platform_config_key(source.platform),

                    "show_reasoning",

                    getattr(self, "_show_reasoning", False),

                )

            except Exception:

                _show_reasoning_effective = getattr(self, "_show_reasoning", False)

            if _show_reasoning_effective and response:

                last_reasoning = agent_result.get("last_reasoning")

                if last_reasoning:

                    # Collapse long reasoning to keep messages readable

                    lines = last_reasoning.strip().splitlines()

                    if len(lines) > 15:

                        display_reasoning = "\n".join(lines[:15])

                        display_reasoning += f"\n_... ({len(lines) - 15} more lines)_"

                    else:

                        display_reasoning = last_reasoning.strip()

                    response = f"💭 **Reasoning:**\n```\n{display_reasoning}\n```\n\n{response}"

            # Runtime-metadata footer — only on the FINAL message of the turn.

            # Off by default (display.runtime_footer.enabled=false).  When

            # streaming already delivered the body, we can't mutate the sent

            # text, so we fire a separate trailing send below.

            _footer_line = ""

            try:

                from gateway.runtime_footer import build_footer_line as _bfl

                _footer_line = _bfl(

                    user_config=_load_gateway_config(),

                    platform_key=_platform_config_key(source.platform),

                    model=agent_result.get("model"),

                    context_tokens=agent_result.get("last_prompt_tokens", 0) or 0,

                    context_length=agent_result.get("context_length") or None,

                    cwd=os.environ.get("TERMINAL_CWD", ""),

                )

            except Exception as _footer_err:

                logger.debug("runtime_footer build failed: %s", _footer_err)

                _footer_line = ""

            if _footer_line and response and not agent_result.get("already_sent"):

                response = f"{response}\n\n{_footer_line}"

            # Emit agent:end hook

            await self.hooks.emit("agent:end", {

                **hook_ctx,

                "response": (response or "")[:500],

            })

            # Check for pending process watchers (check_interval on background processes)

            try:

                from tools.process_registry import process_registry

                # Detach the current batch atomically (see crash-recovery drain

                # above): reassign to a fresh list so a watcher appended by a

                # concurrent session during the yield isn't dropped by clear().

                watchers = process_registry.pending_watchers

                process_registry.pending_watchers = []

                for i, watcher in enumerate(watchers):

                    asyncio.create_task(self._run_process_watcher(watcher))

                    if i % 100 == 99:

                        await asyncio.sleep(0)

            except Exception as e:

                logger.error("Process watcher setup error: %s", e)

            # Drain watch pattern notifications that arrived during the agent run.

            # Watch events and completions share the same queue; completions are

            # already handled by the per-process watcher task above, so we only

            # inject watch-type events here.

            try:

                from tools.process_registry import process_registry as _pr

                _watch_events = []

                while not _pr.completion_queue.empty():

                    evt = _pr.completion_queue.get_nowait()

                    evt_type = evt.get("type", "completion")

                    if evt_type in {"watch_match", "watch_disabled"}:

                        _watch_events.append(evt)

                    # else: completion events are handled by the watcher task

                for evt in _watch_events:

                    synth_text = _format_gateway_process_notification(evt)

                    if synth_text:

                        try:

                            await self._inject_watch_notification(synth_text, evt)

                        except Exception as e2:

                            logger.error("Watch notification injection error: %s", e2)

            except Exception as e:

                logger.debug("Watch queue drain error: %s", e)

            # NOTE: Dangerous command approvals are now handled inline by the

            # blocking gateway approval mechanism in tools/approval.py.  The agent

            # thread blocks until the user responds with /approve or /deny, so by

            # the time we reach here the approval has already been resolved.  The

            # old post-loop pop_pending + approval_hint code was removed in favour

            # of the blocking approach that mirrors CLI's synchronous input().

            # Save the full conversation to the transcript, including tool calls.

            # This preserves the complete agent loop (tool_calls, tool results,

            # intermediate reasoning) so sessions can be resumed with full context

            # and transcripts are useful for debugging and training data.

            #

            # IMPORTANT: For context-overflow failures (compression exhausted,

            # generic 400 on large sessions) we must NOT persist the user's

            # message — doing so would grow the session further and cause the

            # same failure on the next attempt, an infinite loop. (#1630, #9893)

            #

            # Transient failures (429, timeout, connection error, provider 5xx)

            # are different: the session is not oversized, and silently dropping

            # the user message causes severe context loss on retry — the agent

            # forgets what was just asked.  Persist the user turn so the

            # conversation is preserved. (#7100)

            agent_failed_early = bool(agent_result.get("failed"))

            _err_str_for_classify = str(agent_result.get("error", "")).lower()

            # Use specific multi-word phrases (not bare "exceed" or "token")

            # to avoid false positives on transient errors like "rate limit

            # exceeded" or "invalid auth token". Matches run_agent.py's

            # own context-length classifier.

            is_context_overflow_failure = agent_failed_early and (

                bool(agent_result.get("compression_exhausted"))

                or any(p in _err_str_for_classify for p in (

                    "context length", "context size", "context window",

                    "maximum context", "token limit", "too many tokens",

                    "reduce the length", "exceeds the limit",

                    "request entity too large", "prompt is too long",

                    "payload too large", "input is too long",

                ))

                or ("400" in _err_str_for_classify and len(history) > 50)

            )

            if is_context_overflow_failure:

                logger.info(

                    "Skipping transcript persistence for context-overflow "

                    "failure in session %s to prevent session growth loop.",

                    session_entry.session_id,

                )

            elif agent_failed_early:

                logger.info(

                    "Transient agent failure in session %s — persisting user "

                    "message so conversation context is preserved on retry.",

                    session_entry.session_id,

                )

            # When compression is exhausted, the session is permanently too

            # large to process.  Auto-reset it so the next message starts

            # fresh instead of replaying the same oversized context in an

            # infinite fail loop.  (#9893)

            if agent_result.get("compression_exhausted") and session_entry and session_key:

                logger.info(

                    "Auto-resetting session %s after compression exhaustion.",

                    session_entry.session_id,

                )

                self.session_store.reset_session(session_key)

                self._evict_cached_agent(session_key)

                self._session_model_overrides.pop(session_key, None)

                self._set_session_reasoning_override(session_key, None)

                if hasattr(self, "_pending_model_notes"):

                    self._pending_model_notes.pop(session_key, None)

                response = (response or "") + (

                    "\n\n🔄 Session auto-reset — the conversation exceeded the "

                    "maximum context size and could not be compressed further. "

                    "Your next message will start a fresh session."

                )

            ts = datetime.now().isoformat()

            # If this is a fresh session (no history), write the full tool

            # definitions as the first entry so the transcript is self-describing

            # -- the same list of dicts sent as tools=[...] in the API request.

            if is_context_overflow_failure:

                pass  # Skip all transcript writes — don't grow a broken session

            elif not history:

                tool_defs = agent_result.get("tools", [])

                self.session_store.append_to_transcript(

                    session_entry.session_id,

                    {

                        "role": "session_meta",

                        "tools": tool_defs or [],

                        "model": _resolve_gateway_model(),

                        "platform": source.platform.value if source.platform else "",

                        "timestamp": ts,

                    }

                )

            # Find only the NEW messages from this turn (skip history we loaded).

            # Use the filtered history length (history_offset) that was actually

            # passed to the agent, not len(history) which includes session_meta

            # entries that were stripped before the agent saw them.

            if is_context_overflow_failure:

                pass  # handled above — skip all transcript writes

            elif agent_failed_early:

                # Transient failure (429/timeout/5xx): persist only the user

                # message so the next message can load a transcript that

                # reflects what was said.  Skip the assistant error text since

                # it's a gateway-generated hint, not model output. (#7100)

                _user_entry = {"role": "user", "content": message_text, "timestamp": ts}

                if event.message_id:

                    _user_entry["message_id"] = str(event.message_id)

                self.session_store.append_to_transcript(

                    session_entry.session_id,

                    _user_entry,

                )

            else:

                history_len = agent_result.get("history_offset", len(history))

                new_messages = agent_messages[history_len:] if len(agent_messages) > history_len else []

                # If no new messages found (edge case), fall back to simple user/assistant

                if not new_messages:

                    _user_entry = {"role": "user", "content": message_text, "timestamp": ts}

                    if event.message_id:

                        _user_entry["message_id"] = str(event.message_id)

                    self.session_store.append_to_transcript(

                        session_entry.session_id,

                        _user_entry,

                    )

                    if response:

                        self.session_store.append_to_transcript(

                            session_entry.session_id,

                            {"role": "assistant", "content": response, "timestamp": ts}

                        )

                else:

                    # The agent already persisted these messages to SQLite via

                    # _flush_messages_to_session_db(), so skip the DB write here

                    # to prevent the duplicate-write bug (#860).  We still write

                    # to JSONL for backward compatibility and as a backup.

                    agent_persisted = self._session_db is not None

                    # Attach the inbound platform message_id to the first user

                    # entry written this turn so platform-level quote-resolution

                    # (e.g. Yuanbao QuoteContextMiddleware's transcript fallback)

                    # can find earlier @bot messages by their original message_id.

                    _user_msg_id_attached = False

                    for msg in new_messages:

                        # Skip system messages (they're rebuilt each run)

                        if msg.get("role") == "system":

                            continue

                        # Add timestamp to each message for debugging

                        entry = {**msg, "timestamp": ts}

                        if (

                            not _user_msg_id_attached

                            and msg.get("role") == "user"

                            and event.message_id

                            and "message_id" not in entry

                        ):

                            entry["message_id"] = str(event.message_id)

                            _user_msg_id_attached = True

                        self.session_store.append_to_transcript(

                            session_entry.session_id, entry,

                            skip_db=agent_persisted,

                        )

            # Token counts and model are now persisted by the agent directly.

            # Keep only last_prompt_tokens here for context-window tracking and

            # compression decisions.

            self.session_store.update_session(

                session_entry.session_key,

                last_prompt_tokens=agent_result.get("last_prompt_tokens", 0),

            )

            # Auto voice reply: send TTS audio before the text response

            _already_sent = bool(agent_result.get("already_sent"))

            if self._should_send_voice_reply(event, response, agent_messages, already_sent=_already_sent):

                await self._send_voice_reply(event, response)

            # If streaming already delivered the response, extract and

            # deliver any MEDIA: files before returning None.  Streaming

            # sends raw text chunks that include MEDIA: tags — the normal

            # post-processing in _process_message_background is skipped

            # when already_sent is True, so media files would never be

            # delivered without this.

            #

            # Never skip when the agent failed — the error message is new

            # content the user hasn't seen (streaming only sent earlier

            # partial output before the failure).  Without this guard,

            # users see the agent "stop responding without explanation."

            if agent_result.get("already_sent") and not agent_result.get("failed"):

                if response:

                    _media_adapter = self.adapters.get(source.platform)

                    if _media_adapter:

                        await self._deliver_media_from_response(

                            response, event, _media_adapter,

                        )

                # Streaming already delivered the body text, but the footer was

                # intentionally held back (see the `not already_sent` gate above).

                # Send it now as a small trailing message so Telegram/Discord/etc.

                # still surface the runtime metadata on the final reply.

                if _footer_line:

                    try:

                        _foot_adapter = self.adapters.get(source.platform)

                        if _foot_adapter:

                            await _foot_adapter.send(

                                source.chat_id,

                                _footer_line,

                                metadata=self._thread_metadata_for_source(source, self._reply_anchor_for_event(event)),

                            )

                    except Exception as _e:

                        logger.debug("trailing footer send failed: %s", _e)

                return None

            return response

        except Exception as e:

            # Stop typing indicator on error too

            try:

                _err_adapter = self.adapters.get(source.platform)

                if _err_adapter and hasattr(_err_adapter, "stop_typing"):

                    await _err_adapter.stop_typing(source.chat_id)

            except Exception:

                _log_non_critical()

            logger.exception("Agent error in session %s", session_key)

            error_type = type(e).__name__

            error_detail = str(e)[:300] if str(e) else "no details available"

            status_hint = ""

            status_code = getattr(e, "status_code", None)

            _hist_len = len(history) if 'history' in locals() else 0

            if status_code == 401:

                status_hint = " Check your API key or run `claude /login` to refresh OAuth credentials."

            elif status_code == 402:

                status_hint = " Your API balance or quota is exhausted. Check your provider dashboard."

            elif status_code == 429:

                # Check if this is a plan usage limit (resets on a schedule) vs a transient rate limit

                _err_body = getattr(e, "response", None)

                _err_json = {}

                try:

                    if _err_body is not None:

                        _err_json = _err_body.json().get("error", {})

                        if not isinstance(_err_json, dict):

                            _err_json = {}

                except Exception:

                    _log_non_critical()

                if _err_json.get("type") == "usage_limit_reached":

                    _resets_in = _err_json.get("resets_in_seconds")

                    if _resets_in and _resets_in > 0:

                        import math

                        _hours = math.ceil(_resets_in / 3600)

                        status_hint = f" Your plan's usage limit has been reached. It resets in ~{_hours}h."

                    else:

                        status_hint = " Your plan's usage limit has been reached. Please wait until it resets."

                else:

                    status_hint = " You are being rate-limited. Please wait a moment and try again."

            elif status_code == 529:

                status_hint = " The API is temporarily overloaded. Please try again shortly."

            elif status_code in {400, 500}:

                # 400 with a large session is context overflow.

                # 500 with a large session often means the payload is too large

                # for the API to process — treat it the same way.

                if _hist_len > 50:

                    return (

                        "⚠️ Session too large for the model's context window.\n"

                        "Use /compact to compress the conversation, or "

                        "/reset to start fresh."

                    )

                elif status_code == 400:

                    status_hint = " The request was rejected by the API."

            return (

                f"Sorry, I encountered an error ({error_type}).\n"

                f"{error_detail}\n"

                f"{status_hint}"

                "Try again or use /reset to start a fresh session."

            )

        finally:

            # Restore session context variables to their pre-handler state

            self._clear_session_env(_session_env_tokens)

    # ── OAuth gateway commands ──────────────────────────────────────────

    # ────────────────────────────────────────────────────────────────

    # /goal — persistent cross-turn goals (Ralph-style loop)

    # ────────────────────────────────────────────────────────────────

    async def _deliver_media_from_response(

        self,

        response: str,

        event: MessageEvent,

        adapter,

    ) -> None:

        """Extract MEDIA: tags and local file paths from a response and deliver them.

        Called after streaming has already sent the text to the user, so the

        text itself is already delivered — this only handles file attachments

        that the normal _process_message_background path would have caught.

        """

        from pathlib import Path

        from urllib.parse import quote as _quote

        try:

            # Capture [[as_document]] before extract_media strips it, so the

            # dispatch partition below can route image-extension files

            # through send_document (preserving bytes) instead of

            # send_multiple_images (Telegram sendPhoto recompresses to ~1280px).

            force_document_attachments = "[[as_document]]" in response

            from gateway.platforms.base import BasePlatformAdapter, should_send_media_as_audio

            media_files, cleaned = adapter.extract_media(response)

            media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)

            # Chain the cleaned text through each extractor (extract_media →

            # extract_images → extract_local_files) so MEDIA: tags and image URLs

            # are removed before the bare-path auto-detect runs. Previously the

            # cleaned text from extract_media was dropped (``_``) and

            # extract_local_files scanned text that still contained MEDIA: tags,

            # producing false-positive bare-path matches with the MEDIA: prefix

            # glued on. This matches the chain order in gateway/platforms/base.py.

            _, cleaned = adapter.extract_images(cleaned)

            local_files, _ = adapter.extract_local_files(cleaned)

            local_files = BasePlatformAdapter.filter_local_delivery_paths(local_files)

            _thread_meta = self._thread_metadata_for_source(event.source, self._reply_anchor_for_event(event))

            _VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}

            _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

            # Partition out images so they can be sent as a single batch

            # (e.g. Signal's multi-attachment RPC). When [[as_document]] was

            # set, image-extension files skip the photo path and route to

            # send_document below — preserving original bytes.

            image_paths: list = []

            non_image_media: list = []

            for media_path, is_voice in media_files:

                ext = Path(media_path).suffix.lower()

                if (ext in _IMAGE_EXTS

                        and not is_voice

                        and not force_document_attachments):

                    image_paths.append(media_path)

                else:

                    non_image_media.append((media_path, is_voice))

            non_image_local: list = []

            for file_path in local_files:

                if (Path(file_path).suffix.lower() in _IMAGE_EXTS

                        and not force_document_attachments):

                    image_paths.append(file_path)

                else:

                    non_image_local.append(file_path)

            if image_paths:

                try:

                    images = [(f"file://{_quote(p)}", "") for p in image_paths]

                    await adapter.send_multiple_images(

                        chat_id=event.source.chat_id,

                        images=images,

                        metadata=_thread_meta,

                    )

                except Exception as e:

                    logger.warning("[%s] Post-stream image batch delivery failed: %s", adapter.name, e)

            for media_path, is_voice in non_image_media:

                try:

                    ext = Path(media_path).suffix.lower()

                    if should_send_media_as_audio(event.source.platform, ext, is_voice=is_voice):

                        await adapter.send_voice(

                            chat_id=event.source.chat_id,

                            audio_path=media_path,

                            metadata=_thread_meta,

                        )

                    elif ext in _VIDEO_EXTS:

                        await adapter.send_video(

                            chat_id=event.source.chat_id,

                            video_path=media_path,

                            metadata=_thread_meta,

                        )

                    else:

                        await adapter.send_document(

                            chat_id=event.source.chat_id,

                            file_path=media_path,

                            metadata=_thread_meta,

                        )

                except Exception as e:

                    logger.warning("[%s] Post-stream media delivery failed: %s", adapter.name, e)

            for file_path in non_image_local:

                try:

                    ext = Path(file_path).suffix.lower()

                    if ext in _VIDEO_EXTS:

                        await adapter.send_video(

                            chat_id=event.source.chat_id,

                            video_path=file_path,

                            metadata=_thread_meta,

                        )

                    else:

                        await adapter.send_document(

                            chat_id=event.source.chat_id,

                            file_path=file_path,

                            metadata=_thread_meta,

                        )

                except Exception as e:

                    logger.warning("[%s] Post-stream file delivery failed: %s", adapter.name, e)

        except Exception as e:

            logger.warning("Post-stream media extraction failed: %s", e)

    _TELEGRAM_CAPABILITY_HINT_COOLDOWN_S = 300.0

    # ------------------------------------------------------------------

    # Slash-command confirmation primitive (generic)

    # ------------------------------------------------------------------

    # Used by slash commands that have a non-destructive but expensive

    # side effect worth an explicit user confirmation (currently only

    # /reload-mcp, which invalidates the prompt cache).  Two delivery

    # paths:

    #   1. Button UI — adapters that override ``send_slash_confirm``

    #      (Telegram, Discord, Slack, Matrix, Feishu) render three

    #      inline buttons.  The adapter routes the button click back via

    #      ``tools.slash_confirm.resolve(session_key, confirm_id, choice)``.

    #   2. Text fallback — adapters that don't override the hook get a

    #      plain text prompt.  Users reply with /approve, /always, or

    #      /cancel; the early intercept in ``_handle_message`` matches

    #      those replies against ``tools.slash_confirm.get_pending()``.

    # ------------------------------------------------------------------

    # /approve & /deny — explicit dangerous-command approval

    # ------------------------------------------------------------------

    _APPROVAL_TIMEOUT_SECONDS = 300  # 5 minutes

    # Platforms where /update is allowed.  ACP, API server, and webhooks are

    # programmatic interfaces that should not trigger system updates.

    _UPDATE_ALLOWED_PLATFORMS = frozenset({

        Platform.TELEGRAM, Platform.DISCORD, Platform.SLACK, Platform.WHATSAPP,

        Platform.SIGNAL, Platform.MATTERMOST, Platform.MATRIX,

        Platform.HOMEASSISTANT, Platform.EMAIL, Platform.SMS, Platform.DINGTALK,

        Platform.FEISHU, Platform.WECOM, Platform.WECOM_CALLBACK, Platform.WEIXIN, Platform.BLUEBUBBLES, Platform.QQBOT, Platform.LOCAL,

    })

    async def _watch_update_progress(

        self,

        poll_interval: float = 2.0,

        stream_interval: float = 4.0,

        timeout: float = 1800.0,

    ) -> None:

        """Watch ``intellect update --gateway``, streaming output + forwarding prompts.

        Polls ``.update_output.txt`` for new content and sends chunks to the

        user periodically.  Detects ``.update_prompt.json`` (written by the

        update process when it needs user input) and forwards the prompt to

        the messenger.  The user's next message is intercepted by

        ``_handle_message`` and written to ``.update_response``.

        """

        pending_path = _intellect_home / ".update_pending.json"

        claimed_path = _intellect_home / ".update_pending.claimed.json"

        output_path = _intellect_home / ".update_output.txt"

        exit_code_path = _intellect_home / ".update_exit_code"

        prompt_path = _intellect_home / ".update_prompt.json"

        loop = asyncio.get_running_loop()

        deadline = loop.time() + timeout

        # Resolve the adapter and chat_id for sending messages

        adapter = None

        chat_id = None

        session_key = None

        metadata = None

        for path in (claimed_path, pending_path):

            if path.exists():

                try:

                    pending = json.loads(path.read_text())

                    platform_str = pending.get("platform")

                    chat_id = pending.get("chat_id")

                    chat_type = pending.get("chat_type")

                    session_key = pending.get("session_key")

                    thread_id = pending.get("thread_id")

                    if platform_str and chat_id:

                        platform = Platform(platform_str)

                        adapter = self.adapters.get(platform)

                        metadata = self._thread_metadata_for_target(

                            platform,

                            chat_id,

                            thread_id,

                            chat_type=chat_type,

                            adapter=adapter,

                        )

                        # Fallback session key if not stored (old pending files)

                        if not session_key:

                            session_key = f"{platform_str}:{chat_id}"

                    break

                except Exception:

                    _log_non_critical()

        if not adapter or not chat_id:

            logger.warning("Update watcher: cannot resolve adapter/chat_id, falling back to completion-only")

            # Fall back to old behavior: wait for exit code and send final notification

            while (pending_path.exists() or claimed_path.exists()) and loop.time() < deadline:

                if exit_code_path.exists():

                    await self._send_update_notification()

                    return

                await asyncio.sleep(poll_interval)

            if (pending_path.exists() or claimed_path.exists()) and not exit_code_path.exists():

                exit_code_path.write_text("124")

                await self._send_update_notification()

            return

        def _strip_ansi(text: str) -> str:

            return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)

        bytes_sent = 0

        last_stream_time = loop.time()

        buffer = ""

        async def _flush_buffer() -> None:

            """Send buffered output to the user."""

            nonlocal buffer, last_stream_time

            if not buffer.strip():

                buffer = ""

                return

            # Chunk to fit message limits (Telegram: 4096, others: generous)

            clean = _strip_ansi(buffer).strip()

            buffer = ""

            last_stream_time = loop.time()

            if not clean:

                return

            # Split into chunks if too long

            max_chunk = 3500

            chunks = [clean[i:i + max_chunk] for i in range(0, len(clean), max_chunk)]

            for chunk in chunks:

                try:

                    await adapter.send(chat_id, f"```\n{chunk}\n```", metadata=metadata)

                except Exception as e:

                    logger.debug("Update stream send failed: %s", e)

        while loop.time() < deadline:

            # Check for completion

            if exit_code_path.exists():

                # Read any remaining output

                if output_path.exists():

                    try:

                        content = output_path.read_text()

                        if len(content) > bytes_sent:

                            buffer += content[bytes_sent:]

                            bytes_sent = len(content)

                    except OSError:

                        pass

                await _flush_buffer()

                # Send final status

                try:

                    exit_code_raw = exit_code_path.read_text().strip() or "1"

                    exit_code = int(exit_code_raw)

                    if exit_code == 0:

                        await adapter.send(chat_id, "✅ Intellect update finished.", metadata=metadata)

                    else:

                        await adapter.send(

                            chat_id,

                            "❌ Intellect update failed (exit code {}).".format(exit_code),

                            metadata=metadata,

                        )

                    logger.info("Update finished (exit=%s), notified %s", exit_code, session_key)

                except Exception as e:

                    logger.warning("Update final notification failed: %s", e)

                # Cleanup

                for p in (pending_path, claimed_path, output_path,

                          exit_code_path, prompt_path):

                    p.unlink(missing_ok=True)

                (_intellect_home / ".update_response").unlink(missing_ok=True)

                self._update_prompt_pending.pop(session_key, None)

                return

            # Check for new output

            if output_path.exists():

                try:

                    content = output_path.read_text()

                    if len(content) > bytes_sent:

                        buffer += content[bytes_sent:]

                        bytes_sent = len(content)

                except OSError:

                    pass

            # Flush buffer periodically

            if buffer.strip() and (loop.time() - last_stream_time) >= stream_interval:

                await _flush_buffer()

            # Check for prompts — only forward if we haven't already sent

            # one that's still awaiting a response.  Without this guard the

            # watcher would re-read the same .update_prompt.json every poll

            # cycle and spam the user with duplicate prompt messages.

            if (prompt_path.exists() and session_key

                    and not self._update_prompt_pending.get(session_key)):

                try:

                    prompt_data = json.loads(prompt_path.read_text())

                    prompt_text = prompt_data.get("prompt", "")

                    default = prompt_data.get("default", "")

                    if prompt_text:

                        # Flush any buffered output first so the user sees

                        # context before the prompt

                        await _flush_buffer()

                        # Try platform-native buttons first (Discord, Telegram)

                        sent_buttons = False

                        if getattr(type(adapter), "send_update_prompt", None) is not None:

                            try:

                                await adapter.send_update_prompt(

                                    chat_id=chat_id,

                                    prompt=prompt_text,

                                    default=default,

                                    session_key=session_key,

                                    metadata=metadata,

                                )

                                sent_buttons = True

                            except Exception as btn_err:

                                logger.debug("Button-based update prompt failed: %s", btn_err)

                        if not sent_buttons:

                            default_hint = f" (default: {default})" if default else ""

                            await adapter.send(

                                chat_id,

                                f"⚕ **Update needs your input:**\n\n"

                                f"{prompt_text}{default_hint}\n\n"

                                f"Reply `/approve` (yes) or `/deny` (no), "

                                f"or type your answer directly.",

                                metadata=metadata,

                            )

                        # Keep the prompt marker on disk until the user

                        # answers. If the gateway restarts mid-prompt, the

                        # next watcher can recover by re-forwarding it from

                        # disk. Duplicate sends in the same process are

                        # still suppressed by _update_prompt_pending.

                        self._update_prompt_pending[session_key] = True

                        # .update_response to continue — it doesn't re-check

                        logger.info("Forwarded update prompt to %s: %s", session_key, prompt_text[:80])

                except (json.JSONDecodeError, OSError) as e:

                    logger.debug("Failed to read update prompt: %s", e)

            await asyncio.sleep(poll_interval)

        # Timeout

        if not exit_code_path.exists():

            logger.warning("Update watcher timed out after %.0fs", timeout)

            exit_code_path.write_text("124")

            await _flush_buffer()

            try:

                await adapter.send(

                    chat_id,

                    "❌ Intellect update timed out after 30 minutes.",

                    metadata=metadata,

                )

            except Exception:

                _log_non_critical()

            for p in (pending_path, claimed_path, output_path,

                      exit_code_path, prompt_path):

                p.unlink(missing_ok=True)

            (_intellect_home / ".update_response").unlink(missing_ok=True)

            self._update_prompt_pending.pop(session_key, None)

    async def _enrich_message_with_vision(

        self,

        user_text: str,

        image_paths: List[str],

    ) -> str:

        """

        Auto-analyze user-attached images with the vision tool and prepend

        the descriptions to the message text.

        Each image is analyzed with a general-purpose prompt.  The resulting

        description *and* the local cache path are injected so the model can:

          1. Immediately understand what the user sent (no extra tool call).

          2. Re-examine the image with vision_analyze if it needs more detail.

        Args:

            user_text:   The user's original caption / message text.

            image_paths: List of local file paths to cached images.

        Returns:

            The enriched message string with vision descriptions prepended.

        """

        from tools.vision_tools import vision_analyze_tool

        from agent.memory_manager import sanitize_context

        analysis_prompt = (

            "Describe everything visible in this image in thorough detail. "

            "Include any text, code, data, objects, people, layout, colors, "

            "and any other notable visual information."

        )

        enriched_parts = []

        for path in image_paths:

            try:

                logger.debug("Auto-analyzing user image: %s", path)

                result_json = await vision_analyze_tool(

                    image_url=path,

                    user_prompt=analysis_prompt,

                )

                result = json.loads(result_json)

                if result.get("success"):

                    description = result.get("analysis", "")

                    description = sanitize_context(description)

                    enriched_parts.append(

                        f"[The user sent an image~ Here's what I can see:\n{description}]\n"

                        f"[If you need a closer look, use vision_analyze with "

                        f"image_url: {path} ~]"

                    )

                else:

                    enriched_parts.append(

                        "[The user sent an image but I couldn't quite see it "

                        "this time (>_<) You can try looking at it yourself "

                        f"with vision_analyze using image_url: {path}]"

                    )

            except Exception as e:

                logger.error("Vision auto-analysis error: %s", e)

                enriched_parts.append(

                    f"[The user sent an image but something went wrong when I "

                    f"tried to look at it~ You can try examining it yourself "

                    f"with vision_analyze using image_url: {path}]"

                )

        # Combine: vision descriptions first, then the user's original text

        if enriched_parts:

            prefix = "\n\n".join(enriched_parts)

            if user_text:

                return f"{prefix}\n\n{user_text}"

            return prefix

        return user_text

    async def _enrich_message_with_transcription(

        self,

        user_text: str,

        audio_paths: List[str],

    ) -> str:

        """

        Auto-transcribe user voice/audio messages using the configured STT provider

        and prepend the transcript to the message text.

        Args:

            user_text:   The user's original caption / message text.

            audio_paths: List of local file paths to cached audio files.

        Returns:

            The enriched message string with transcriptions prepended.

        """

        if not getattr(self.config, "stt_enabled", True):

            notes = []

            for path in audio_paths:

                abs_path = os.path.abspath(path)

                duration_str = await _probe_audio_duration(abs_path)

                if duration_str:

                    notes.append(

                        f"[The user sent a voice message: {abs_path} (duration: {duration_str})]"

                    )

                else:

                    notes.append(f"[The user sent a voice message: {abs_path}]")

            if not notes:

                return user_text

            prefix = "\n\n".join(notes)

            _placeholder = "(The user sent a message with no text content)"

            if user_text and user_text.strip() == _placeholder:

                return prefix

            if user_text:

                return f"{prefix}\n\n{user_text}"

            return prefix

        from tools.transcription_tools import transcribe_audio

        enriched_parts = []

        for path in audio_paths:

            try:

                logger.debug("Transcribing user voice: %s", path)

                result = await asyncio.to_thread(transcribe_audio, path)

                if result["success"]:

                    transcript = result["transcript"]

                    enriched_parts.append(

                        f'[The user sent a voice message~ '

                        f'Here\'s what they said: "{transcript}"]'

                    )

                else:

                    error = result.get("error", "unknown error")

                    if (

                        "No STT provider" in error

                        or error.startswith("Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set")

                    ):

                        _no_stt_note = (

                            "[The user sent a voice message but I can't listen "

                            "to it right now — no STT provider is configured. "

                            "A direct message has already been sent to the user "

                            "with setup instructions."

                        )

                        if self._has_setup_skill():

                            _no_stt_note += (

                                " You have a skill called intellect-agent-setup "

                                "that can help users configure Intellect features "

                                "including voice, tools, and more."

                            )

                        _no_stt_note += "]"

                        enriched_parts.append(_no_stt_note)

                    else:

                        enriched_parts.append(

                            "[The user sent a voice message but I had trouble "

                            f"transcribing it~ ({error})]"

                        )

            except Exception as e:

                logger.error("Transcription error: %s", e)

                enriched_parts.append(

                    "[The user sent a voice message but something went wrong "

                    "when I tried to listen to it~ Let them know!]"

                )

        if enriched_parts:

            prefix = "\n\n".join(enriched_parts)

            # Strip the empty-content placeholder from the Discord adapter

            # when we successfully transcribed the audio — it's redundant.

            _placeholder = "(The user sent a message with no text content)"

            if user_text and user_text.strip() == _placeholder:

                return prefix

            if user_text:

                return f"{prefix}\n\n{user_text}"

            return prefix

        return user_text

    _MAX_INTERRUPT_DEPTH = 3  # Cap recursive interrupt handling (#816)

    # Config keys whose values MUST invalidate the gateway's cached agent

    # when they change.  The agent bakes these into its compressor / context

    # handling at construction time, so a mid-running-gateway config edit

    # would otherwise be silently ignored until the user triggers a

    # different cache eviction (model switch, /reset, etc.).

    #

    # Each entry is a tuple of (section, key) read from the raw config dict.

    # Add more here as new baked-at-construction config settings are added.

    _CACHE_BUSTING_CONFIG_KEYS: tuple = (

        ("model", "context_length"),

        ("model", "max_tokens"),

        ("compression", "enabled"),

        ("compression", "threshold"),

        ("compression", "target_ratio"),

        ("compression", "protect_last_n"),

        ("agent", "disabled_toolsets"),

        ("memory", "provider"),

    )

    _HONCHO_CACHE_BUSTING_KEYS = (

        "honcho.peer_name",

        "honcho.ai_peer",

        "honcho.pin_peer_name",

        "honcho.runtime_peer_prefix",

        "honcho.user_peer_aliases",

    )

    _HONCHO_CACHE_BUSTING_MEMO: dict[tuple[str, int | None], dict[str, Any]] = {}

def _run_planned_stop_watcher(

    stop_event: threading.Event,

    runner,

    loop: asyncio.AbstractEventLoop,

    shutdown_handler,

    *,

    poll_interval: float = 0.5,

) -> None:

    """Poll for the planned-stop marker and trigger graceful shutdown.

    On Windows, ``asyncio.add_signal_handler`` raises NotImplementedError

    for SIGTERM/SIGINT, so the standard signal-driven shutdown path

    never runs when ``intellect gateway stop`` signals the gateway. The

    consequence is that the drain loop is skipped — in-flight agent

    sessions are killed mid-turn and ``resume_pending`` is never set,

    so the next gateway boot has no idea those sessions need to be

    auto-resumed (issue #33778, v0.13.0 session-resume feature broken

    on native Windows).

    This watcher runs on every platform (cheap, defensive) and bridges

    the gap on Windows by translating a filesystem marker into the

    same shutdown-handler invocation a real SIGTERM would have produced

    on POSIX. The CLI's ``intellect_cli.gateway_windows.stop()`` writes

    the marker via ``write_planned_stop_marker(pid)`` and then waits

    for the gateway PID to exit; this watcher is what makes that

    exit happen cleanly.

    On POSIX this is a no-op safety net — the signal handler always

    races us to consuming the marker file because it fires synchronously

    from the kernel's signal delivery.

    Args:

        stop_event: cleared by start_gateway() during normal shutdown

            to tell the watcher to exit.

        runner: the GatewayRunner instance; we check ``_running`` and

            ``_draining`` to avoid triggering shutdown if the gateway

            is already in one of those states.

        loop: the asyncio event loop the shutdown handler must run on.

        shutdown_handler: same callable that's wired to SIGTERM —

            tolerates a ``None`` signal argument (planned stop case)

            and consumes the marker via

            ``consume_planned_stop_marker_for_self()``.

        poll_interval: seconds between marker checks. 0.5s gives a

            responsive shutdown without burning CPU.

    """

    from gateway.status import (

        _get_planned_stop_marker_path,

        planned_stop_marker_targets_self,

    )

    marker_path = _get_planned_stop_marker_path()

    while not stop_event.is_set():

        try:

            if (

                marker_path.exists()

                and not getattr(runner, "_draining", False)

                and getattr(runner, "_running", False)

            ):

                # A marker existing is NOT sufficient — it may have been

                # written for a PREVIOUS gateway instance (different PID)

                # and left behind because that process exited before the

                # CLI's stop() could clean it up. Firing the handler on a

                # stale/foreign marker drives the gateway into shutdown,

                # then consume_planned_stop_marker_for_self() correctly

                # reports a PID mismatch — but by then we're already

                # stopping, so it's logged as an unexpected "UNKNOWN" exit

                # and the watchdog crash-loops the gateway (issue #34597,

                # a regression from PR #33798 which added this watcher

                # without the PID check).

                #

                # Only fire when the marker actually targets us. The probe

                # is non-destructive on a match (the handler does the

                # authoritative consume on the loop thread) and self-heals

                # by unlinking stale/malformed markers so they cannot wedge

                # a freshly booted gateway.

                if not planned_stop_marker_targets_self():

                    stop_event.wait(poll_interval)

                    continue

                # Drive the same path as a real signal handler.

                # Pass signal=None — the handler tolerates that and consumes

                # the marker via consume_planned_stop_marker_for_self,

                # which also validates target_pid + start_time match us.

                loop.call_soon_threadsafe(shutdown_handler, None)

                # Done — the handler will set _draining; we exit on next tick.

                break

        except Exception as _e:

            logger.debug("Planned-stop watcher tick error: %s", _e)

        stop_event.wait(poll_interval)

def _start_cron_ticker(stop_event: threading.Event, adapters=None, loop=None, interval: int = 60):

    """

    Background thread that ticks the cron scheduler at a regular interval.

    Runs inside the gateway process so cronjobs fire automatically without

    needing a separate `intellect cron daemon` or system cron entry.

    When ``adapters`` and ``loop`` are provided, passes them through to the

    cron delivery path so live adapters can be used for E2EE rooms.

    Also refreshes the channel directory every 5 minutes and prunes the

    image/audio/document cache + expired ``intellect debug share`` pastes

    once per hour.

    """

    from cron.scheduler import tick as cron_tick

    from gateway.platforms.base import cleanup_image_cache, cleanup_document_cache

    from intellect_cli.debug import _sweep_expired_pastes

    IMAGE_CACHE_EVERY = 60   # ticks — once per hour at default 60s interval

    CHANNEL_DIR_EVERY = 5    # ticks — every 5 minutes

    PASTE_SWEEP_EVERY = 60   # ticks — once per hour

    CURATOR_EVERY = 60       # ticks — poll hourly (inner gate handles the real cadence)

    logger.info("Cron ticker started (interval=%ds)", interval)

    tick_count = 0

    while not stop_event.is_set():

        try:

            cron_tick(verbose=False, adapters=adapters, loop=loop)

        except Exception as e:

            logger.debug("Cron tick error: %s", e)

        tick_count += 1

        if tick_count % CHANNEL_DIR_EVERY == 0 and adapters:

            try:

                from gateway.channel_directory import build_channel_directory

                if loop is not None:

                    # build_channel_directory is async (Slack web calls), and

                    # this ticker runs in a background thread. Schedule onto

                    # the gateway event loop and wait briefly for completion

                    # so refresh failures are still logged via the except.

                    fut = safe_schedule_threadsafe(

                        build_channel_directory(adapters), loop,

                        logger=logger,

                        log_message="Channel directory refresh scheduling error",

                    )

                    if fut is not None:

                        fut.result(timeout=30)

            except Exception as e:

                logger.debug("Channel directory refresh error: %s", e)

        if tick_count % IMAGE_CACHE_EVERY == 0:

            try:

                removed = cleanup_image_cache(max_age_hours=24)

                if removed:

                    logger.info("Image cache cleanup: removed %d stale file(s)", removed)

            except Exception as e:

                logger.debug("Image cache cleanup error: %s", e)

            try:

                removed = cleanup_document_cache(max_age_hours=24)

                if removed:

                    logger.info("Document cache cleanup: removed %d stale file(s)", removed)

            except Exception as e:

                logger.debug("Document cache cleanup error: %s", e)

        if tick_count % PASTE_SWEEP_EVERY == 0:

            try:

                deleted, remaining = _sweep_expired_pastes()

                if deleted:

                    logger.info(

                        "Paste sweep: deleted %d expired paste(s), %d pending",

                        deleted, remaining,

                    )

            except Exception as e:

                logger.debug("Paste sweep error: %s", e)

        # Curator — piggy-back on the existing cron ticker so long-running

        # gateways get weekly skill maintenance without needing restarts.

        # maybe_run_curator() is internally gated by config.interval_hours

        # (7 days by default), so CURATOR_EVERY is just the poll rate — the

        # real work only fires once per config interval.

        if tick_count % CURATOR_EVERY == 0:

            try:

                from agent.curator import maybe_run_curator

                maybe_run_curator(

                    idle_for_seconds=float("inf"),

                    on_summary=lambda msg: logger.info("curator: %s", msg),

                )

            except Exception as e:

                logger.debug("Curator tick error: %s", e)

        try:

            from intellect_cli.vault_build import maybe_run_vault_scheduled_builds

            vault_result = maybe_run_vault_scheduled_builds()

            if vault_result and vault_result.ran and vault_result.summary:

                logger.info("vault-scheduler: %s", vault_result.summary)

        except Exception as e:

            logger.debug("Vault scheduled tick error: %s", e)

        stop_event.wait(timeout=interval)

    logger.info("Cron ticker stopped")

async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool = False, verbosity: Optional[int] = 0) -> bool:

    """

    Start the gateway and run until interrupted.

    This is the main entry point for running the gateway.

    Returns True if the gateway ran successfully, False if it failed to start.

    A False return causes a non-zero exit code so systemd can auto-restart.

    Args:

        config: Optional gateway configuration override.

        replace: If True, kill any existing gateway instance before starting.

                 Useful for systemd services to avoid restart-loop deadlocks

                 when the previous process hasn't fully exited yet.

    """

    # ── Duplicate-instance guard ──────────────────────────────────────

    # Prevent two gateways from running under the same INTELLECT_HOME.

    # The PID file is scoped to INTELLECT_HOME, so future multi-profile

    # setups (each profile using a distinct INTELLECT_HOME) will naturally

    # allow concurrent instances without tripping this guard.

    from gateway.status import (

        acquire_gateway_runtime_lock,

        get_running_pid,

        get_process_start_time,

        release_gateway_runtime_lock,

        remove_pid_file,

        terminate_pid,

    )

    existing_pid = get_running_pid()

    if existing_pid is not None and existing_pid != os.getpid():

        if replace:

            existing_start_time = get_process_start_time(existing_pid)

            logger.info(

                "Replacing existing gateway instance (PID %d) with --replace.",

                existing_pid,

            )

            # Record a takeover marker so the target's shutdown handler

            # recognises its SIGTERM as a planned takeover and exits 0

            # (rather than exit 1, which would trigger systemd's

            # Restart=on-failure and start a flap loop against us).

            # Best-effort — proceed even if the write fails.

            try:

                from gateway.status import write_takeover_marker

                write_takeover_marker(existing_pid)

            except Exception as e:

                logger.debug("Could not write takeover marker: %s", e)

            try:

                terminate_pid(existing_pid, force=False)

            except ProcessLookupError:

                pass  # Already gone

            except (PermissionError, OSError):

                logger.error(

                    "Permission denied killing PID %d. Cannot replace.",

                    existing_pid,

                )

                # Marker is scoped to a specific target; clean it up on

                # give-up so it doesn't grief an unrelated future shutdown.

                try:

                    from gateway.status import clear_takeover_marker

                    clear_takeover_marker()

                except Exception:

                    _log_non_critical()

                return False

            # Wait up to 10 seconds for the old process to exit.

            # ``os.kill(pid, 0)`` on Windows is NOT a no-op — use the

            # handle-based existence check instead.

            from gateway.status import _pid_exists

            for _ in range(20):

                if not _pid_exists(existing_pid):

                    break  # Process is gone

                time.sleep(0.5)

            else:

                # Still alive after 10s — force kill

                logger.warning(

                    "Old gateway (PID %d) did not exit after SIGTERM, sending SIGKILL.",

                    existing_pid,

                )

                try:

                    terminate_pid(existing_pid, force=True)

                    time.sleep(0.5)

                except (ProcessLookupError, PermissionError, OSError):

                    pass

            remove_pid_file()

            # remove_pid_file() is a no-op when the PID doesn't match.

            # Force-unlink to cover the old-process-crashed case.

            try:

                (get_intellect_home() / "gateway.pid").unlink(missing_ok=True)

            except Exception:

                _log_non_critical()

            # Clean up any takeover marker the old process didn't consume

            # (e.g. SIGKILL'd before its shutdown handler could read it).

            try:

                from gateway.status import clear_takeover_marker

                clear_takeover_marker()

            except Exception:

                _log_non_critical()

            # Also release all scoped locks left by the old process.

            # Stopped (Ctrl+Z) processes don't release locks on exit,

            # leaving stale lock files that block the new gateway from starting.

            try:

                from gateway.status import release_all_scoped_locks

                _released = release_all_scoped_locks(

                    owner_pid=existing_pid,

                    owner_start_time=existing_start_time,

                )

                if _released:

                    logger.info("Released %d stale scoped lock(s) from old gateway.", _released)

            except Exception:

                _log_non_critical()

        else:

            intellect_home = str(get_intellect_home())

            logger.error(

                "Another gateway instance is already running (PID %d, INTELLECT_HOME=%s). "

                "Use 'intellect gateway restart' to replace it, or 'intellect gateway stop' first.",

                existing_pid, intellect_home,

            )

            print(

                f"\n❌ Gateway already running (PID {existing_pid}).\n"

                f"   Use 'intellect gateway restart' to replace it,\n"

                f"   or 'intellect gateway stop' to kill it first.\n"

                f"   Or use 'intellect gateway run --replace' to auto-replace.\n"

            )

            return False

    # Sync bundled skills on gateway start (fast -- skips unchanged)

    try:

        from tools.skills_sync import sync_skills

        sync_skills(quiet=True)

    except Exception:

        _log_non_critical()

    # Centralized logging — agent.log (INFO+), errors.log (WARNING+),

    # and gateway.log (INFO+, gateway-component records only).

    # Idempotent, so repeated calls from AIAgent.__init__ won't duplicate.

    from intellect_logging import setup_logging

    setup_logging(intellect_home=_intellect_home, mode="gateway")

    # Periodic process memory usage logging (gateway only) — emits a

    # grep-friendly "[MEMORY] rss=...MB ..." line every N minutes so

    # slow leaks in the long-lived gateway process show up as a time

    # series in agent.log / gateway.log.  Ported from cline/cline#10343.

    # Controlled by the logging.memory_monitor section in config.yaml.

    try:

        from gateway import memory_monitor as _memory_monitor

        _mm_cfg = {}

        try:

            # config is loaded a few lines up; re-read the logging section

            # here so we pick up user overrides without coupling to local

            # variable names inside the start_gateway body.

            from intellect_cli.config import load_config as _load_cli_config

            _mm_cfg = (_load_cli_config() or {}).get("logging", {}).get("memory_monitor", {}) or {}

        except Exception:

            _mm_cfg = {}

        if _mm_cfg.get("enabled", True):

            try:

                _mm_interval = float(_mm_cfg.get("interval_seconds", 300))

            except (TypeError, ValueError):

                _mm_interval = 300.0

            _memory_monitor.start_memory_monitoring(interval_seconds=_mm_interval)

    except Exception as _mm_exc:

        logger.debug("Failed to start memory monitor: %s", _mm_exc)

    # Optional stderr handler — level driven by -v/-q flags on the CLI.

    # verbosity=None (-q/--quiet): no stderr output

    # verbosity=0    (default):    WARNING and above

    # verbosity=1    (-v):         INFO and above

    # verbosity=2+   (-vv/-vvv):   DEBUG

    if verbosity is not None:

        from agent.redact import RedactingFormatter

        _stderr_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)

        _stderr_handler = logging.StreamHandler()

        _stderr_handler.setLevel(_stderr_level)

        _stderr_handler.setFormatter(RedactingFormatter('%(levelname)s %(name)s: %(message)s'))

        logging.getLogger().addHandler(_stderr_handler)

        # Lower root logger level if needed so DEBUG records can reach the handler

        if _stderr_level < logging.getLogger().level:

            logging.getLogger().setLevel(_stderr_level)

    runner = GatewayRunner(config)

    # Track whether an unexpected signal initiated the shutdown. When an

    # unexpected SIGTERM kills the gateway, we exit non-zero so service

    # managers can revive the process. Planned stop paths write a marker

    # before signalling us so they can exit cleanly instead.

    _signal_initiated_shutdown = False

    # Set up signal handlers

    def shutdown_signal_handler(received_signal=None):

        nonlocal _signal_initiated_shutdown

        # Planned --replace takeover check: when a sibling gateway is

        # taking over via --replace, it wrote a marker naming this PID

        # before sending SIGTERM. If present, treat the signal as a

        # planned shutdown and exit 0 so systemd's Restart=on-failure

        # doesn't revive us (which would flap-fight the replacer when

        # both services are enabled, e.g. intellect.service + intellect-

        # gateway.service from pre-rename installs).

        planned_takeover = False

        try:

            from gateway.status import consume_takeover_marker_for_self

            planned_takeover = consume_takeover_marker_for_self()

        except Exception as e:

            logger.debug("Takeover marker check failed: %s", e)

        # Planned stop check: service managers and `intellect gateway stop`

        # also send SIGTERM, which is indistinguishable from an unexpected

        # external kill unless the CLI marks it first. SIGINT comes from an

        # interactive Ctrl+C and is likewise an intentional foreground stop.

        planned_stop = False

        if received_signal == signal.SIGINT:

            planned_stop = True

        elif not planned_takeover:

            try:

                from gateway.status import consume_planned_stop_marker_for_self

                planned_stop = consume_planned_stop_marker_for_self()

            except Exception as e:

                logger.debug("Planned stop marker check failed: %s", e)

        # Fast (<10ms) snapshot of who's asking us to shut down — runs

        # synchronously inside the asyncio signal handler, so we keep it

        # purely stdlib + /proc reads, no subprocesses.  See PR #15826

        # (May 2026): the previous implementation called `ps aux` here

        # synchronously, blocking the event loop for up to 3s while

        # adapter teardown couldn't begin.

        try:

            from gateway.shutdown_forensics import (

                format_context_for_log,

                snapshot_shutdown_context,

                spawn_async_diagnostic,

            )

            _shutdown_ctx = snapshot_shutdown_context(received_signal)

        except Exception as _e:

            _shutdown_ctx = None

            logger.debug("snapshot_shutdown_context failed: %s", _e)

        if planned_takeover:

            logger.info(

                "Received %s as a planned --replace takeover — exiting cleanly",

                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM",

            )

        elif planned_stop:

            logger.info(

                "Received %s as a planned gateway stop — exiting cleanly",

                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",

            )

        else:

            _signal_initiated_shutdown = True

            logger.info(

                "Received %s — initiating shutdown",

                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",

            )

        # Always log who/what triggered the signal — most useful single

        # line when diagnosing "the gateway keeps dying" tickets.  Format

        # is one line, key=value, parent_cmdline last (often long).

        if _shutdown_ctx is not None:

            try:

                logger.warning(

                    "Shutdown context: %s", format_context_for_log(_shutdown_ctx)

                )

            except Exception as _e:

                logger.debug("format_context_for_log failed: %s", _e)

            # Spawn the heavyweight diagnostic (ps auxf, pstree, dmesg) in

            # a detached subprocess so it can finish writing to disk even

            # if our cgroup is being torn down.  Bounded by an internal

            # timeout; never blocks the event loop here.

            try:

                _diag_log = _intellect_home / "logs" / "gateway-shutdown-diag.log"

                spawn_async_diagnostic(

                    _diag_log, _shutdown_ctx["signal"], timeout_seconds=5.0

                )

            except Exception as _e:

                logger.debug("spawn_async_diagnostic failed: %s", _e)

        asyncio.create_task(runner.stop())

    def restart_signal_handler():

        runner.request_restart(detached=False, via_service=True)

    loop = asyncio.get_running_loop()

    # Install a loop-level exception handler that swallows transient

    # network errors from background tasks. Issues #31066 / #31110:

    # an unhandled ``telegram.error.TimedOut`` (or peer NetworkError /

    # httpx connection error) in any awaited coroutine would propagate

    # to the loop and kill the gateway process, taking down every

    # profile attached to the same runner. systemd then restarts the

    # service after ~5s but the active conversation turn is lost.

    #

    # The fix is intentionally narrow: only well-known transient

    # network errors are swallowed (and logged with full traceback so

    # the originating call site is still discoverable). Anything else

    # is forwarded to the default handler so real bugs still surface.

    loop.set_exception_handler(_gateway_loop_exception_handler)

    if threading.current_thread() is threading.main_thread():

        for sig in (signal.SIGINT, signal.SIGTERM):

            try:

                loop.add_signal_handler(sig, shutdown_signal_handler, sig)  # windows-footgun: ok — wrapped in try/except NotImplementedError for Windows

            except NotImplementedError:

                pass

        if hasattr(signal, "SIGUSR1"):

            try:

                loop.add_signal_handler(signal.SIGUSR1, restart_signal_handler)  # windows-footgun: ok — POSIX signal, guarded by hasattr above + try/except NotImplementedError

            except NotImplementedError:

                pass

    else:

        logger.info("Skipping signal handlers (not running in main thread).")

    # Windows fallback: asyncio.add_signal_handler raises NotImplementedError

    # on Windows, so `intellect gateway stop`'s SIGTERM (which Python maps to

    # TerminateProcess on Windows) never invokes shutdown_signal_handler.

    # That means the drain loop never runs, mark_resume_pending never fires,

    # and sessions are silently lost across restarts (issue #33778).

    #

    # The fix is a marker-polling thread: `intellect gateway stop` writes the

    # planned-stop marker BEFORE killing, and this thread notices it and

    # drives the same shutdown path the signal handler would have.  Runs

    # on every platform (cheap, defensive) so non-signal-bearing

    # environments (Windows native, sandboxed CI runners that mask

    # SIGTERM) still get a clean drain.

    _planned_stop_watcher_stop = threading.Event()

    _planned_stop_watcher_thread = threading.Thread(

        target=_run_planned_stop_watcher,

        args=(_planned_stop_watcher_stop, runner, loop, shutdown_signal_handler),

        daemon=True,

        name="planned-stop-watcher",

    )

    _planned_stop_watcher_thread.start()

    # Claim the PID file BEFORE bringing up any platform adapters.

    # This closes the --replace race window: two concurrent `gateway run

    # --replace` invocations both pass the termination-wait above, but

    # only the winner of the O_CREAT|O_EXCL race below will ever open

    # Telegram polling, Discord gateway sockets, etc. The loser exits

    # cleanly before touching any external service.

    import atexit

    from gateway.status import write_pid_file, remove_pid_file, get_running_pid

    _current_pid = get_running_pid()

    if _current_pid is not None and _current_pid != os.getpid():

        logger.error(

            "Another gateway instance (PID %d) started during our startup. "

            "Exiting to avoid double-running.", _current_pid

        )

        return False

    if not acquire_gateway_runtime_lock():

        logger.error(

            "Gateway runtime lock is already held by another instance. Exiting."

        )

        return False

    try:

        write_pid_file()

    except FileExistsError:

        release_gateway_runtime_lock()

        logger.error(

            "PID file race lost to another gateway instance. Exiting."

        )

        return False

    atexit.register(remove_pid_file)

    atexit.register(release_gateway_runtime_lock)

    # MCP tool discovery — run in an executor so the asyncio event loop

    # stays responsive even when a configured MCP server is slow or

    # unreachable.  discover_mcp_tools() uses a blocking 120s wait

    # internally; calling it from the loop thread would freeze platform

    # heartbeats (Discord shard, Telegram polling) until it returned.

    # See #16856.

    try:

        from tools.mcp_tool import discover_mcp_tools

        _loop = asyncio.get_running_loop()

        await _loop.run_in_executor(None, discover_mcp_tools)

    except Exception as e:

        logger.debug("MCP tool discovery failed: %s", e)

    # Start the gateway

    success = await runner.start()

    if not success:

        return False

    if runner.should_exit_cleanly:

        if runner.exit_reason:

            logger.error("Gateway exiting cleanly: %s", runner.exit_reason)

        return True

    # Start background cron ticker so scheduled jobs fire automatically.

    # Pass the event loop so cron delivery can use live adapters (E2EE support).

    cron_stop = threading.Event()

    cron_thread = threading.Thread(

        target=_start_cron_ticker,

        args=(cron_stop,),

        kwargs={"adapters": runner.adapters, "loop": asyncio.get_running_loop()},

        daemon=True,

        name="cron-ticker",

    )

    cron_thread.start()

    # Wait for shutdown

    await runner.wait_for_shutdown()

    if runner.should_exit_with_failure:

        if runner.exit_reason:

            logger.error("Gateway exiting with failure: %s", runner.exit_reason)

        return False

    # Stop cron ticker cleanly

    cron_stop.set()

    cron_thread.join(timeout=5)

    # Stop the planned-stop watcher (daemon=True so this is belt-and-suspenders).

    _planned_stop_watcher_stop.set()

    _planned_stop_watcher_thread.join(timeout=2)

    # Close MCP server connections

    try:

        from tools.mcp_tool import shutdown_mcp_servers

        shutdown_mcp_servers()

    except Exception:

        _log_non_critical()

    # Stop the periodic memory monitor (if it was started above).

    # This also emits one final "[MEMORY] shutdown rss=..." line so the

    # last RSS reading before gateway exit is always in the log.

    try:

        from gateway import memory_monitor as _memory_monitor

        _memory_monitor.stop_memory_monitoring(timeout=2.0)

    except Exception:

        _log_non_critical()

    if runner.exit_code is not None:

        raise SystemExit(runner.exit_code)

    # When an unexpected SIGTERM caused the shutdown and it wasn't a planned

    # restart (/restart, /update, SIGUSR1), exit non-zero so systemd's

    # Restart=on-failure revives the process.  This covers:

    #   - intellect update killing the gateway mid-work

    #   - External kill commands

    #   - WSL2/container runtime sending unexpected signals

    # `intellect gateway stop` and interactive Ctrl+C are handled above as

    # planned stops and should not trigger service-manager revival.

    if _signal_initiated_shutdown and not runner._restart_requested:

        logger.info(

            "Exiting with code 1 (signal-initiated shutdown without restart "

            "request) so systemd Restart=on-failure can revive the gateway."

        )

        return False  # → sys.exit(1) in the caller

    # When the gateway is restarting via the service manager (SIGUSR1 →

    # launchd_restart or /restart / /update commands), exit with code 75 so

    # that launchd's ``KeepAlive → SuccessfulExit → false`` policy treats

    # the exit as *unsuccessful* and relaunches the service.  This mirrors

    # the systemd ``RestartForceExitStatus=75`` convention already used by

    # the systemd unit template.

    if runner._restart_via_service:

        logger.info(

            "Exiting with code 75 (service-restart requested) so "

            "launchd KeepAlive relaunches the gateway."

        )

        raise SystemExit(75)

    return True

def main():

    """CLI entry point for the gateway."""

    # Force UTF-8 stdio on Windows — gateway logs and startup banner would

    # otherwise UnicodeEncodeError on cp1252 consoles.  No-op on POSIX.

    try:

        from intellect_cli.stdio import configure_windows_stdio

        configure_windows_stdio()

    except Exception:

        _log_non_critical()

    import argparse

    parser = argparse.ArgumentParser(description="Intellect Gateway - Multi-platform messaging")

    parser.add_argument("--config", "-c", help="Path to gateway config file")

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    config = None

    if args.config:

        import yaml

        with open(args.config, encoding="utf-8") as f:

            data = yaml.safe_load(f) or {}

            config = GatewayConfig.from_dict(data)

    # Run the gateway - exit with code 1 if no platforms connected,

    # so systemd Restart=on-failure will retry on transient errors (e.g. DNS)

    success = asyncio.run(start_gateway(config))

    if not success:

        sys.exit(1)

if __name__ == "__main__":

    main()