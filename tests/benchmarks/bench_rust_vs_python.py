"""Rust vs Python performance benchmarks for Stage 1-5 operations.

Run: python tests/benchmarks/bench_rust_vs_python.py
"""

import time
import statistics
import tempfile
import os
from pathlib import Path


def bench(name, fn_py, fn_rust=None, iterations=10000):
    """Run both Python and Rust versions, report speedup."""
    # Python
    start = time.perf_counter()
    for _ in range(iterations):
        fn_py()
    py_time = time.perf_counter() - start

    # Rust (if available)
    if fn_rust:
        try:
            start = time.perf_counter()
            for _ in range(iterations):
                fn_rust()
            rust_time = time.perf_counter() - start
            speedup = py_time / rust_time
            print(f"  {name:35s}  py={py_time*1000/iterations:.3f}ms  rust={rust_time*1000/iterations:.3f}ms  {speedup:.1f}x")
        except Exception as e:
            print(f"  {name:35s}  py={py_time*1000/iterations:.3f}ms  rust=ERROR: {e}")
    else:
        print(f"  {name:35s}  py={py_time*1000/iterations:.3f}ms  (no Rust version)")
    return py_time


def main():
    print("=" * 75)
    print("Rust vs Python Performance Benchmarks")
    print("=" * 75)

    has_rust = False
    try:
        import intellect_core
        has_rust = True
    except ImportError:
        print("WARNING: intellect_core not installed — Rust paths unavailable\n")

    # ── 1. PKCE generation ───────────────────────────────────────────────
    print("\n--- PKCE (10,000 iterations) ---")
    import hashlib, base64, secrets

    def py_pkce():
        v = secrets.token_urlsafe(32)
        d = hashlib.sha256(v.encode()).digest()
        c = base64.urlsafe_b64encode(d).rstrip(b"=").decode()
        return v, c

    def rust_pkce():
        return intellect_core.pkce_challenge() if has_rust else py_pkce()

    bench("pkce_challenge", py_pkce, rust_pkce)

    # ── 2. Fernet encrypt/decrypt ────────────────────────────────────────
    print("\n--- Fernet (5,000 iterations) ---")
    from cryptography.fernet import Fernet
    py_key = Fernet.generate_key()
    py_fernet = Fernet(py_key)
    plaintext = "hello world " * 10
    py_token = py_fernet.encrypt(plaintext.encode()).decode()

    rust_key = intellect_core.generate_fernet_key() if has_rust else py_key.decode()

    def py_fernet_enc():
        return Fernet(py_key).encrypt(plaintext.encode())

    def rust_fernet_enc():
        return intellect_core.fernet_encrypt(rust_key, plaintext) if has_rust else ""

    bench("fernet_encrypt", py_fernet_enc, rust_fernet_enc, 5000)

    def py_fernet_dec():
        return Fernet(py_key).decrypt(py_token.encode())

    def rust_fernet_dec():
        return intellect_core.fernet_decrypt(rust_key, py_token) if has_rust else ""

    bench("fernet_decrypt", py_fernet_dec, rust_fernet_dec, 5000)

    # ── 3. normalize_usage ────────────────────────────────────────────────
    print("\n--- normalize_usage (100,000 iterations) ---")

    class FakeUsage:
        prompt_tokens = 1500
        completion_tokens = 600
        prompt_tokens_details = None
        output_tokens_details = None
        input_tokens = 0
        output_tokens = 600
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    from agent.usage_pricing import normalize_usage, _HAS_RUST_USAGE

    def py_norm():
        return normalize_usage(FakeUsage(), provider="openai", api_mode="")

    def rust_norm():
        if has_rust:
            return intellect_core.normalize_usage_rs("", "", 0, 600, 1500, 600, 0, 0, 0, 0, 0)
        return py_norm()

    bench("normalize_usage", py_norm, rust_norm, 100000)

    # ── 4. TokenAccumulator ──────────────────────────────────────────────
    print("\n--- TokenAccumulator (100,000 add operations) ---")

    def py_accumulate():
        total = 0
        for i in range(1000):
            total += i
        return total

    if has_rust:
        acc = intellect_core.TokenAccumulator()

    def rust_accumulate():
        if has_rust:
            for i in range(1000):
                acc.add(i, i, i, i, i, 1, 0)
            return acc.input_tokens()
        return py_accumulate()

    bench("token_accumulator (1000 adds)", py_accumulate, rust_accumulate, 100)

    # ── 5. Command sandbox ────────────────────────────────────────────────
    print("\n--- Command Sandbox (50,000 matches) ---")
    from tools.approval import detect_dangerous_command, _HAS_RUST_SANDBOX, _normalize_command_for_detection

    test_cmds = [
        "rm -rf /tmp/data",
        "git push --force origin main",
        "find . -name '*.py' -exec grep -l TODO {} \\;",
        "echo hello world",
        "systemctl restart nginx",
    ]

    def py_sandbox():
        for cmd in test_cmds * 10000:
            detect_dangerous_command(cmd)

    def rust_sandbox():
        if has_rust:
            for cmd in test_cmds * 10000:
                n = _normalize_command_for_detection(cmd).lower()
                intellect_core.detect_dangerous_command_rs(n)
        else:
            py_sandbox()

    bench("detect_dangerous_command (5cmds×10000)", py_sandbox, rust_sandbox, 1)

    # ── 6. Session key builder ────────────────────────────────────────────
    print("\n--- Session Key Builder (50,000 iterations) ---")
    from gateway.session import build_session_key, SessionSource
    from gateway.config import Platform

    src = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm")

    def py_key():
        return build_session_key(src)

    def rust_key():
        if has_rust:
            return intellect_core.build_session_key_rs("telegram", "dm", "12345", "", "", "", True, False, "", "", "")
        return py_key()

    bench("build_session_key", py_key, rust_key, 50000)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("Benchmark complete.")
    print("=" * 75)


if __name__ == "__main__":
    main()
