# lgtm[py/incomplete-url-substring-sanitization]: URL substring for provider identification
# lgtm[py/clear-text-logging-sensitive-data]: logger.info prints operational data
"""Helper functions extracted from ``agent/conversation_loop.py``.
"""

from __future__ import annotations

def _ollama_context_limit_error(agent: Any, request_tokens: int) -> Optional[str]:
    """Return a user-facing error when Ollama is loaded with too little context."""
    if not getattr(agent, "tools", None):
        return None

    runtime_ctx = getattr(agent, "_ollama_num_ctx", None)
    if not isinstance(runtime_ctx, int) or runtime_ctx <= 0:
        return None
    if runtime_ctx >= MINIMUM_CONTEXT_LENGTH:
        return None

    model = getattr(agent, "model", "") or "the selected model"
    base_url = getattr(agent, "base_url", "") or "unknown base URL"
    provider = getattr(agent, "provider", "") or "unknown"
    tool_count = len(getattr(agent, "tools", None) or [])

    logger.warning(
        "Ollama runtime context too small for Intellect tool use: "
        "model=%s provider=%s base_url=%s runtime_context=%d "
        "minimum_context=%d estimated_request_tokens=%d tool_count=%d "
        "session=%s",
        model,
        provider,
        base_url,
        runtime_ctx,
        MINIMUM_CONTEXT_LENGTH,
        request_tokens,
        tool_count,
        getattr(agent, "session_id", None) or "none",
    )

    return (
        f"Ollama loaded `{model}` with only {runtime_ctx:,} tokens of runtime "
        f"context, but Intellect needs at least {MINIMUM_CONTEXT_LENGTH:,} tokens "
        "for reliable tool use.\n\n"
        "Increase the Ollama context for this model and restart/reload the "
        "model before trying again. A known-good starting point is 65,536 "
        "tokens. In Intellect config, set `model.ollama_num_ctx: 65536` "
        "(and `model.context_length: 65536` if you also override the displayed "
        "model context). If you manage the model through an Ollama Modelfile, "
        "set `PARAMETER num_ctx 65536` there instead."
    )


def _ra():
    """Lazy reference to ``run_agent`` so callers can patch
    ``run_agent.handle_function_call`` / ``run_agent._set_interrupt`` /
    ``run_agent.OpenAI`` and have those patches reach this code path.
    """
    import run_agent
    return run_agent


def _ontoweb_entitlement_message(capability: str) -> str:
    try:
        from intellect_cli.ontoweb_account import (
            format_ontoweb_portal_entitlement_message,
            get_ontoweb_portal_account_info,
        )

        account_info = get_ontoweb_portal_account_info(force_fresh=True)
        message = format_ontoweb_portal_entitlement_message(
            account_info,
            capability=capability,
        )
        return message or ""
    except Exception:
        return ""


def _print_ontoweb_entitlement_guidance(agent, capability: str) -> bool:
    message = _ontoweb_entitlement_message(capability)
    if not message:
        return False
    for line in message.splitlines():
        agent._vprint(f"{agent.log_prefix}   💡 {line}", force=True)
    return True


def _is_ontoweb_inference_route(provider: str, base_url: str) -> bool:
    provider = (provider or "").strip().lower()
    if provider == "ontoweb":
        return True
    base = str(base_url or "")
    return (
        base_url_host_matches(base, "inference-api.ontoweb.cn")
        or base_url_host_matches(base, "inference.ontoweb.cn")
    )


def _billing_or_entitlement_message(
    *,
    capability: str,
    provider: str,
    base_url: str,
    model: str,
) -> str:
    if _is_ontoweb_inference_route(provider, base_url):
        return _ontoweb_entitlement_message(capability)

    provider_label = (provider or "").strip() or "the selected provider"
    model_label = (model or "").strip() or "the selected model"
    lines = [
        (
            f"{provider_label} reported that billing, credits, or account "
            f"entitlement is exhausted for {model_label}."
        ),
        "Add credits or update billing with that provider, then retry.",
    ]
    if base_url_host_matches(str(base_url or ""), "openrouter.ai"):
        lines.append("OpenRouter credits: https://openrouter.ai/settings/credits")
    lines.append("You can switch providers temporarily with /model <model> --provider <provider>.")
    return "\n".join(lines)


def _print_billing_or_entitlement_guidance(
    agent,
    *,
    capability: str,
    provider: str,
    base_url: str,
    model: str,
) -> bool:
    message = _billing_or_entitlement_message(
        capability=capability,
        provider=provider,
        base_url=base_url,
        model=model,
    )
    if not message:
        return False
    for line in message.splitlines():
        agent._vprint(f"{agent.log_prefix}   💡 {line}", force=True)
    return True


def _try_refresh_ontoweb_paid_entitlement_credentials(agent) -> bool:
    """Refresh OntoWeb runtime credentials after a fresh paid-entitlement check."""
    try:
        from intellect_cli.ontoweb_account import get_ontoweb_portal_account_info

        account_info = get_ontoweb_portal_account_info(force_fresh=True)
        if account_info.paid_service_access is not True:
            return False
        return agent._try_refresh_ontoweb_client_credentials(
            force=True,
        )
    except Exception:
        return False


def _restore_or_build_system_prompt(agent, system_message, conversation_history):
    """Restore the cached system prompt from the session DB or build it fresh.

    Mutates ``agent._cached_system_prompt`` and persists a freshly-built
    prompt back to the session DB on first build.  Extracted from
    ``run_conversation`` so the prefix-cache restore path can be tested in
    isolation.

    Three-way state distinction for the stored row, surfaced via logs so
    silent prefix-cache misses are visible in ``agent.log``:

      * ``missing`` — no session row yet (legitimate first turn).
      * ``null``   — row exists, ``system_prompt`` column is NULL.
        Legacy session predating system-prompt persistence, or a migration
        leftover.  Warns when ``conversation_history`` is non-empty.
      * ``empty``  — row exists, ``system_prompt`` column is the empty
        string.  Indicates a previous-turn write that ran but stored
        nothing (silent persistence bug).  Always warns.
      * ``present`` — row exists with a usable prompt → reused verbatim.

    Read or write failures against the session DB log at WARNING (not
    DEBUG) so persistent issues (disk full, schema drift, lock contention)
    surface without needing verbose mode.  This used to be a debug-level
    log that silently broke prefix-cache reuse on the gateway path
    (which constructs a fresh ``AIAgent`` per turn and depends on this
    DB roundtrip).
    """
    stored_prompt = None
    stored_state = "missing"
    if conversation_history and agent._session_db:
        try:
            session_row = agent._session_db.get_session(agent.session_id, include_system_prompt=True)
            if session_row is not None:
                raw_prompt = session_row.get("system_prompt")
                if raw_prompt is None:
                    stored_state = "null"
                elif raw_prompt == "":
                    stored_state = "empty"
                else:
                    stored_prompt = raw_prompt
                    stored_state = "present"
        except Exception as exc:
            logger.warning(
                "Session DB get_session failed for system-prompt restore "
                "(session=%s): %s. Falling back to fresh build — prefix "
                "cache will miss for this turn.",
                agent.session_id, exc,
            )

    if stored_prompt:
        # Continuing session — reuse the exact system prompt from the
        # previous turn so the Anthropic cache prefix matches.
        agent._cached_system_prompt = stored_prompt
        return

    if conversation_history and stored_state in ("null", "empty"):
        # Continuing session whose stored prompt is unusable.  The
        # previous turn's write either never happened or wrote an empty
        # string — either way every turn now rebuilds and the prefix
        # cache misses every time.
        logger.warning(
            "Stored system prompt for session %s is %s; rebuilding "
            "from scratch this turn. Prefix cache will miss until "
            "the rebuild persists. Investigate the previous turn's "
            "update_system_prompt write path.",
            agent.session_id, stored_state,
        )

    # First turn of a new session (or recovering from a broken stored
    # prompt) — build from scratch.
    agent._cached_system_prompt = agent._build_system_prompt(system_message)

    # Plugin hook: on_session_start — fired once when a brand-new
    # session is created (not on continuation).  Plugins can use this
    # to initialise session-scoped state (e.g. warm a memory cache).
    try:
        from intellect_cli.plugins import invoke_hook as _invoke_hook
        _invoke_hook(
            "on_session_start",
            session_id=agent.session_id,
            model=agent.model,
            platform=getattr(agent, "platform", None) or "",
        )
    except Exception as exc:
        logger.warning("on_session_start hook failed: %s", exc)

    # Persist the system prompt snapshot in SQLite.  Failure here used
    # to log at DEBUG, which silently broke prefix-cache reuse on the
    # gateway path (fresh AIAgent per turn → reads from this row every
    # subsequent turn).
    if agent._session_db:
        try:
            agent._session_db.update_system_prompt(agent.session_id, agent._cached_system_prompt)
        except Exception as exc:
            logger.warning(
                "Session DB update_system_prompt failed for session %s: "
                "%s. Subsequent turns will rebuild the system prompt and "
                "miss the prefix cache.",
                agent.session_id, exc,
            )


def _get_continuation_prompt(is_partial_stub: bool, dropped_tools: Optional[List[str]] = None) -> str:
    if is_partial_stub and dropped_tools:
        tool_list = ", ".join(dropped_tools[:3])
        return (
            "[System: Your previous tool call "
            f"({tool_list}) was too large and "
            "the stream timed out before it "
            "could be delivered. Do NOT retry "
            "the same tool call with the same "
            "large content. Instead, break the "
            "content into multiple smaller tool "
            "calls (e.g. use multiple patch calls "
            "or write smaller files). Each tool "
            "call's arguments must be under ~8K "
            "tokens to avoid stream timeouts.]"
        )
    elif is_partial_stub:
        return (
            "[System: The previous response was cut off by a "
            "network error mid-stream. Continue exactly where "
            "you left off. Do not restart or repeat prior text. "
            "Finish the answer directly.]"
        )
    else:
        return (
            "[System: Your previous response was truncated by the output "
            "length limit. Continue exactly where you left off. Do not "
            "restart or repeat prior text. Finish the answer directly.]"
        )


# ═══════════════════════════════════════════════════════════════════════════
# run_conversation() refactoring — data classes and extracted phases
# ═══════════════════════════════════════════════════════════════════════════

import dataclasses
import logging

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ConversationState:
    """State passed between phases of run_conversation().

    Eliminates the need for 10+ local variables threaded through
    a 4,500-line function.  Each phase reads/writes its own fields.
    """
    agent: Any
    messages: list
    conversation_history: list | None = None
    system_prompt: str | None = None
    api_messages: list | None = None
    final_response: str = ""
    turn_exit_reason: str = "completed"
    api_call_count: int = 0
    interrupted: bool = False
    token_accumulator: Any = None
    compression_result: dict | None = None


def _build_turn_exit_diagnostic(
    messages: list,
    final_response: str,
    turn_exit_reason: str,
    model: str,
    api_call_count: int,
    max_iterations: int,
    iteration_budget: Any,
    interrupted: bool,
    session_id: str,
) -> None:
    """Emit turn-exit diagnostic log (Phase 5).

    Extracted from run_conversation() — pure function: reads inputs,
    emits log, no side effects on agent state.
    """
    _last_msg_role = messages[-1].get("role") if messages else None
    _last_tool_name = None
    if _last_msg_role == "tool":
        for _m in reversed(messages):
            if _m.get("role") == "assistant" and _m.get("tool_calls"):
                _tcs = _m["tool_calls"]
                if _tcs and isinstance(_tcs[0], dict):
                    _last_tool_name = _tcs[-1].get("function", {}).get("name")
                break

    _turn_tool_count = sum(
        1 for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    _resp_len = len(final_response) if final_response else 0
    _budget_used = iteration_budget.used if iteration_budget else 0
    _budget_max = iteration_budget.max_total if iteration_budget else 0

    _diag_msg = (
        "Turn ended: reason=%s model=%s api_calls=%d/%d budget=%d/%d "
        "tool_turns=%d last_msg_role=%s response_len=%d session=%s"
    )
    _diag_args = (
        turn_exit_reason, model, api_call_count, max_iterations,
        _budget_used, _budget_max,
        _turn_tool_count, _last_msg_role, _resp_len,
        session_id or "none",
    )

    if _last_msg_role == "tool" and not interrupted:
        logger.warning(
            "Turn ended with pending tool result (agent may appear stuck). "
            + _diag_msg + " last_tool=%s",
            *_diag_args, _last_tool_name,
        )
    else:
        logger.info(_diag_msg, *_diag_args)

