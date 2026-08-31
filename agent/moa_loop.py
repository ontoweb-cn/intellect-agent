"""MoA orchestration loop — parallel reference models + aggregator (HP-302).

The ``MoARunner`` replaces the normal OpenAI/Anthropic client when
``api_mode == "moa"``.  Its ``.chat.completions.create()``-compatible
interface fans out the user prompt to N reference models in parallel,
then has a synthesizer model produce the final answer from all
reference responses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

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
    """Extract text content from a call_llm result, handling both dict and str."""
    if isinstance(result, dict):
        return str(result.get("content") or result.get("text") or "")
    if isinstance(result, str):
        return result
    return ""


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
        self._references: list[dict[str, str]] = preset.get("references", [])
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

        Returns ``{model, provider, content, latency_ms, success}``.
        """
        t0 = time.monotonic()
        provider = ref.get("provider", "")
        model = ref.get("model", "")
        messages = [{"role": "system", "content": _REFERENCE_SYSTEM_PROMPT}, *ref_messages]
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
                "error": str(exc),
            }

    def _build_aggregator_messages(
        self, user_message: str, ref_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Build the aggregator prompt: system + per-ref context + user."""
        system = (
            "You are an expert synthesizer. Below are responses from multiple "
            "AI models to the same user question. Synthesize a comprehensive, "
            "accurate answer that combines the best insights from all responses. "
            "Resolve any contradictions. Do not mention the models by name — "
            "just produce the best possible answer."
        )
        ref_blocks = []
        for i, rr in enumerate(ref_results, 1):
            if rr.get("success") and rr.get("content"):
                ref_blocks.append(
                    f"--- Reference Model {i} ---\n{rr['content']}\n"
                )
        context = "\n".join(ref_blocks) if ref_blocks else "(no reference responses)"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Reference responses:\n\n{context}\n\nUser question: {user_message}\n\nSynthesize the best answer:"},
        ]

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

        # Extract the last user message as the prompt for the aggregator.
        user_message = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_message = str(m.get("content", ""))
                break
        if not user_message:
            user_message = str(messages[-1].get("content", "")) if messages else ""

        # Build the denoised advisory view once for all references — advisors see
        # a clean read-only transcript (no system boilerplate, no tool-role
        # messages), not just the last user message.
        ref_messages = _reference_messages(messages)

        # Phase 1 — fan out to reference models
        tasks = [self._run_single_reference(r, ref_messages) for r in self._references]
        ref_results = await asyncio.gather(*tasks)

        successful = [r for r in ref_results if r.get("success")]
        if len(successful) < MIN_SUCCESSFUL_REFERENCES:
            raise RuntimeError(
                f"MoA: only {len(successful)}/{len(self._references)} reference "
                f"models succeeded (need at least {MIN_SUCCESSFUL_REFERENCES})"
            )

        agg_provider = self._aggregator.get("provider", "")
        agg_model = self._aggregator.get("model", "")

        # Phase 2 — aggregator (skipped when the user interrupted during the
        # fan-out: its +1 call is wasted on a turn that's being abandoned).
        if self._interrupted():
            best = successful[0]
            content = (
                "[Interrupted — showing best reference response]\n\n"
                f"{best['content']}"
            )
            api_calls = len(ref_results)  # N references, no aggregator
        else:
            agg_messages = self._build_aggregator_messages(user_message, successful)
            try:
                agg_result = await asyncio.to_thread(
                    call_llm,
                    messages=agg_messages,
                    provider=agg_provider,
                    model=agg_model,
                    temperature=self._agg_temp,
                    task="moa_aggregator",
                )
                content = _extract_content(agg_result)
            except Exception as exc:
                logger.warning("moa_loop: aggregator failed: %s", exc)
                # Fall back to best reference response
                best = successful[0]
                content = f"[Aggregator unavailable — showing best reference response]\n\n{best['content']}"
            api_calls = len(ref_results) + 1  # N references + 1 aggregator

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

        # Build an OpenAI-response-shaped result
        return _FakeResponse(content, ref_results, total_ms, api_calls)


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

    def __init__(self, content: str, ref_results: list, total_ms: float, api_calls: int = 1):
        self.choices = [_FakeChoice(content)]
        self._moa_ref_results = ref_results
        self._moa_total_ms = total_ms
        self._moa_api_calls = api_calls
        # Minimal usage info so the token tracker sees N+1 calls
        self.usage = type("_Usage", (), {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        })()


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None
