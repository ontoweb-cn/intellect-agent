# Intellect Core — Rust (PyO3) Native Extension

Rust 加速层，通过 PyO3 编译为 Python 原生扩展模块 `intellect_core`。
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

All Rust paths use `_HAS_RUST` opt-in flags with pure-Python fallbacks:

| Module | Flag | Functions |
|--------|------|-----------|
| `state/fts.py` | `_HAS_RUST` | FTS5 trigger/index ops |
| `state/compression.py` | `_HAS_RUST` | Compression chain |
| `agent/storage/sqlite_backend.py` | `_HAS_RUST_BACKEND` | SQLiteBackend + 10 write ops |
| `tools/approval.py` | `_HAS_RUST_SANDBOX` | Command detection (59 patterns) |
| `agent/usage_pricing.py` | `_HAS_RUST_USAGE` | normalize_usage + TokenAccumulator |
| `agent/chat_completion_helpers.py` | `_HAS_RUST_STREAM` | StreamAccumulator |
| `agent/oauth/__init__.py` | `_HAS_RUST_CRYPTO` | PKCE + secure random |
| `agent/oauth/storage.py` | `_HAS_RUST_FERNET` | Fernet encrypt/decrypt |
| `agent/secret_store.py` | `_HAS_RUST_FERNET` | Fernet encrypt/decrypt |
| `gateway/session.py` | `_HAS_RUST_GATEWAY` | Session key + reset policy |
| `run_agent.py` | `_HAS_TOKEN_ACC` | TokenAccumulator as primary counter |

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
