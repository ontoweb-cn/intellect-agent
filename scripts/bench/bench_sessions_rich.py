#!/usr/bin/env python3
"""Benchmark: SessionDB.list_sessions_rich at 10k sessions (G-14 baseline).

Creates a temp state.db seeded with N sessions + messages, then times
``list_sessions_rich`` (p50/p95 over repeated calls). Prints one JSON line
so results can be piped into docs/plans/bench-baseline.json.

Usage: python scripts/bench/bench_sessions_rich.py [--sessions 10000] [--iters 20]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=10_000)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--msgs-per-session", type=int, default=4)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="intellect-bench-")
    os.environ["INTELLECT_HOME"] = tmp

    from intellect_state import SessionDB  # noqa: E402 (after INTELLECT_HOME)

    db = SessionDB(db_path=Path(tmp) / "state.db")
    t0 = time.perf_counter()
    for i in range(args.sessions):
        sid = f"bench-{i:06d}"
        db.create_session(sid, source="cli")
        for j in range(args.msgs_per_session):
            db.append_message(sid, "user" if j % 2 == 0 else "assistant",
                              f"message {i}/{j} " + "x" * 120)
    seed_s = time.perf_counter() - t0

    # Warm once (page cache + statement cache), then sample.
    db.list_sessions_rich(limit=200)
    samples = []
    for _ in range(args.iters):
        t = time.perf_counter()
        rows = db.list_sessions_rich(limit=200)
        samples.append((time.perf_counter() - t) * 1000)
        assert rows, "expected rows"

    out = {
        "bench": "list_sessions_rich",
        "sessions": args.sessions,
        "msgs_per_session": args.msgs_per_session,
        "seed_seconds": round(seed_s, 3),
        "limit": 200,
        "iters": args.iters,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(statistics.quantiles(samples, n=20)[-1], 3),
        "rows_returned": len(rows),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
