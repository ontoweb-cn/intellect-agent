# Intellect Core — Rust (PyO3) Native Extension

Rust 加速层，通过 PyO3 编译为 Python 原生扩展模块 `intellect_community_core`。
覆盖存储、安全、Agent、加密、Gateway 五个核心域。

## Build

```bash
pip install maturin
cd rust-core
maturin develop        # dev install
maturin build --release  # release wheel
```

## Architecture (11 files, ~3,170 lines)

```
src/
  backend.rs      — SQLiteBackend (WAL, write retry, 10 operations)
  connection.rs   — RustConnection / RustCursor (DB-API compat)
  fts.rs          — FTS5 trigger/index utilities
  compression.rs  — Compression chain CTE traversal
  schema.rs       — FTS identifier whitelist
  sandbox.rs      — Command safety (59 regex patterns)
  usage.rs        — Token normalization + TokenAccumulator
  stream.rs       — SSE delta accumulator (StreamAccumulator)
  crypto.rs       — PKCE, Fernet (AES-128-CBC + HMAC), secure random
  gateway.rs      — Session key builder, reset policy, backoff, rate limiter
  lib.rs          — Module entry point
```

## Runtime Integration

Since v0.6.4, all imports are centralized in `intellect_rust.py`. When the Rust extension is not installed, safe pure-Python fallbacks are used automatically — no per-module `_HAS_RUST_*` flags needed.

| Domain | Module(s) | Fallback |
|--------|-----------|----------|
| Storage | `SQLiteBackend` | Returns `None` (callers use standard sqlite3) |
| Sandbox | `rust_detect_dangerous`, `rust_detect_hardline` | Raises `NotImplementedError` |
| Stream | `StreamAccumulator`, `TokenAccumulator` | Returns `None` (serial Python path) |
| Usage | `rust_normalize_usage` | Pure-Python pass-through |
| Crypto | `rust_pkce_*`, `rust_fernet_*` | Raises `NotImplementedError` |
| Gateway | `rust_build_session_key`, etc. | Raises `NotImplementedError` |
| Model | `rust_normalize_model_name` | Identity function |

> The extension is **optional** since v0.6.4. All core agent workflows run without it.

## Benchmark (v0.6.0)

| Operation | Python | Rust | Speedup |
|-----------|--------|------|---------|
| `normalize_usage` | 0.004ms | 0.001ms | 2.6x |
| `build_session_key` | 0.002ms | 0.001ms | 1.2x |
| `detect_dangerous_command` (50K) | 18.7s | 18.4s | 1.0x |

## Dependencies

```toml
pyo3 = "0.21"       # Python bindings
rusqlite = "0.31"   # SQLite (bundled, FTS5 included)
fancy-regex = "0.14" # Regex with look-ahead support
serde_json = "1"     # JSON serialization
sha2 = "0.10"        # SHA-256
base64 = "0.22"      # Base64 URL-safe
rand = "0.8"         # CSPRNG
aes + cbc + hmac     # Fernet (AES-128-CBC + HMAC-SHA256)
```
