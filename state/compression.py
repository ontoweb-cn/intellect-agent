"""Compression chain traversal utilities."""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

# ── Opt-in Rust acceleration ────────────────────────────────────────────────
try:
    from intellect_core import (  # type: ignore[import-not-found]
        get_compression_tip_py as _rust_get_compression_tip,
    )
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False


_COMPRESSION_TIP_SQL = """\
WITH RECURSIVE chain AS (
    SELECT id, parent_session_id, started_at, 0 AS depth
    FROM sessions WHERE id = ?
    UNION ALL
    SELECT s.id, s.parent_session_id, s.started_at, c.depth + 1
    FROM sessions s
    JOIN chain c ON s.parent_session_id = c.id
    WHERE s.started_at >= (
        SELECT ended_at FROM sessions
        WHERE id = c.id AND end_reason = 'compression'
    )
    AND c.depth < 100
)
SELECT id FROM chain ORDER BY depth DESC LIMIT 1"""


def get_compression_tip(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    session_id: str,
) -> Optional[str]:
    """Walk the compression-continuation chain and return the tip.

    Uses a single WITH RECURSIVE CTE instead of iterative lock/acquire
    per hop — reduces up to 100 sequential queries to 1.
    """
    if _HAS_RUST:
        return _rust_get_compression_tip(conn, lock, session_id)
    with lock:
        cursor = conn.execute(_COMPRESSION_TIP_SQL, (session_id,))
        row = cursor.fetchone()
    return row["id"] if row else session_id
