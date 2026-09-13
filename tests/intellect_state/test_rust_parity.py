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
    """Tests for rust_is_forbidden_path.

    Contract: a non-empty reason string when the path is forbidden, ``None``
    when it is safe.  This guards secrets/credentials specifically — files
    like ``/etc/passwd`` are not secrets and are handled by the write-side
    deny list in ``agent/file_safety`` instead.
    """

    def test_blocks_system_paths(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/etc/shadow") is not None
        assert rust_is_forbidden_path("/etc/sudoers") is not None
        assert rust_is_forbidden_path("/proc/kcore") is not None
        assert rust_is_forbidden_path("/dev/mem") is not None

    def test_blocks_credential_files(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/home/user/.ssh/id_rsa") is not None
        assert rust_is_forbidden_path("/home/user/.aws/credentials") is not None
        assert rust_is_forbidden_path("/home/user/.kube/config") is not None
        assert rust_is_forbidden_path("/home/user/.netrc") is not None

    def test_private_key_extension_blocked_on_absolute_paths_only(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/home/user/certs/server.key") is not None
        # A relative path is more likely a project file than a credential.
        assert rust_is_forbidden_path("./certs/server.key") is None

    def test_allows_normal_paths(self):
        from intellect_rust import rust_is_forbidden_path

        assert rust_is_forbidden_path("/home/user/docs") is None
        assert rust_is_forbidden_path("./myfile.txt") is None


class TestIsIpBlocked:
    """Tests for rust_is_ip_blocked.

    Contract: a non-empty reason string when the IP is blocked, ``None`` when
    it looks safe.  Production callers treat it as a truthiness check.
    """

    def test_blocks_loopback(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("127.0.0.1") is not None
        assert rust_is_ip_blocked("::1") is not None

    def test_blocks_private_ranges(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("10.0.0.1") is not None
        assert rust_is_ip_blocked("192.168.1.1") is not None
        assert rust_is_ip_blocked("172.16.0.1") is not None

    def test_blocks_cloud_metadata(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("169.254.169.254") is not None

    def test_allows_public_ips(self):
        from intellect_rust import rust_is_ip_blocked

        assert rust_is_ip_blocked("8.8.8.8") is None
        assert rust_is_ip_blocked("1.1.1.1") is None


class TestCheckSudoStdin:
    """Tests for rust_check_sudo_stdin.

    Contract: ``(normalized_command, sudo_password_set) -> reason | None``.
    The guard is only relevant when no SUDO_PASSWORD is configured — with a
    password set, piping is legitimate and the check short-circuits.
    """

    def test_detects_sudo_stdin(self):
        from intellect_rust import rust_check_sudo_stdin

        assert rust_check_sudo_stdin("echo password | sudo -S command", False) is not None

    def test_short_circuits_when_password_configured(self):
        from intellect_rust import rust_check_sudo_stdin

        assert rust_check_sudo_stdin("echo password | sudo -S command", True) is None

    def test_allows_normal_sudo(self):
        from intellect_rust import rust_check_sudo_stdin

        assert rust_check_sudo_stdin("sudo ls", False) is None


# ── Usage / Token normalization ─────────────────────────────────────────────


class TestNormalizeUsage:
    """Tests for rust_normalize_usage.

    Positional contract: ``(api_mode, provider_name, input_tokens,
    output_tokens, prompt_tokens, completion_tokens, cache_read_input_tokens,
    cache_creation_input_tokens, cached_tokens_detail, cache_write_tokens_detail,
    reasoning_tokens_detail) -> (input, output, cache_read, cache_write, reasoning)``
    """

    def test_anthropic_mode(self):
        from intellect_rust import rust_normalize_usage

        result = rust_normalize_usage(
            "anthropic_messages", "anthropic",
            100, 50, 0, 0, 10, 5, 0, 0, 0,
        )
        assert result is not None
        assert len(result) == 5

    def test_openai_mode(self):
        from intellect_rust import rust_normalize_usage

        result = rust_normalize_usage(
            "chat_completions", "openai",
            0, 0, 200, 100, 0, 0, 0, 0, 0,
        )
        assert result is not None
        assert len(result) == 5

    def test_anthropic_buckets_pass_through(self):
        from intellect_rust import rust_normalize_usage

        i, o, cr, cw, r = rust_normalize_usage(
            "anthropic_messages", "anthropic",
            100, 50, 0, 0, 10, 5, 0, 0, 3,
        )
        assert (i, o, cr, cw, r) == (100, 50, 10, 5, 3)

    def test_openai_derives_input_by_subtracting_cached(self):
        from intellect_rust import rust_normalize_usage

        # prompt_tokens is the inclusive total; details break out the cached
        # portion, which must be subtracted from the input bucket.
        i, o, cr, _cw, _r = rust_normalize_usage(
            "chat_completions", "openai",
            0, 0, 200, 100, 0, 0, 30, 0, 0,
        )
        assert (i, o, cr) == (170, 100, 30)


class TestNormalizeModelName:
    """Tests for rust_normalize_model_name.

    Contract: ``(model, preserve_dots) -> str``.  When ``preserve_dots`` is
    False, dots are converted to hyphens for Anthropic model IDs (except
    Bedrock IDs, which use dots as namespace separators).
    """

    def test_returns_string(self):
        from intellect_rust import rust_normalize_model_name

        result = rust_normalize_model_name("claude-sonnet-4-6", False)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_passthrough_unknown(self):
        from intellect_rust import rust_normalize_model_name

        result = rust_normalize_model_name("unknown-model-xyz", False)
        assert result == "unknown-model-xyz"

    def test_strips_openrouter_anthropic_prefix(self):
        from intellect_rust import rust_normalize_model_name

        result = rust_normalize_model_name("anthropic/claude-sonnet-4", False)
        assert result == "claude-sonnet-4"

    def test_preserve_dots_flag(self):
        from intellect_rust import rust_normalize_model_name

        # Dots are version separators → hyphens unless preserve_dots is set.
        assert rust_normalize_model_name("claude-3.5-sonnet", False) == "claude-3-5-sonnet"
        assert rust_normalize_model_name("claude-3.5-sonnet", True) == "claude-3.5-sonnet"


# ── Gateway ─────────────────────────────────────────────────────────────────


class TestBuildSessionKey:
    """Tests for rust_build_session_key.

    Positional contract: ``(platform, chat_type, chat_id, thread_id, user_id,
    user_id_alt, group_sessions_per_user, thread_sessions_per_user, member_id,
    team_id, project_id) -> str``.  The result is a structured session key
    (``agent:main:<platform>:...``), not a hash.
    """

    @staticmethod
    def _key(chat_id="c1", thread_id="", user_id="u1", user_id_alt="",
             group_per_user=False, thread_per_user=False,
             member_id="", team_id="", project_id="",
             platform="cli", chat_type="dm"):
        from intellect_rust import rust_build_session_key

        return rust_build_session_key(
            platform, chat_type, chat_id, thread_id, user_id, user_id_alt,
            group_per_user, thread_per_user, member_id, team_id, project_id,
        )

    def test_dm_session_key(self):
        key = self._key()
        assert key == "agent:main:cli:dm:c1"

    def test_deterministic_for_same_inputs(self):
        assert self._key() == self._key()

    def test_group_sessions_per_user_isolates_participants(self):
        alice = self._key(platform="telegram", chat_type="group", chat_id="g1",
                          user_id="alice", user_id_alt="alice",
                          group_per_user=True)
        bob = self._key(platform="telegram", chat_type="group", chat_id="g1",
                        user_id="bob", user_id_alt="bob",
                        group_per_user=True)
        shared = self._key(platform="telegram", chat_type="group", chat_id="g1",
                           user_id="alice", user_id_alt="alice",
                           group_per_user=False)

        assert alice != bob
        # Without per-user grouping everyone shares one key.
        assert shared == "agent:main:telegram:group:g1"

    def test_optional_scopes_are_appended(self):
        key = self._key(member_id="m1", team_id="t1", project_id="p1")
        assert key == "agent:main:cli:dm:c1:member:m1:team:t1:project:p1"


# ── Stream ──────────────────────────────────────────────────────────────────


class TestStreamAccumulator:
    """Tests for StreamAccumulator.

    Contract: feed deltas via ``add_content`` / ``add_reasoning`` /
    ``add_tool_delta`` / ``set_model`` / ``set_finish_reason``, then read the
    assembled result from ``finalize()`` as a 5-tuple:
    ``(content, tool_calls_json, reasoning, finish_reason, model)``.
    """

    def test_accumulates_content_delta(self):
        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        acc.add_content("Hello ")
        acc.add_content("world")

        content, tool_calls, reasoning, finish_reason, model = acc.finalize()
        assert content == "Hello world"
        assert tool_calls == ""
        assert reasoning == ""
        assert finish_reason is None
        assert model is None

    def test_initial_state_is_empty(self):
        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        content, tool_calls, reasoning, finish_reason, model = acc.finalize()
        assert content == ""
        assert tool_calls == ""
        assert reasoning == ""
        assert finish_reason is None
        assert model is None

    def test_tracks_reasoning_finish_reason_and_model(self):
        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        acc.add_content("answer")
        acc.add_reasoning("thinking")
        acc.set_model("claude-sonnet-4")
        acc.set_finish_reason("stop")

        content, _tools, reasoning, finish_reason, model = acc.finalize()
        assert content == "answer"
        assert reasoning == "thinking"
        assert finish_reason == "stop"
        assert model == "claude-sonnet-4"

    def test_accumulates_tool_call_arguments(self):
        import json

        from intellect_rust import StreamAccumulator

        acc = StreamAccumulator()
        # Arguments arrive in fragments and must be concatenated.
        acc.add_tool_delta(0, "call_1", "search", '{"q":')
        acc.add_tool_delta(0, "call_1", None, '"hi"}')

        _content, tool_calls, _reasoning, _finish, _model = acc.finalize()
        entries = json.loads(tool_calls)
        assert len(entries) == 1
        assert entries[0]["id"] == "call_1"
        assert entries[0]["function"]["name"] == "search"
        assert json.loads(entries[0]["function"]["arguments"]) == {"q": "hi"}
