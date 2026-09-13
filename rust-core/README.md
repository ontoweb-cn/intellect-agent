# Intellect Core — Rust (PyO3) Native Extension

Rust 加速层，通过 PyO3 编译为 Python 原生扩展模块 `intellect_community_core`。
覆盖存储、安全、Agent、加密、Gateway、验证与自动化六个核心域。

## Build

From the repo root (preferred — these are the targets CI and the docs use):

```bash
make rust-build    # maturin develop --release
make rust-dev      # maturin develop (debug, faster compile)
make rust-test     # cargo test
make rust-wheel    # maturin build --release
```

Or directly:

```bash
pip install maturin
cd rust-core
maturin develop --release   # dev install into the active venv
maturin build --release     # release wheel
```

`pyproject.toml` points maturin at `rust-core/Cargo.toml`
(`[tool.maturin] manifest = ...`, `module-name = "intellect_community_core"`).
The extension is **mandatory since v0.6.2** — `intellect_rust.py` raises at
startup if it is missing, and `make install-pure` no longer produces a working
install.

## Test & CI

- Rust unit tests: `cargo test --no-default-features` (no CPython linking needed)
- Python-side parity: `scripts/run_tests.sh tests/intellect_state/test_rust_parity.py`
- Handshake (build identity, stale-extension detection): `tests/test_community_core_handshake.py`
- CI: `.github/workflows/rust-ci.yml` (mirrored in `.gitee/workflows/`), which
  also runs `cargo deny check` against `deny.toml` and asserts a minimum Rust
  test count.

## Architecture (21 files, 8,141 lines)

```
src/
  backend.rs           — SQLiteBackend (managed connection, WAL, BEGIN IMMEDIATE write retry)
  connection.rs        — RustConnection / RustCursor / RustRow (DB-API compat)
  fts.rs               — FTS5 trigger/index utilities
  compression.rs       — Compression chain CTE traversal
  merge_queue.rs       — SessionDB write merge queue (batched append_message, HP-402)
  schema.rs            — FTS identifier whitelist
  sandbox.rs           — Command safety (87 compiled patterns: 12 hardline + 75 dangerous)
  verification.rs      — Verification evidence table for test/command results (HP-303)
  usage.rs             — Token normalization, model name normalization, TokenAccumulator
  stream.rs            — SSE delta accumulator (StreamAccumulator)
  crypto.rs            — PKCE, Fernet (AES-128-CBC + HMAC), secure random, JWT claims
  gateway.rs           — Session key builder, reset policy, backoff, rate limiter, retry scheduler
  delegation.rs        — Background delegation registry (handle tracking + completion queue)
  tokens.rs            — Token estimation, Grok allowlist, model name helpers, context probe tiers
  error_classifier.rs  — API error taxonomy (21 failover reasons, 8-stage pipeline, 400 heuristic)
  counters.rs          — Iteration budget + jittered backoff
  prompt_caching.rs    — Anthropic cache_control breakpoints (system_and_3 layout)
  sanitize.rs          — Surrogate stripping, non-ASCII stripping, JSON control char escaping, tool arg repair
  tool_utils.rs        — Pure tool/prompt helpers (mutation-landed, YAML frontmatter, truncate, path overlap, canonical args)
  blueprints.rs        — Automation Blueprint YAML parsing + param validation (HP-304)
  lib.rs               — Module entry point (59 functions + 12 classes exported)
```

The command-safety layer is split across languages by design: Python
(`tools/approval.py`) pre-normalizes the string and runs an `ast.parse()` pass
over `-c`/`-e` payloads; Rust receives the normalized lowercase string and
matches the compiled `RegexSet`.

## Runtime Integration

Since v0.6.4, all imports are centralized in `intellect_rust.py`. The Rust extension is a **required** dependency — all core workflows use native acceleration.

| Domain | Module(s) |
|--------|-----------|
| Storage | `SQLiteBackend`, `rust_append_message_batch` |
| FTS / compression | `rust_get_compression_tip`, `rust_is_fts5_unavailable_error`, `rust_drop_fts_triggers`, `rust_fts_trigger_count`, `rust_rebuild_fts_indexes` |
| Sandbox | `rust_detect_dangerous`, `rust_detect_hardline`, `rust_check_sudo_stdin`, `rust_is_forbidden_path`, `rust_is_ip_blocked` |
| Stream | `StreamAccumulator` |
| Usage | `TokenAccumulator`, `rust_normalize_usage`, `rust_normalize_model_name`, `rust_format_duration_compact`, `rust_format_token_count_compact` |
| Crypto | `rust_pkce_challenge`, `rust_pkce_from_verifier`, `rust_secure_hex`, `rust_fernet_encrypt`, `rust_fernet_decrypt`, `rust_generate_fernet_key` |
| Gateway | `rust_build_session_key`, `rust_evaluate_reset_policy`, `rust_check_expiry_batch`, `PlatformRetryScheduler` |
| Delegation | `DelegationRegistry` |
| Model | `rust_estimate_tokens_rough`, `rust_grok_supports_re`, `rust_strip_provider_prefix`, `rust_parse_context_limit`, `rust_parse_output_limit`, `rust_model_id_matches`, `rust_get_next_probe_tier`, etc. |
| Counters | `IterationBudget`, `rust_jittered_backoff` |
| Prompt caching | `rust_apply_cache_control` |
| Sanitize | `rust_sanitize_surrogates`, `rust_strip_non_ascii`, `rust_repair_tool_args`, `rust_escape_json_chars` |
| Tool utils | `rust_file_mutation_landed`, `rust_strip_yaml_frontmatter`, `rust_validate_skill_frontmatter`, `rust_truncate_content`, `rust_paths_overlap`, `rust_canonical_tool_args` |
| Blueprints | `rust_validate_blueprint_yaml`, `rust_validate_blueprint_params` |
| Verification | `rust_insert_verification_evidence`, `rust_query_verification_evidence`, `rust_classify_verification_command` |
| Error | `rust_classify_api_error`, `RustFailoverReason`, `RustClassifiedError` |

Wrappers are named `rust_*`; the extension-side symbols they bind to keep the
`*_rs` suffix (e.g. `rust_paths_overlap` → `paths_overlap_rs`).

## Benchmark

Current source of truth: **`docs/plans/bench-baseline.json`** (generated
2026-08-31), produced by the scripts in `scripts/bench/`. Excerpts:

| Bench | Setup | Result |
|-------|-------|--------|
| `stream_parse` | 2,000 chunks / 898 KB | p50 0.219 ms — **4,103 MB/s** (Rust) |
| `list_sessions_rich` | 10k sessions, limit 200 | p50 35.7 ms, of which **92% is SQL execution** |
| `token_estimate` | 601 messages | p50 0.507 ms |
| `compression` | — | `scripts/bench/bench_compression.py` |

Two candidate migrations were **measured and explicitly closed** — do not
re-open without a new benchmark:

- **G-14 `list_sessions_rich` → Rust: rejected.** The SQL already runs in
  SQLite's C layer; a port reclaims only the ~8% Python assembly while paying
  FFI row/dict rebuild costs. The real lever, if it is ever needed, is the SQL
  shape (the correlated preview subquery), not the language.
- **G-21 `stream_consumer` → Rust: rejected.** The Python hot path measures
  24.96 MB/s (p50 15.96 µs per delta); at a realistic 20 delta/s × 450-char
  gateway load that is 0.036% CPU. The bottleneck is the platform edit API
  (seconds, rate-limit bound), and SSE parsing is already covered by the Rust
  `StreamAccumulator`.

Historical micro-benchmarks (v0.6.0, not re-measured — treat as indicative
only): `normalize_usage` 0.004 → 0.001 ms, `build_session_key` 0.002 →
0.001 ms, `detect_dangerous_command` over 50K inputs 18.7 s → 18.4 s. Note the
pattern-scan case is essentially a wash: the workload is regex-bound, not
Python-bound, which is the same reason G-14 and G-21 closed.

## Dependencies

```toml
pyo3 = "0.29"          # Python bindings (default feature: extension-module)
                       # floor: CPython 3.8, Rust 1.83
rusqlite = "0.31"      # SQLite (bundled, FTS5 included)
regex = "1"            # Regex engine (replaced fancy-regex v0.14 for ReDoS safety)
serde + serde_json = "1"   # Serialization
serde_yaml = "0.9"         # Blueprint YAML parsing (HP-304)
sha2 = "0.10"           # SHA-256
base64 = "0.22"         # Base64 URL-safe
hex = "0.4"             # Hex encoding
rand = "0.8"            # CSPRNG
aes + cbc + hmac        # Fernet (AES-128-CBC + HMAC-SHA256)
pbkdf2 = "0.12"         # PBKDF2 key derivation
```

Licence/security policy for these crates lives in `rust-core/deny.toml`.
