"""Intellect Agent state package — database schema, FTS5, compression.

Migrated from intellect_state.py (v0.5.x file split). This package
holds the modules targeted for Rust Stage 1 migration (PyO3).
"""

from state.schema import (
    _ALLOWED_FTS_TRIGGERS,
    _FTS_TABLES,
    _FTS_TRIGGERS,
    FTS_SQL,
    FTS_TRIGRAM_SQL,
    SCHEMA_SQL,
    validate_fts_identifier,
)
from state.fts import (
    drop_fts_triggers,
    fts_trigger_count,
    is_fts5_unavailable_error,
    rebuild_fts_indexes,
)
from state.compression import get_compression_tip

__all__ = [
    # Schema
    "SCHEMA_SQL",
    "FTS_SQL",
    "FTS_TRIGRAM_SQL",
    # FTS
    "_FTS_TABLES",
    "_FTS_TRIGGERS",
    "_ALLOWED_FTS_TRIGGERS",
    "validate_fts_identifier",
    "drop_fts_triggers",
    "fts_trigger_count",
    "is_fts5_unavailable_error",
    "rebuild_fts_indexes",
    # Compression
    "get_compression_tip",
]
