"""MoA orchestration loop — parallel reference models + aggregator (HP-302).

The ``MoARunner`` replaces the normal OpenAI/Anthropic client when
``api_mode == "moa"``.  Its ``.chat.completions.create()``-compatible
interface fans out the user prompt to N reference models in parallel,
then has a synthesizer model produce the final answer from all
reference responses.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from agent.auxiliary_client import call_llm
from tools.interrupt import is_interrupted

try:
    from agent.moa_trace import MoaTrace, save_trace
except ImportError:
    MoaTrace = None  # type: ignore[assignment]
    save_trace = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

MIN_SUCCESSFUL_REFERENCES = 1


def _extract_content(result: Any) -> str:
    """Extract text content from a call_llm result (dict, str, or raw response)."""
    if isinstance(result, dict):
        return str(result.get("content") or result.get("text") or "")
    if isinstance(result, str):
        return result
    # Raw OpenAI-style response object.
    try:
        choices = getattr(result, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            return str(getattr(msg, "content", "") or "")
    except (IndexError, TypeError, AttributeError):
        pass
    return ""


def _extract_tool_calls(result: Any):
    """Extract tool_calls from a call_llm result (dict or raw response)."""
    if isinstance(result, dict):
        return result.get("tool_calls") or None
    try:
        choices = getattr(result, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            return getattr(msg, "tool_calls", None) or None
    except (IndexError, TypeError, AttributeError):
        pass
    return None


def _extract_usage(result: Any) -> dict:
    """Best-effort token usage from a call_llm result (dict or raw response).

    Returns ``{prompt_tokens, completion_tokens, total_tokens}``, all-zero when
    the backend reported no usage.
    """
    usage = None
    if isinstance(result, dict):
        usage = result.get("usage")
    else:
        try:
            usage = getattr(result, "usage", None)
        except Exception:
            usage = None
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _tok(obj, *keys):
        for k in keys:
            v = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 0
        return 0

    prompt = _tok(usage, "prompt_tokens", "input_tokens")
    completion = _tok(usage, "completion_tokens", "output_tokens")
    total = _tok(usage, "total_tokens") or (prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


# Conservative default context window for reference models whose window we can't
# resolve (per-model resolution is a future refinement).  Output reserve keeps
# room for the advisor's own reply.
_REFERENCE_DEFAULT_CONTEXT = 128_000
_REFERENCE_OUTPUT_RESERVE = 8192


def _trim_reference_messages(
    messages: list[dict[str, str]], max_tokens: int | None = None
) -> list[dict[str, str]]:
    """Drop the oldest (user, assistant) pairs from the front to fit a reference
    model's context budget.  No-op when the view already fits."""
    if not messages:
        return messages
    budget = (max_tokens or _REFERENCE_DEFAULT_CONTEXT) - _REFERENCE_OUTPUT_RESERVE
    budget = max(budget, 1024)  # floor: always allow at least ~1k tokens
    try:
        from agent.model_metadata import estimate_messages_tokens_rough
        if estimate_messages_tokens_rough(messages) <= budget:
            return messages
    except Exception:
        return messages

    trimmed = list(messages)
    while len(trimmed) > 2:
        try:
            if estimate_messages_tokens_rough(trimmed) <= budget:
                break
        except Exception:
            break
        # Drop the oldest user + its assistant partner (preserve user-first +
        # strict alternation + the user-ending tail).
        trimmed.pop(0)
        if trimmed and trimmed[0]["role"] == "assistant":
            trimmed.pop(0)
    return trimmed


def _turn_signature(messages: list) -> str:
    """Stable per-user-turn signature for fan-out cadence caching.

    Hashes the conversation prefix up to (and including) the last user message —
    the Hermes "turn_prefix" — so two turns sharing the same final text but with
    different prior history do not collide.  Tool results appended after the last
    user message are excluded, keeping the signature stable across the tool
    iterations of a single turn.
    """
    import json
    last_user_idx = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user_idx = i
    prefix = messages[:last_user_idx + 1] if last_user_idx >= 0 else messages
    payload = json.dumps(prefix, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _task_signature(messages: list) -> str:
    """Compression-stable key: hash of the last user message's content.

    Context compression summarizes the older turns but preserves the last user
    message (the task), so this key survives compression — used for peel/rebase to
    reuse advisor guidance across a compression boundary.
    """
    content = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = _content_to_text(m.get("content"))
            break
    return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()


# ── Advisory view (M2) ──────────────────────────────────────────────────────

_REFERENCE_SYSTEM_PROMPT = (
    "You are a reference advisor inside a Mixture-of-Agents (MoA) pipeline. "
    "You are NOT the acting agent and must NOT execute anything.\n\n"
    "You cannot and must not: call tools, run commands, browse, access files or "
    "repositories, or fetch URLs. You only receive a read-only view of the "
    "conversation so far.\n\n"
    "CRITICAL — describe, do not fabricate. Say 'based on the error pattern, curl "
    "would likely return 404' — never 'I ran curl and got 404' or 'I downloaded it'. "
    "You have run nothing, so do not claim you did.\n\n"
    "Give your judgment and a concrete recommendation directly — no preamble, no "
    "tool disclaimer. Your reply is private guidance for the aggregator model, not "
    "an answer shown to the user."
)


def _coerce_fanout(mode: Any) -> tuple[str, int]:
    """Normalize a preset ``fanout`` value to (mode, n).

    Accepts "user_turn" (default), "per_iteration", "every_n:<N>", or
    {"mode": "every_n", "n": N}. N=1 folds to per_iteration; garbage falls
    back to user_turn.
    """
    try:
        if isinstance(mode, dict):
            n = int(mode.get("n", 1) or 1)
            return ("per_iteration", 0) if n <= 1 else ("every_n", n)
        text = str(mode or "user_turn").strip().lower()
        if text == "per_iteration":
            return ("per_iteration", 0)
        if text.startswith("every_n"):
            n_part = text.split(":", 1)[1] if ":" in text else ""
            if not n_part.strip().isdigit():
                return ("user_turn", 0)  # garbage N falls back to default
            n = int(n_part)
            return ("per_iteration", 0) if n <= 1 else ("every_n", n)
    except (TypeError, ValueError):
        pass
    return ("user_turn", 0)


def _content_to_text(content: Any) -> str:
    """Flatten message content (str or multimodal list) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                _type = part.get("type")
                if _type == "text":
                    parts.append(str(part.get("text", "")))
                elif _type == "image_url":
                    parts.append("[image]")
                elif _type:
                    # Some other known block type (e.g. Anthropic tool_use) —
                    # surface the type rather than the raw payload.
                    parts.append(str(_type))
                # else: type is None/empty — skip (not noise the advisor needs).
            elif part is not None:
                parts.append(str(part))
        return " ".join(p for p in parts if p).strip()
    if content is None:
        return ""
    return str(content)


def _render_tool_calls(tool_calls: list) -> str:
    """Render assistant tool_calls as '[called tool: name(args)]' text lines."""
    lines = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name", "?")
        args = fn.get("arguments", "")
        if not isinstance(args, str):
            args = str(args)
        args_preview = args if len(args) <= 120 else args[:120] + "…"
        lines.append(f"[called tool: {name}({args_preview})]")
    return "\n".join(lines)


def _reference_messages(messages: list) -> list[dict[str, str]]:
    """Build a denoised, text-only view of the conversation for the advisors.

    Drops the system prompt (boilerplate is noise to advisors), renders assistant
    tool_calls as text lines, folds tool results into the preceding assistant turn
    as short previews, and emits zero tool-role messages / tool_calls arrays (strict
    providers 400 on orphan tool messages).  The returned view always ends with a
    user turn (Anthropic treats a trailing assistant as a prefill).
    """
    view: list[dict[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        tool_calls = m.get("tool_calls")
        if role == "system":
            continue
        if role == "assistant":
            text = _content_to_text(content)
            if tool_calls:
                rendered = _render_tool_calls(tool_calls)
                text = f"{text}\n{rendered}" if text else rendered
            if text:
                if view and view[-1]["role"] == "assistant":
                    view[-1]["content"] += f"\n{text}"
                else:
                    view.append({"role": "assistant", "content": text})
        elif role == "user":
            text = _content_to_text(content)
            if text:
                if view and view[-1]["role"] == "user":
                    view[-1]["content"] += f"\n{text}"
                else:
                    view.append({"role": "user", "content": text})
        elif role == "tool":
            preview = _content_to_text(content)
            preview = preview if len(preview) <= 400 else preview[:400] + "…"
            if view and view[-1]["role"] == "assistant":
                view[-1]["content"] += f"\n[tool result: {preview}]"
            # else: orphan tool result (no preceding assistant) — drop it, so we
            # never fabricate an assistant-first transcript that strict
            # providers (Anthropic) reject.
    if not view or view[-1]["role"] != "user":
        view.append({
            "role": "user",
            "content": (
                "Assess the current task state and give your recommendation on "
                "how the acting agent should proceed."
            ),
        })
    return view


class MoaRunner:
    """Drop-in replacement for an OpenAI-compatible client when api_mode="moa".

    Implements ``.chat.completions.create(**kwargs)`` so the existing
    ``interruptible_api_call`` / ``interruptible_streaming_api_call``
    code paths can dispatch to MoA without changing their call shape.
    """

    def __init__(self, preset: dict[str, Any], *, agent: Any = None):
        self._preset = preset
        self._agent = agent  # optional AIAgent — enables interrupt-aware short-circuit
        # G-13/A2-4: enabled:false advisors are filtered at construction so
        # they never fan out and never appear in unavailable-advisor notes.
        self._references: list[dict[str, str]] = [
            r for r in preset.get("references", []) if r.get("enabled", True)
        ]
        self._aggregator: dict[str, str] = preset.get("aggregator", {})
        self._ref_temp: float = float(preset.get("reference_temperature", 0.6))
        self._agg_temp: float = float(preset.get("aggregator_temperature", 0.4))

        # Expose a .chat.completions namespace so callers can do
        #   runner.chat.completions.create(**kwargs)
        self.chat = _ChatNamespace(self)

    def _interrupted(self) -> bool:
        """True if the user interrupted this turn.

        Prefer the agent's plain ``_interrupt_requested`` flag — it is
        thread-independent (set by ``AIAgent.interrupt()``).  ``is_interrupted()``
        is per-thread and MoA runs on a worker thread whose id the interrupt
        signal never targets, so it would always return False here in production.
        """
        if self._agent is not None:
            return bool(getattr(self._agent, "_interrupt_requested", False))
        return is_interrupted()

    # ── helpers ──────────────────────────────────────────────────────────

    async def _run_single_reference(
        self, ref: dict[str, str], ref_messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Call one reference model with the denoised advisory view.

        Returns ``{model, provider, content, latency_ms, success, usage}`` (plus
        ``failed_label``/``error`` on failure).
        """
        t0 = time.monotonic()
        provider = ref.get("provider", "")
        model = ref.get("model", "")
        _view = _trim_reference_messages(ref_messages)
        messages = [{"role": "system", "content": _REFERENCE_SYSTEM_PROMPT}, *_view]
        try:
            result = await asyncio.to_thread(
                call_llm,
                messages=messages,
                provider=provider,
                model=model,
                temperature=self._ref_temp,
                task="moa_reference",
            )
            content = _extract_content(result)
            latency = (time.monotonic() - t0) * 1000
            return {
                "model": model,
                "provider": provider,
                "content": content,
                "latency_ms": round(latency, 1),
                "success": bool(content),
                "usage": _extract_usage(result),
            }
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            logger.debug("moa_loop: reference %s/%s failed: %s", provider, model, exc)
            return {
                "model": model,
                "provider": provider,
                "content": "",
                "latency_ms": round(latency, 1),
                "success": False,
                "failed_label": f"{provider}/{model}",
                "error": str(exc),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    def _emit_moa_progress(self, done: int, total: int) -> None:
        """Emit ``moa.progress`` + per-advisor ``moa.reference`` events.

        Display-only side channel (tool_progress_callback) — nothing here
        enters the message history or touches the prompt cache. Best-effort.
        """
        agent = self._agent
        if agent is None:
            return
        cb = getattr(agent, "tool_progress_callback", None)
        if not callable(cb):
            return

        def _emit(payload: dict) -> None:
            try:
                cb("moa", payload)
            except Exception:
                logger.debug("moa progress emit failed", exc_info=True)

        for i, rr in enumerate(getattr(self, "_last_ref_results", None) or [], 1):
            label = (
                f"{rr.get('provider', '?')}/{rr.get('model', '?')}"
                if isinstance(rr, dict) else "?"
            )
            _emit({
                "event": "moa.reference",
                "index": i,
                "count": total,
                "label": label,
                "ok": bool(rr.get("success")),
            })
        _emit({"event": "moa.progress", "refs_done": done, "refs_total": total})

    def _build_aggregator_messages(
        self, messages: list[dict[str, Any]], ref_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Build the aggregator prompt: system + conversation + guidance-at-end.

        The aggregator is the acting agent — it sees its own system prompt, the
        full conversation (user turns + its prior tool calls/results, minus the
        main agent's system prompt), and the reference guidance appended at the
        END (so the conversation prefix stays stable across tool iterations).
        """
        system = (
            "You are the acting agent in a Mixture-of-Agents (MoA) pipeline. "
            "Complete the user's task using the tools available to you. "
            "Reference-model advice is appended at the end of the conversation — "
            "use it to inform your actions, but do the work yourself. Do not "
            "mention the reference models by name."
        )
        ref_blocks = []
        for i, rr in enumerate(ref_results, 1):
            if rr.get("success") and rr.get("content"):
                ref_blocks.append(
                    f"--- Reference Model {i} ---\n{rr['content']}\n"
                )
        context = "\n".join(ref_blocks) if ref_blocks else "(no reference responses)"
        # Surface unavailable advisors (loud policy) so the aggregator knows
        # which reference models contributed nothing.
        failed = [
            rr.get("failed_label") or f"{rr.get('provider')}/{rr.get('model')}"
            for rr in ref_results if not rr.get("success")
        ]
        if failed:
            context += (
                f"\n\nNote: the following reference models were unavailable and "
                f"provided no input: {', '.join(failed)}."
            )

        conversation = [
            m for m in messages
            if isinstance(m, dict) and m.get("role") != "system"
        ]
        guidance = f"Reference responses:\n\n{context}\n\nSynthesize the best answer:"

        out = [{"role": "system", "content": system}, *conversation]
        if out and out[-1]["role"] == "user":
            # Merge guidance into the trailing user turn to avoid consecutive
            # same-role messages (strict providers reject them).
            out[-1] = {
                "role": "user",
                "content": f"{out[-1].get('content', '')}\n\n{guidance}",
            }
        else:
            out.append({"role": "user", "content": guidance})
        return out

    # ── main entry ───────────────────────────────────────────────────────

    async def run(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Execute the MoA pipeline and return a response-like object.

        The returned object has ``.choices[0].message.content`` so it
        quacks like an OpenAI API response.
        """
        t0 = time.monotonic()

        # Short-circuit before the fan-out if the user already interrupted —
        # don't spend N reference calls on a turn that's being abandoned.
        if self._interrupted():
            raise RuntimeError("MoA interrupted before reference fan-out")

        # Build the denoised advisory view once for all references — advisors see
        # a clean read-only transcript (no system boilerplate, no tool-role
        # messages), not just the last user message.
        ref_messages = _reference_messages(messages)

        # Phase 1 — fan out to reference models (user_turn cadence: reuse the
        # previous turn's results across tool iterations of the same user turn,
        # saving N reference calls each subsequent iteration).  The cache lives
        # on the agent so it persists across the fresh MoaRunner created per API
        # call.
        signature = _turn_signature(messages)
        task_signature = _task_signature(messages)
        _cache = getattr(self._agent, "_moa_fanout_cache", None) if self._agent is not None else None
        _fanout_cached = False

        # ── Cadence state machine (G-13/A2-4 every_n) ───────────────────
        # Modes: user_turn (default — cache hit while the turn signature is
        # unchanged), per_iteration (never reuse), every_n:N (re-fan-out
        # only every N *state advances*; an advance is the advisory view
        # actually growing — stream retries don't count). Off-cadence calls
        # pin the cache key to the last on-cadence signature so they REUSE
        # that guidance instead of re-running advisors or re-billing.
        _fanout_mode, _fanout_n = _coerce_fanout(self._preset.get("fanout"))
        _cadence = getattr(self._agent, "_moa_fanout_cadence", None) if self._agent is not None else None
        if _fanout_mode == "every_n" and _cadence is not None:
            if _cadence.get("turn_sig") != signature:
                # New user turn: reset the cadence counter; this call IS the
                # turn's first fan-out (on-cadence by definition).
                _cadence["turn_sig"] = signature
                _cadence["count"] = 0
                _cadence["state_sig"] = task_signature
                _cadence.pop("pinned_key", None)
            elif _cadence.get("state_sig") != task_signature:
                # Advisory state actually advanced (tool results landed).
                _cadence["state_sig"] = task_signature
                _cadence["count"] = _cadence.get("count", 0) + 1
            _cadence["count"] = _cadence.get("count", 0)
            on_cadence = (_cadence["count"] % max(1, _fanout_n)) == 0
            if not on_cadence:
                pinned = _cadence.get("pinned_key")
                if (
                    pinned is not None
                    and _cache is not None
                    and _cache.get("signature") == pinned
                    and _cache.get("ref_results") is not None
                ):
                    ref_results = _cache["ref_results"]
                    _fanout_cached = True
                    _cache["message_count"] = max(
                        _cache.get("message_count", 0), len(messages)
                    )
                    # Fall through to guidance assembly with cached results.

        if (
            _fanout_cached
            or (
                _cache is not None
                and _cache.get("signature") == signature
                and _cache.get("ref_results") is not None
            )
        ):
            ref_results = ref_results if _fanout_cached else _cache["ref_results"]
            _fanout_cached = True
            # Track the peak transcript size so a later compression (which
            # shrinks the transcript below this peak) stays detectable.
            _cache["message_count"] = max(_cache.get("message_count", 0), len(messages))
        elif (
            _cache is not None
            and _cache.get("task_signature") == task_signature
            and _cache.get("ref_results") is not None
            and len(messages) <= _cache.get("message_count", 0)
        ):
            # peel/rebase: compression shrank the transcript but kept the task,
            # so reuse the advisor guidance and rebase the cache onto the
            # compressed transcript (compression doesn't change advisor results).
            ref_results = _cache["ref_results"]
            _fanout_cached = True
            _cache["signature"] = signature
            _cache["message_count"] = len(messages)
        else:
            tasks = [self._run_single_reference(r, ref_messages) for r in self._references]
            ref_results = await asyncio.gather(*tasks)
            if self._agent is not None:
                self._agent._moa_fanout_cache = {
                    "signature": signature,
                    "task_signature": task_signature,
                    "message_count": len(messages),
                    "ref_results": ref_results,
                }
                # every_n: this was an on-cadence fan-out — future off-cadence
                # calls pin to THIS cache entry for guidance reuse.
                if _fanout_mode == "every_n" and _cadence is not None:
                    _cadence["pinned_key"] = signature
            self._last_ref_results = ref_results

        successful = [r for r in ref_results if r.get("success")]
        self._emit_moa_progress(len(successful), len(self._references))
        if len(successful) < MIN_SUCCESSFUL_REFERENCES:
            raise RuntimeError(
                f"MoA: only {len(successful)}/{len(self._references)} reference "
                f"models succeeded (need at least {MIN_SUCCESSFUL_REFERENCES})"
            )

        agg_provider = self._aggregator.get("provider", "")
        agg_model = self._aggregator.get("model", "")
        agg_tool_calls = None
        agg_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # Reference calls actually made this turn (0 when the fan-out was reused).
        _ref_calls = 0 if _fanout_cached else len(ref_results)

        # Phase 2 — aggregator (skipped when the user interrupted during the
        # fan-out: its +1 call is wasted on a turn that's being abandoned).
        if self._interrupted():
            best = successful[0]
            content = (
                "[Interrupted — showing best reference response]\n\n"
                f"{best['content']}"
            )
            api_calls = _ref_calls  # references actually called, no aggregator
        else:
            agg_messages = self._build_aggregator_messages(messages, ref_results)
            # M3 — apply Anthropic prompt caching.  The conversation prefix is
            # stable across tool iterations (guidance is appended at the end), so
            # cache_control breakpoints make the prefix reusable and cut input
            # cost ~75% for Anthropic aggregators.
            if agg_provider == "anthropic" or (agg_model or "").lower().startswith("claude"):
                try:
                    from agent.prompt_caching import apply_anthropic_cache_control
                    agg_messages = apply_anthropic_cache_control(
                        agg_messages,
                        cache_ttl=getattr(self._agent, "_cache_ttl", "5m"),
                        native_anthropic=(agg_provider == "anthropic"),
                    )
                except Exception:
                    pass
            # The aggregator is the action model — forward the full tool schema
            # so it can emit tool_calls, which the normal agent loop executes.
            tools = kwargs.get("tools")
            try:
                agg_result = await asyncio.to_thread(
                    call_llm,
                    messages=agg_messages,
                    provider=agg_provider,
                    model=agg_model,
                    temperature=self._agg_temp,
                    tools=tools,
                    task="moa_aggregator",
                )
                content = _extract_content(agg_result)
                agg_tool_calls = _extract_tool_calls(agg_result)
                agg_usage = _extract_usage(agg_result)
            except Exception as exc:
                logger.warning("moa_loop: aggregator failed: %s", exc)
                # Fall back to best reference response
                best = successful[0]
                content = f"[Aggregator unavailable — showing best reference response]\n\n{best['content']}"
            api_calls = _ref_calls + 1  # references actually called + 1 aggregator

        total_ms = round((time.monotonic() - t0) * 1000, 1)

        # Save trace (both normal and interrupted paths)
        if MoaTrace is not None and save_trace is not None:
            try:
                trace = MoaTrace(
                    preset_name=kwargs.get("_moa_preset_name", "default"),
                    reference_results=ref_results,
                    aggregator_model=f"{agg_provider}/{agg_model}",
                    aggregator_content=content,
                    total_latency_ms=total_ms,
                )
                session_id = kwargs.get("_session_id", "")
                save_trace(trace, session_id)
            except Exception:
                logger.debug("moa_loop: trace save failed", exc_info=True)

        # Aggregate token usage across all slots (references + aggregator) so the
        # MoA turn's token accounting is visible instead of all-zero.  When the
        # fan-out was reused from the cache, the references made no calls this
        # turn, so their (already-counted) usage must NOT be re-summed.
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if not _fanout_cached:
            for rr in ref_results:
                u = rr.get("usage") or {}
                total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += u.get("completion_tokens", 0)
                total_usage["total_tokens"] += u.get("total_tokens", 0)
        total_usage["prompt_tokens"] += agg_usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += agg_usage.get("completion_tokens", 0)
        total_usage["total_tokens"] += agg_usage.get("total_tokens", 0)

        # Build an OpenAI-response-shaped result
        return _FakeResponse(content, ref_results, total_ms, api_calls, tool_calls=agg_tool_calls, usage=total_usage)


class _ChatNamespace:
    """Minimal ``.chat.completions`` namespace so the runner quacks like
    an OpenAI client object for the agent loop's call sites."""

    def __init__(self, runner: MoaRunner):
        self.completions = _CompletionsNamespace(runner)


class _CompletionsNamespace:
    def __init__(self, runner: MoaRunner):
        self._runner = runner

    async def create(self, messages, **kwargs):
        return await self._runner.run(messages, **kwargs)


class _FakeResponse:
    """A minimal object that the response normalizer can read."""

    def __init__(self, content: str, ref_results: list, total_ms: float, api_calls: int = 1, tool_calls=None, usage=None):
        self.choices = [_FakeChoice(content, tool_calls)]
        self._moa_ref_results = ref_results
        self._moa_total_ms = total_ms
        self._moa_api_calls = api_calls
        # Aggregated token usage across all slots so the token tracker sees the
        # real MoA turn cost instead of all-zero.
        usage = usage or {}
        self.usage = type("_Usage", (), {
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        })()


class _FakeChoice:
    def __init__(self, content: str, tool_calls=None):
        self.message = _FakeMessage(content, tool_calls)


class _FakeMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
