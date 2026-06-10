# Intellect Core — Rust (PyO3)

Stage 1 native extension for the Intellect Agent storage layer.

## Build

```bash
pip install maturin
cd rust-core
maturin develop
```

## Usage

```python
import intellect_core

# FTS5 utilities
intellect_core.is_fts5_unavailable_error(exc)
intellect_core.drop_fts_triggers(cursor)
intellect_core.fts_trigger_count(cursor)
intellect_core.rebuild_fts_indexes(cursor)

# Compression
intellect_core.get_compression_tip(conn, lock, session_id)
```

## Structure

```
src/
  lib.rs          — PyO3 module entry point
  schema.rs       — FTS identifier whitelist, validation
  fts.rs          — FTS5 trigger/index utilities
  compression.rs  — Compression chain CTE traversal

tests/            — (future) Rust unit tests
```
