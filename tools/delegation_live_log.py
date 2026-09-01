"""Live transcript side-channel for delegated subagents (G-12 / A2-3②).

One append-only log file per running subagent so a human (or a tail/RPC
consumer) can watch what a child is doing in real time:

    ~/.intellect/cache/delegation/live/<subagent_id>/transcript.log
    ~/.intellect/cache/delegation/live/<subagent_id>/manifest.json

Design contracts (Hermes-aligned):

- **Pure side channel**: the writer only OBSERVES progress events and
  writes files. It never touches message history or the prompt cache, and
  writer failures never propagate into the delegation (best-effort everywhere).
- **Append-per-write**: every line re-opens the file in append mode — no
  long-lived handle, so a crashed child still leaves everything it already
  wrote, and `tail -f` attaches trivially.
- **Every line is redacted**: the directory is readable by sandboxed
  processes by design; `redact_sensitive_text(force=True)` runs on every
  line, and if the redactor itself fails the WHOLE LINE is withheld.
- **Bounded**: per-event-type truncation budgets keep lines short; logs
  older than RETENTION_DAYS are pruned opportunistically at dispatch.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

LIVE_ROOT_SUBPATH = ("cache", "delegation", "live")
RETENTION_DAYS = 7

# Per-event truncation budgets (chars).
_BUDGETS = {
    "assistant": 600,
    "think": 300,
    "tool": 220,
    "result": 400,
    "user": 500,
    "stream": 4000,
}
_STREAM_BUFFER_FLUSH_CHARS = 4000


def live_transcript_root() -> Path:
    home = get_intellect_home()
    return home.joinpath(*LIVE_ROOT_SUBPATH)


def _redact_or_withhold(text: str, tool_name: str) -> str:
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text, force=True)
    except Exception:
        # Fail closed: never write an unredacted line, ever.
        logger.debug("live transcript redaction failed — line withheld")
        return f"[line withheld — redaction unavailable] ({tool_name})"


def _ts() -> str:
    return time.strftime("%H:%M:%S")


class LiveTranscriptWriter:
    """Per-subagent transcript writer. Best-effort; never raises."""

    def __init__(self, subagent_id: str, goal: str) -> None:
        self.subagent_id = subagent_id
        self.dir = live_transcript_root() / subagent_id
        self.path = self.dir / "transcript.log"
        self._disabled = False
        self._stream_buf: list[str] = []
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"# {time.strftime('%Y-%m-%d %H:%M:%S')}  subagent {subagent_id}\n"
                f"# goal: {_redact_or_withhold(goal, 'goal')}\n"
                f"# tail -f this file to follow the run\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self._disabled = True
            logger.debug("live transcript init failed: %s", exc)

    # ── writing ────────────────────────────────────────────────────────

    def _append(self, line: str) -> None:
        if self._disabled:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            logger.debug("live transcript write failed: %s", exc)
            self._disabled = True

    def _line(self, kind: str, text: str, budget: int) -> None:
        text = _redact_or_withhold((text or "").strip(), kind)
        if not text:
            return
        if len(text) > budget:
            text = text[:budget] + f"… (+{len(text) - budget} chars)"
        self._append(f"{_ts()} {kind:<9}| {text}")

    # ── event ingress (child progress callback shape) ──────────────────

    def observe(
        self,
        event_type: str,
        tool_name: str = None,
        preview: str = None,
        args=None,
        **kwargs,
    ) -> None:
        """Consume one child progress event. Mirrors the tool-progress shape."""
        if self._disabled:
            return
        try:
            self._observe(event_type, tool_name, preview, args, **kwargs)
        except Exception:
            logger.debug("live transcript observe failed", exc_info=True)

    def _observe(self, event_type, tool_name, preview, args, **kwargs) -> None:
        if event_type == "tool.start":
            self._line("tool", f"→ {tool_name} {str(preview or '')}", _BUDGETS["tool"])
        elif event_type == "tool.complete":
            dur = kwargs.get("duration_seconds", kwargs.get("duration"))
            suffix = f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else ""
            ok = "ok" if not kwargs.get("is_error") else "ERROR"
            self._line(
                "result",
                f"← {tool_name} {ok}{suffix}: {str(preview or '')}",
                _BUDGETS["result"],
            )
        elif event_type == "tool.progress":
            pass  # duplicate of start/complete for transcript purposes
        elif event_type in ("thinking.delta", "reasoning.delta"):
            self._buffer_stream("think: ", str(kwargs.get("text") or preview or ""))
        elif event_type in ("assistant.delta", "message.delta", "subagent.text"):
            self._buffer_stream("say: ", str(kwargs.get("text") or preview or ""))
        elif event_type == "subagent.start":
            self._append(f"{_ts()} start    | subagent {self.subagent_id}")
        elif event_type == "subagent.complete":
            self.finalize(str(kwargs.get("status") or "completed"))

    def _buffer_stream(self, prefix: str, text: str) -> None:
        """Accumulate streaming deltas; flush as one line when large enough."""
        self._stream_buf.append(text)
        pending = "".join(self._stream_buf)
        if len(pending) >= _STREAM_BUFFER_FLUSH_CHARS:
            self._line("assistant", prefix + pending, _BUDGETS["assistant"])
            self._stream_buf.clear()

    def flush_stream(self) -> None:
        if self._stream_buf:
            pending = "".join(self._stream_buf)
            self._stream_buf.clear()
            self._line("assistant", "say: " + pending, _BUDGETS["assistant"])

    def kickoff(self, task: str) -> None:
        self._line("user", task, _BUDGETS["user"])

    def finalize(self, status: str, exit_reason: str = "") -> None:
        self.flush_stream()
        self._append(f"{_ts()} final    | {status}"
                     + (f" ({exit_reason})" if exit_reason else ""))

    def manifest_entry(self) -> Dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "log": str(self.path),
            "disabled": self._disabled,
        }


def create_live_transcript(subagent_id: str, goal: str) -> Optional[LiveTranscriptWriter]:
    """Create a writer for one subagent. Returns None on init failure."""
    writer = LiveTranscriptWriter(subagent_id, goal)
    if writer._disabled:
        return None
    return writer


def wrap_progress_callback(inner_cb, writer: Optional[LiveTranscriptWriter]):
    """Tee progress events into *writer* while preserving *inner_cb* exactly.

    Returns the original callback unchanged when there is no writer, so
    dispatch sites can wrap unconditionally. Writer failures never reach
    the child (observe() swallows everything).
    """
    if writer is None:
        return inner_cb

    def wrapped(event_type: str, tool_name: str = None, preview: str = None,
                args=None, **kwargs):
        writer.observe(event_type, tool_name, preview, args, **kwargs)
        if inner_cb is not None:
            return inner_cb(event_type, tool_name, preview, args, **kwargs)
        return None

    return wrapped


def prune_stale_live_dirs(days: int = RETENTION_DAYS) -> int:
    """Remove live-transcript dirs older than *days*. Opportunistic, silent."""
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        root = live_transcript_root()
        if not root.is_dir():
            return 0
        for entry in root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    import shutil

                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


def manifest_path(subagent_id: str) -> Path:
    return live_transcript_root() / subagent_id / "manifest.json"
