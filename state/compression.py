"""Compression chain traversal utilities."""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from intellect_rust import rust_get_compression_tip


def get_compression_tip(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    session_id: str,
    *,
    db_path: str | None = None,
) -> Optional[str]:
    """Walk the compression-continuation chain and return the tip.

    Uses the Rust extension for fast traversal via its own read-only
    connection — no Python-side lock needed.
    """
    if db_path is None:
        raise ValueError(
            "db_path is required — the Rust extension opens its own connection"
        )
    return rust_get_compression_tip(db_path, session_id)
