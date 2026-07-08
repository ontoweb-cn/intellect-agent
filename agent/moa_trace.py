"""MoA call tracing — save and format traces for WebUI display (HP-302)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)


@dataclass
class MoaTrace:
    """One MoA invocation trace."""

    preset_name: str
    reference_results: list[dict[str, Any]] = field(default_factory=list)
    aggregator_model: str = ""
    aggregator_content: str = ""
    total_latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset_name,
            "references": [
                {
                    "model": r.get("model", ""),
                    "provider": r.get("provider", ""),
                    "content_preview": (r.get("content", "") or "")[:200],
                    "latency_ms": r.get("latency_ms", 0),
                    "success": r.get("success", False),
                }
                for r in self.reference_results
            ],
            "aggregator": {
                "model": self.aggregator_model,
                "content_preview": self.aggregator_content[:200],
            },
            "total_latency_ms": self.total_latency_ms,
            "timestamp": self.timestamp,
        }


def _trace_dir() -> Path:
    return get_intellect_home() / "moa" / "traces"


def save_trace(trace: MoaTrace, session_id: str = "") -> None:
    """Persist a trace to the session's trace directory."""
    try:
        d = _trace_dir()
        d.mkdir(parents=True, exist_ok=True)
        ts = int(trace.timestamp)
        sid = session_id or "unknown"
        fname = f"{sid}-{ts}.json"
        (d / fname).write_text(
            json.dumps(trace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("moa_trace: failed to save trace", exc_info=True)


def format_trace_for_webui(trace_data: dict[str, Any]) -> dict[str, Any]:
    """Convert a trace dict into the shape the WebUI session detail panel expects."""
    refs = trace_data.get("references", [])
    agg = trace_data.get("aggregator", {})
    return {
        "type": "moa_trace",
        "preset": trace_data.get("preset", "default"),
        "referenceCount": len(refs),
        "successfulCount": sum(1 for r in refs if r.get("success")),
        "referenceModels": [
            f"{r.get('provider', '?')}/{r.get('model', '?')}" for r in refs
        ],
        "aggregatorModel": agg.get("model", ""),
        "totalLatencyMs": trace_data.get("total_latency_ms", 0),
        "timestamp": trace_data.get("timestamp", 0),
    }
