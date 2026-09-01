#!/usr/bin/env python3
"""Benchmark: gateway stream_consumer hot path + delivery routing (G-21 / A3-8).

Gate procedure per the roadmap ("基准门控 … 无收益即关闭") and the G-14
precedent: measure the Python-side cost that a rust migration of
``gateway/stream_consumer.py``'s buffering/filtering and
``gateway/delivery.py``'s routing would remove, compare it against the
realistic gateway workload, and record a verdict.

The parsing-adjacent hot loop (SSE content accumulation) ALREADY runs in
Rust — ``rust-core/src/stream.rs`` StreamAccumulator measured ~4.1 GB/s in
the P0-2 baseline (docs/plans/bench-baseline.json: stream_parse). This
benchmark measures what is left in Python.

Usage:
    python scripts/bench/bench_stream_consumer.py [--deltas 2000] [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_DELTA_CHARS = 450  # typical LLM delta size (~a sentence)
_DELTA_INTERVAL_S = 0.05  # 20 deltas/s — a fast streaming model


def _make_consumer():
    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig

    adapter = SimpleNamespace()  # never touched by the CPU-only hot path
    config = StreamConsumerConfig()
    consumer = GatewayStreamConsumer(adapter, "bench-chat", config)
    consumer._queue.clear() if hasattr(consumer._queue, "clear") else None
    return consumer


def bench_filter_accumulate(deltas: int) -> dict:
    """CPU cost of the think-block state machine + buffering (per delta)."""
    consumer = _make_consumer()
    text = "w" * _DELTA_CHARS + " "

    samples = []
    total_chars = 0
    for i in range(deltas):
        start = time.perf_counter()
        consumer._filter_and_accumulate(text)
        elapsed = time.perf_counter() - start
        samples.append(elapsed)
        total_chars += len(text)

    total_s = sum(samples)
    return {
        "deltas": deltas,
        "chars": total_chars,
        "p50_us": statistics.median(samples) * 1e6,
        "p95_us": sorted(samples)[int(len(samples) * 0.95)] * 1e6,
        "mb_per_s": (total_chars / 1e6) / total_s if total_s else 0.0,
    }


def bench_on_delta_enqueue(deltas: int) -> dict:
    """Full on_delta path (queue put included) as the agent calls it."""
    consumer = _make_consumer()
    text = "w" * _DELTA_CHARS
    start = time.perf_counter()
    for _ in range(deltas):
        consumer.on_delta(text)
    elapsed = time.perf_counter() - start
    return {
        "deltas": deltas,
        "mb_per_s": (deltas * len(text) / 1e6) / elapsed if elapsed else 0.0,
        "us_per_delta": (elapsed / deltas) * 1e6,
    }


def bench_delivery_parse(iters: int) -> dict:
    """DeliveryTarget.parse — the routing pure function in delivery.py."""
    from gateway.delivery import DeliveryTarget

    samples = []
    target = "-1001234567890"
    for _ in range(iters):
        start = time.perf_counter()
        DeliveryTarget.parse(target)
        samples.append(time.perf_counter() - start)
    return {
        "iters": iters,
        "p50_us": statistics.median(samples) * 1e6,
        "p95_us": sorted(samples)[int(len(samples) * 0.95)] * 1e6,
    }


def estimate_cpu_share(filter_mb_per_s: float) -> float:
    """Estimated CPU share (%) at a realistic gateway streaming workload:
    20 deltas/s × 450 chars — platform edit rate limits dominate the loop."""
    if filter_mb_per_s <= 0:
        return 100.0
    bytes_per_delta = _DELTA_CHARS
    cpu_s_per_delta = bytes_per_delta / (filter_mb_per_s * 1e6)
    return (cpu_s_per_delta / _DELTA_INTERVAL_S) * 100


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deltas", type=int, default=2000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    filt = bench_filter_accumulate(args.deltas)
    enq = bench_on_delta_enqueue(args.deltas)
    route = bench_delivery_parse(100_000)
    cpu_share = estimate_cpu_share(filt["mb_per_s"])

    result = {
        "bench": "stream_consumer",
        "filter_accumulate": filt,
        "on_delta_enqueue": enq,
        "delivery_parse": route,
        "estimated_cpu_share_pct_at_20_delta_s": round(cpu_share, 4),
        "rust_baseline_stream_parse_mb_per_s": 4103.6,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
