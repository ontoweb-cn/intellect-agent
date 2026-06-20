"""Per-agent iteration budget — thread-safe consume/refund counter.

Extracted from ``run_agent.py``.  Each ``AIAgent`` instance (parent or
subagent) holds an :class:`IterationBudget`; the parent's cap comes from
``max_iterations`` (default 90), each subagent's cap comes from
``delegation.max_iterations`` (default 50).

``run_agent`` re-exports ``IterationBudget`` so existing
``from run_agent import IterationBudget`` imports keep working unchanged.

Since v0.6.2 the implementation runs in Rust (``intellect_community_core``).
"""

from __future__ import annotations

from intellect_rust import IterationBudget as _RustBudget

IterationBudget = _RustBudget

__all__ = ["IterationBudget"]
