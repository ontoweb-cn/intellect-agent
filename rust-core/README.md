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

## Architecture (15 files, ~6,600 lines)

```
src/
  backend.rs           — SQLiteBackend (WAL, write retry, 10 operations)
  connection.rs        — RustConnection / RustCursor (DB-API compat)
  fts.rs               — FTS5 trigger/index utilities
  compression.rs       — Compression chain CTE traversal
  schema.rs            — FTS identifier whitelist
  sandbox.rs           — Command safety (59 regex patterns, AST double-layer)
  usage.rs             — Token normalization, model name normalization, TokenAccumulator
  stream.rs            — SSE delta accumulator (StreamAccumulator)
  crypto.rs            — PKCE, Fernet (AES-128-CBC + HMAC), secure random
  gateway.rs           — Session key builder, reset policy, backoff, rate limiter
  tokens.rs            — Token estimation, Grok allowlist, model name helpers, context probe tiers
  error_classifier.rs  — API error taxonomy (22 reasons, 8-stage pipeline, 400 heuristic)
  counters.rs          — Iteration budget + jittered backoff
  sanitize.rs          — Surrogate stripping, non-ASCII stripping, JSON control char escaping, tool arg repair
  lib.rs               — Module entry point
```

## Runtime Integration

Since v0.6.4, all imports are centralized in `intellect_rust.py`. The Rust extension is a **required** dependency — all core workflows use native acceleration.

| Domain | Module(s) |
|--------|-----------|
| Storage | `SQLiteBackend` |
| Sandbox | `rust_detect_dangerous`, `rust_detect_hardline` |
| Stream | `StreamAccumulator`, `TokenAccumulator` |
| Usage | `rust_normalize_usage`, `rust_normalize_model_name` |
| Crypto | `rust_pkce_*`, `rust_fernet_*` |
| Gateway | `rust_build_session_key`, etc. |
| Model | `rust_estimate_tokens_rough`, `rust_grok_supports_re`, `rust_strip_provider_prefix`, etc. |
| Sanitize | `rust_sanitize_surrogates`, `rust_strip_non_ascii`, `rust_repair_tool_args`, `rust_escape_json_chars` |
| Error | `rust_classify_api_error` |

## Benchmark (v0.6.0)

| Operation | Python | Rust | Speedup |
|-----------|--------|------|---------|
| `normalize_usage` | 0.004ms | 0.001ms | 2.6x |
| `build_session_key` | 0.002ms | 0.001ms | 1.2x |
| `detect_dangerous_command` (50K) | 18.7s | 18.4s | 1.0x |

## Dependencies

```toml
pyo3 = "0.21"          # Python bindings
rusqlite = "0.31"      # SQLite (bundled, FTS5 included)
regex = "1"            # Regex engine (replaced fancy-regex v0.14 for ReDoS safety)
serde_json = "1"        # JSON serialization
sha2 = "0.10"           # SHA-256
base64 = "0.22"         # Base64 URL-safe
hex = "0.4"             # Hex encoding
rand = "0.8"            # CSPRNG
aes + cbc + hmac        # Fernet (AES-128-CBC + HMAC-SHA256)
pbkdf2 = "0.12"         # PBKDF2 key derivation
```
