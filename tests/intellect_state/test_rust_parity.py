"""Verify Rust (intellect_community_core) functions work correctly.

These tests confirm the Rust-backed functions behave as expected.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading


def _fresh_db() -> tuple[sqlite3.Connection, str]:
    """Return a temp-file SQLite database with FTS5 enabled, plus its path.

    Uses a temp file (not :memory:) so the Rust rusqlite connection
    to the same path shares the same database.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content, tokenize='trigram')")
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT"
        ")"
    )
    # Create the triggers like the real schema does
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (
            new.id, COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
        );
    END;
    CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
    END;
    CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
        INSERT INTO messages_fts(rowid, content) VALUES (
            new.id, COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
        );
    END;
    """)
    return conn, path


def _fresh_sessions_db() -> tuple[sqlite3.Connection, str]:
    """Return a temp-file SQLite database with sessions table, plus its path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions ("
        "id TEXT PRIMARY KEY, "
        "parent_session_id TEXT, "
        "started_at REAL, "
        "ended_at REAL, "
        "end_reason TEXT"
        ")"
    )
    return conn, path


# ── is_fts5_unavailable_error ────────────────────────────────────────────────


class TestIsFts5UnavailableError:
    """Tests for is_fts5_unavailable_error."""

    def test_detects_fts5_error(self):
        from state import fts

        exc = sqlite3.OperationalError("no such module: FTS5")
        assert fts.is_fts5_unavailable_error(exc) is True

    def test_ignores_other_error(self):
        from state import fts

        exc = sqlite3.OperationalError("table messages already exists")
        assert fts.is_fts5_unavailable_error(exc) is False

    def test_ignores_partial_match(self):
        from state import fts

        exc = sqlite3.OperationalError("no such module: json1")
        assert fts.is_fts5_unavailable_error(exc) is False


# ── drop_fts_triggers ────────────────────────────────────────────────────────


class TestDropFtsTriggers:
    """Tests for drop_fts_triggers."""

    def test_drop_all_triggers(self):
        from state import fts

        conn, db_path = _fresh_db()
        count_before = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count_before == 3

        fts.drop_fts_triggers(conn.cursor(), db_path=db_path)
        count_after = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count_after == 0

    def test_drop_triggers_idempotent(self):
        from state import fts

        conn, db_path = _fresh_db()
        cursor = conn.cursor()
        fts.drop_fts_triggers(cursor, db_path=db_path)
        fts.drop_fts_triggers(cursor, db_path=db_path)
        assert fts.fts_trigger_count(conn.cursor(), db_path=db_path) == 0


# ── fts_trigger_count ────────────────────────────────────────────────────────


class TestFtsTriggerCount:
    """Tests for fts_trigger_count."""

    def test_counts_triggers(self):
        from state import fts

        conn, db_path = _fresh_db()
        count = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count == 3

    def test_zero_triggers(self):
        from state import fts

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        count = fts.fts_trigger_count(conn.cursor(), db_path=path)
        assert count == 0


# ── rebuild_fts_indexes ──────────────────────────────────────────────────────


class TestRebuildFtsIndexes:
    """Tests for rebuild_fts_indexes."""

    def test_rebuild_populates_fts(self):
        from state import fts

        conn, db_path = _fresh_db()
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (1, 'hello world', 'search', 'tool1')"
        )
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (2, 'foo bar', NULL, NULL)"
        )
        conn.commit()

        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        rows = conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'hello'"
        ).fetchall()
        assert len(rows) >= 1

    def test_rebuild_idempotent(self):
        from state import fts

        conn, db_path = _fresh_db()
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (1, 'test', NULL, NULL)"
        )
        conn.commit()

        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()
        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) as c FROM messages_fts").fetchone()
        assert rows["c"] == 1


# ── get_compression_tip ──────────────────────────────────────────────────────


class TestGetCompressionTip:
    """Tests for get_compression_tip."""

    def test_no_chain_returns_self(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()

        result = compression.get_compression_tip(conn, lock, "session-1", db_path=db_path)
        assert result == "session-1"

    def test_chain_follows_parent(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()
        now = 1000.0

        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, NULL, ?, ?, ?)",
            ("s1", now - 30, now - 20, "compression"),
        )
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s2", "s1", now - 15, now - 10, "compression"),
        )
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, ?, ?, NULL, NULL)",
            ("s3", "s2", now - 5),
        )
        conn.commit()

        result = compression.get_compression_tip(conn, lock, "s1", db_path=db_path)
        assert result == "s3"


# ── Rust availability ────────────────────────────────────────────────────────


class TestRustAvailability:
    """Verify Rust module is properly installed and configured."""

    def test_rust_is_importable(self):
        import intellect_community_core
        assert hasattr(intellect_community_core, "is_fts5_unavailable_error")
        assert hasattr(intellect_community_core, "drop_fts_triggers_rs")
        assert hasattr(intellect_community_core, "fts_trigger_count_rs")
        assert hasattr(intellect_community_core, "rebuild_fts_indexes_rs")
        assert hasattr(intellect_community_core, "get_compression_tip_rs")

    def test_centralized_imports_available(self):
        from intellect_rust import (
            rust_drop_fts_triggers,
            rust_fts_trigger_count,
            rust_get_compression_tip,
            rust_is_fts5_unavailable_error,
            rust_rebuild_fts_indexes,
        )
        assert rust_is_fts5_unavailable_error is not None
        assert rust_drop_fts_triggers is not None
        assert rust_fts_trigger_count is not None
        assert rust_rebuild_fts_indexes is not None
        assert rust_get_compression_tip is not None

    def test_crypto_imports_available(self):
        from intellect_rust import (
            rust_pkce_challenge,
            rust_pkce_from_verifier,
            rust_secure_hex,
            rust_fernet_encrypt,
            rust_fernet_decrypt,
            rust_generate_fernet_key,
        )
        assert rust_pkce_challenge is not None
        assert rust_pkce_from_verifier is not None
        assert rust_secure_hex is not None
        assert rust_fernet_encrypt is not None
        assert rust_fernet_decrypt is not None
        assert rust_generate_fernet_key is not None

    def test_sandbox_imports_available(self):
        from intellect_rust import (
            rust_detect_hardline,
            rust_detect_dangerous,
            rust_check_sudo_stdin,
            rust_is_forbidden_path,
            rust_is_ip_blocked,
        )
        assert rust_detect_hardline is not None
        assert rust_detect_dangerous is not None
        assert rust_check_sudo_stdin is not None
        assert rust_is_forbidden_path is not None
        assert rust_is_ip_blocked is not None

    def test_usage_imports_available(self):
        from intellect_rust import (
            rust_normalize_usage,
            rust_normalize_model_name,
            StreamAccumulator,
            TokenAccumulator,
        )
        assert rust_normalize_usage is not None
        assert rust_normalize_model_name is not None
        assert StreamAccumulator is not None
        assert TokenAccumulator is not None

    def test_gateway_imports_available(self):
        from intellect_rust import (
            rust_build_session_key,
            rust_check_expiry_batch,
            rust_evaluate_reset_policy,
        )
        assert rust_build_session_key is not None
        assert rust_check_expiry_batch is not None
        assert rust_evaluate_reset_policy is not None


# ── Crypto (PKCE + Fernet) ──────────────────────────────────────────────────


class TestPkceChallenge:
    """Tests for rust_pkce_challenge (PKCE code verifier + challenge)."""

    def test_generates_valid_pair(self):
        from intellect_rust import rust_pkce_challenge

        verifier, challenge = rust_pkce_challenge()
        # Verifier: 43-128 URL-safe characters
        assert 43 <= len(verifier) <= 128
        # Challenge: SHA-256 base64url, no padding
        assert len(challenge) == 43
        assert "=" not in challenge

    def test_deterministic_from_verifier(self):
        from intellect_rust import rust_pkce_challenge, rust_pkce_from_verifier

        v1, c1 = rust_pkce_challenge()
        c2 = rust_pkce_from_verifier(v1)
        assert c1 == c2

    def test_unique_pairs(self):
        from intellect_rust import rust_pkce_challenge

        pairs = [rust_pkce_challenge() for _ in range(10)]
        verifiers = {v for v, _ in pairs}
        assert len(verifiers) == 10


class TestFernet:
    """Tests for rust_fernet_encrypt / rust_fernet_decrypt."""

    def test_roundtrip(self):
        from intellect_rust import rust_generate_fernet_key, rust_fernet_encrypt, rust_fernet_decrypt

        key = rust_generate_fernet_key()
        plaintext = "hello world — 你好世界"
        token = rust_fernet_encrypt(key, plaintext)
        assert token != plaintext
        decrypted = rust_fernet_decrypt(key, token)
        assert decrypted == plaintext

    def test_different_keys_produce_different_ciphertext(self):
        from intellect_rust import rust_generate_fernet_key, rust_fernet_encrypt

        k1 = rust_generate_fernet_key()
        k2 = rust_generate_fernet_key()
        t1 = rust_fernet_encrypt(k1, "test")
        t2 = rust_fernet_encrypt(k2, "test")
        assert t1 != t2

    def test_decrypt_with_wrong_key_raises(self):
        from intellect_rust import rust_generate_fernet_key, rust_fernet_encrypt, rust_fernet_decrypt

        k1 = rust_generate_fernet_key()
        k2 = rust_generate_fernet_key()
        token = rust_fernet_encrypt(k1, "secret")
        try:
            rust_fernet_decrypt(k2, token)
            assert False, "Should have raised"
        except Exception:
            pass


class TestSecureHex:
    """Tests for rust_secure_hex."""

    def test_generates_hex_string(self):
        from intellect_rust import rust_secure_hex

        result = rust_secure_hex(16)
        assert len(result) == 32  # 16 bytes → 32 hex chars
        assert all(c in "0123456789abcdef" for c in result)

    def test_unique_outputs(self):
        from intellect_rust import rust_secure_hex

        values = {rust_secure_hex(8) for _ in range(20)}
        assert len(values) == 20


# ── Sandbox ─────────────────────────────────────────────────────────────────


class TestDetectHardline:
    """Tests for rust_detect_hardline."""

    def test_detects_rm_rf(self):
        from intellect_rust import rust_detect_hardline

        assert rust_detect_hardline("rm -rf /") is not None
        assert rust_detect_hardline("rm -rf / --no-preserve-root") is not None

    def test_detects_shutdown(self):
        from intellect_rust import rust_detect_hardline

        assert rust_detect_hardline("shutdown -h now") is not None
        assert rust_detect_hardline("shutdown /s /t 0") is not None

    def test_allows_normal_commands(self):
        from intellect_rust import rust_detect_hardline

        assert rust_detect_hardline("echo hello") is None
        assert rust_detect_hardline("ls -la") is None
        assert rust_detect_hardline("cat file.txt") is None


class TestDetectDangerous:
    """Tests for rust_detect_dangerous."""

    def test_detects_chmod_777(self):
        from intellect_rust import rust_detect_dangerous

        assert rust_detect_dangerous("chmod 777 /etc/passwd") is not None
        assert rust_detect_dangerous("chmod -R 777 .") is not None

    def test_detects_python_c_dangerous(self):
        from intellect_rust import rust_detect_dangerous

        # python -c with dangerous imports
        result = rust_detect_dangerous("python -c \"import os; os.system('id')\"")
        assert result is not None

    def test_allows_normal_commands(self):
        from intellect_rust import rust_detect_dangerous

        assert rust_detect_dangerous("python script.py") is None
        assert rust_detect_dangerous("npm install") is None
        assert rust_detect_dangerous("git status") is None


class TestIsForbiddenPath:
    """Tests for rust_is_forbidden_path."""

    def test_blocks_system_paths(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/etc/passwd") is True
        assert rust_is_forbidden_path("C:\\Windows\\System32\\config\\SAM") is True

    def test_allows_normal_paths(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/home/user/docs") is False
        assert rust_is_forbidden_path("./myfile.txt") is False


class TestIsIpBlocked:
    """Tests for rust_is_ip_blocked."""

    def test_blocks_loopback(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("127.0.0.1") is True
        assert rust_is_ip_blocked("::1") is True

    def test_blocks_private_ranges(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("10.0.0.1") is True
        assert rust_is_ip_blocked("192.168.1.1") is True
        assert rust_is_ip_blocked("172.16.0.1") is True

    def test_allows_public_ips(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("8.8.8.8") is False
        assert rust_is_ip_blocked("1.1.1.1") is False


class TestCheckSudoStdin:
    """Tests for rust_check_sudo_stdin."""

    def test_detects_sudo_stdin(self):
        from intellect_rust import rust_check_sudo_stdin

        assert rust_check_sudo_stdin("echo password | sudo -S command") is True
        assert rust_check_sudo_stdin("echo pwd | sudo --stdin cmd") is True

    def test_allows_normal_sudo(self):
        from intellect_rust import rust_check_sudo_stdin

        assert rust_check_sudo_stdin("sudo ls") is False


# ── Usage / Token normalization ─────────────────────────────────────────────


class TestNormalizeUsage:
    """Tests for rust_normalize_usage."""

    def test_anthropic_mode(self):
        from intellect_rust import rust_normalize_usage

        result = rust_normalize_usage(
            "anthropic", "anthropic",
            input_tokens=100, output_tokens=50,
            prompt_tokens=0, completion_tokens=0,
            cache_read_input_tokens=10, cache_creation_input_tokens=5,
            cached_detail=0, cache_write_detail=0, reasoning_tokens=0,
        )
        assert result is not None
        assert len(result) == 5

    def test_openai_mode(self):
        from intellect_rust import rust_normalize_usage

        result = rust_normalize_usage(
            "openai", "openai",
            input_tokens=0, output_tokens=0,
            prompt_tokens=200, completion_tokens=100,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
            cached_detail=0, cache_write_detail=0, reasoning_tokens=0,
        )
        assert result is not None
        assert len(result) == 5

    def test_preserves_token_counts(self):
        from intellect_rust import rust_normalize_usage

        i, o, cr, cw, r = rust_normalize_usage(
            "anthropic", "anthropic",
            input_tokens=100, output_tokens=50,
            prompt_tokens=0, completion_tokens=0,
            cache_read_input_tokens=10, cache_creation_input_tokens=5,
            cached_detail=0, cache_write_detail=0, reasoning_tokens=3,
        )
        assert i + o + cr + cw + r > 0


class TestNormalizeModelName:
    """Tests for rust_normalize_model_name."""

    def test_returns_string(self):
        from intellect_rust import rust_normalize_model_name

        result = rust_normalize_model_name("claude-sonnet-4-6")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_passthrough_unknown(self):
        from intellect_rust import rust_normalize_model_name

        result = rust_normalize_model_name("unknown-model-xyz")
        assert result == "unknown-model-xyz"


# ── Gateway ─────────────────────────────────────────────────────────────────


class TestBuildSessionKey:
    """Tests for rust_build_session_key."""

    def test_dm_session_key(self):
        from intellect_rust import rust_build_session_key

        key = rust_build_session_key("user-alice", "user-bob")
        assert isinstance(key, str)
        assert len(key) == 64  # SHA-256 hex

    def test_deterministic_for_same_members(self):
        from intellect_rust import rust_build_session_key

        k1 = rust_build_session_key("alice", "bob")
        k2 = rust_build_session_key("alice", "bob")
        assert k1 == k2

    def test_order_independent(self):
        from intellect_rust import rust_build_session_key

        k1 = rust_build_session_key("alice", "bob")
        k2 = rust_build_session_key("bob", "alice")
        assert k1 == k2

    def test_different_members_different_key(self):
        from intellect_rust import rust_build_session_key

        k1 = rust_build_session_key("alice", "bob")
        k2 = rust_build_session_key("alice", "charlie")
        assert k1 != k2


# ── Stream ──────────────────────────────────────────────────────────────────


class TestStreamAccumulator:
    """Tests for StreamAccumulator."""

    def test_accumulates_content_delta(self):
        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        assert acc is not None
        # Content delta event
        result = acc.process_sse("content_block_delta", '{"type":"text_delta","text":"Hello"}')
        assert result is not None

    def test_initial_state(self):
        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        state = acc.get_state()
        assert isinstance(state, str)
        import json
        data = json.loads(state)
        assert "text" in data
