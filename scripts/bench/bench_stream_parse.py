#!/usr/bin/env python3
"""Benchmark: StreamAccumulator content-ingestion throughput (G-21/G-03 baseline).

Feeds a synthetic ~1MB stream through the rust StreamAccumulator's
``add_content`` and times ingestion. NOTE: this measures content-append
throughput of the accumulator API, NOT full SSE frame parsing
(``data:``-line splitting happens in the caller today) — keep that in mind
when comparing across G-21 changes. Falls back cleanly when the extension
is absent.

Usage: python scripts/bench/bench_stream_parse.py [--chunks 2000] [--iters 10]
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


def make_chunks(n: int) -> list[str]:
    payload = json.dumps(
        {"choices": [{"delta": {"content": "x" * 400}}]}, ensure_ascii=False
    )
    return [f"data: {payload}\n\n".encode() for _ in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=2000)
    ap.add_argument("--iters", type=int, default=10)
    args = ap.parse_args()

    chunks = make_chunks(args.chunks)
    total_bytes = sum(len(c) for c in chunks)

    try:
        from intellect_rust import StreamAccumulator  # noqa: F401
        have_rust = True
    except ImportError:
        have_rust = False

    out: dict = {
        "bench": "stream_parse",
        "chunks": args.chunks,
        "total_bytes": total_bytes,
        "iters": args.iters,
        "rust_available": have_rust,
        "mode": "rust" if have_rust else "python-fallback (accumulator absent; timing chunk gen only)",
    }

    if have_rust:
        samples = []
        for _ in range(args.iters):
            acc2 = StreamAccumulator()
            t = time.perf_counter()
            for c in chunks:
                acc2.add_content(c.decode("utf-8", "replace"))
            samples.append((time.perf_counter() - t) * 1000)
        out["p50_ms"] = round(statistics.median(samples), 3)
        out["p95_ms"] = round(statistics.quantiles(samples, n=min(20, args.iters))[-1], 3)
        out["mb_per_s"] = round(total_bytes / 1e6 / (statistics.median(samples) / 1000), 1)

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
