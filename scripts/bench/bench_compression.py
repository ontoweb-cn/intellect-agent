#!/usr/bin/env python3
"""Benchmark: token estimation timing (G-06 baseline).

Builds a synthetic 200-turn conversation (with tool results) and times
``estimate_request_tokens_rough`` — the estimator that drives compression
triggering and the G-04 anchor delta. NOTE: this does NOT time the
compression itself, only the estimation step; add a compression-entry
benchmark when A2-1 lands if entry cost becomes decision-relevant.
Prints one JSON line.

Usage: python scripts/bench/bench_compression.py [--turns 200] [--iters 10]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def make_messages(turns: int) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "You are a helpful agent." * 20}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"question {i}: " + "q" * 300})
        msgs.append({
            "role": "assistant",
            "content": f"answer {i}",
            "tool_calls": [{
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": "terminal", "arguments": '{"cmd": "ls -la"}'},
            }],
        })
        msgs.append({
            "role": "tool",
            "tool_call_id": f"call_{i}",
            "content": f"result {i}: " + "r" * 1500,
        })
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=200)
    ap.add_argument("--iters", type=int, default=10)
    args = ap.parse_args()

    from agent.model_metadata import estimate_request_tokens_rough

    msgs = make_messages(args.turns)
    samples = []
    tokens = 0
    for _ in range(args.iters):
        t = time.perf_counter()
        tokens = estimate_request_tokens_rough(msgs)
        samples.append((time.perf_counter() - t) * 1000)

    out = {
        "bench": "token_estimate",
        "turns": args.turns,
        "messages": len(msgs),
        "iters": args.iters,
        "estimated_tokens": tokens,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(statistics.quantiles(samples, n=min(20, args.iters))[-1], 3),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
