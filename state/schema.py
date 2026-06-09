"""Database schema and FTS constants."""

# ── FTS identifier whitelist ──────────────────────────────────────────────
_FTS_TABLES = frozenset({"messages_fts"})
_FTS_TRIGGERS = (
    "messages_fts_insert",
    "messages_fts_delete",
    "messages_fts_update",
)
_ALLOWED_FTS_TRIGGERS = frozenset(_FTS_TRIGGERS)


def validate_fts_identifier(name: str, allowed: frozenset) -> str:
    """Reject unexpected FTS identifiers before they reach an f-string SQL query."""
    if name not in allowed:
        raise ValueError(
            f"Unexpected FTS identifier {name!r}; "
            f"expected one of {sorted(allowed)}"
        )
    return name


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    member_id TEXT,
    team_id TEXT,
    project_id TEXT,
    project_workspace TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS state_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS compression_locks (
    session_id TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

-- =========================================================================
-- DEPRECATED (v0.5.0): Multi-user tables below are kept for backward
-- compatibility with existing databases. New installs create empty tables
-- that are never populated. Future versions may drop them via migration.
-- =========================================================================

-- Members (people) — multi-user foundation (deprecated)
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    login_name TEXT,
    email TEXT,
    enabled INTEGER DEFAULT 0,
    platform TEXT DEFAULT 'cli',
    role TEXT DEFAULT 'member',
    password_hash TEXT,
    password_salt TEXT,
    password_reset_code TEXT,
    password_reset_expiry REAL,
    password_set_at REAL,
    failed_login_count INTEGER DEFAULT 0,
    locked_until REAL,
    last_active_at REAL,
    last_active_platform TEXT,
    online_status TEXT DEFAULT 'offline',
    registration_pending INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL
);

-- External identity → member mapping
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id),
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    email TEXT,
    display_name TEXT,
    raw TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(provider, provider_id)
);

-- Per-member API bearer tokens (store hash only)
CREATE TABLE IF NOT EXISTS member_api_tokens (
    id TEXT PRIMARY KEY,
    member_id TEXT REFERENCES members(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    permissions TEXT,
    scope_type TEXT DEFAULT 'member',
    scope_id TEXT,
    created_at REAL NOT NULL,
    expires_at REAL,
    last_used_at REAL
);

-- Member management audit (approve/reject/delete/invite; no secrets)
CREATE TABLE IF NOT EXISTS member_admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    actor_member_id TEXT NOT NULL,
    target_member_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    source TEXT NOT NULL
);

-- Profile-admin invites
CREATE TABLE IF NOT EXISTS member_invites (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES members(id),
    email TEXT,
    team_id TEXT,
    reserved_member_id TEXT,
    expires_at REAL,
    accepted_by TEXT,
    accepted_at REAL,
    created_at REAL NOT NULL
);

-- Teams — shared collaboration context
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_by TEXT REFERENCES members(id),
    enabled INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL
);

-- Many-to-many: members ↔ teams
CREATE TABLE IF NOT EXISTS team_memberships (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id),
    member_id TEXT NOT NULL REFERENCES members(id),
    role TEXT NOT NULL DEFAULT 'member',
    joined_at REAL,
    invited_by TEXT REFERENCES members(id),
    UNIQUE(team_id, member_id)
);

-- Projects — shared work context
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    display_name TEXT NOT NULL,
    team_id TEXT REFERENCES teams(id),
    owner_member_id TEXT NOT NULL REFERENCES members(id),
    enabled INTEGER DEFAULT 1,
    archived INTEGER DEFAULT 0,
    repo_url TEXT,
    default_branch TEXT,
    created_at REAL NOT NULL,
    updated_at REAL,
    UNIQUE(team_id, slug)
);

-- Many-to-many: members ↔ projects
CREATE TABLE IF NOT EXISTS project_memberships (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    member_id TEXT NOT NULL REFERENCES members(id),
    role TEXT NOT NULL DEFAULT 'member',
    joined_at REAL,
    invited_by TEXT REFERENCES members(id),
    UNIQUE(project_id, member_id)
);

-- Optional many-to-many: projects ↔ teams
CREATE TABLE IF NOT EXISTS project_teams (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    team_id TEXT NOT NULL REFERENCES teams(id),
    role TEXT NOT NULL DEFAULT 'member',
    added_at REAL,
    UNIQUE(project_id, team_id)
);

-- Secret access audit log (project env reads/writes)
CREATE TABLE IF NOT EXISTS secret_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    action TEXT NOT NULL,
    key_name TEXT,
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_secret_access_log_project
    ON secret_access_log(project_id, timestamp DESC);

-- Per-member role bindings for v2 database-driven RBAC.
-- v1 hard-codes roles in ROLE_PERMISSIONS; this table is a schema
-- placeholder so v2 migrations run on installations that already
-- have the multi-user tables created.  No code queries it yet.
CREATE TABLE IF NOT EXISTS member_role_bindings (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    scope_type TEXT,    -- 'team' | 'project' | NULL (global)
    scope_id TEXT,      -- team_id or project_id when scoped
    granted_by TEXT,
    granted_at REAL,
    FOREIGN KEY (member_id) REFERENCES members(id)
);

CREATE TABLE IF NOT EXISTS role_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    description TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    permissions TEXT NOT NULL,
    created_at REAL,
    updated_at REAL
);

-- Member online presence tracking (v18)
CREATE TABLE IF NOT EXISTS member_sessions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id),
    platform TEXT NOT NULL,
    session_type TEXT NOT NULL DEFAULT 'login',
    external_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
    login_at REAL NOT NULL,
    last_active_at REAL NOT NULL,
    expires_at REAL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_member_sessions_member
    ON member_sessions(member_id, status);
CREATE INDEX IF NOT EXISTS idx_member_sessions_platform
    ON member_sessions(platform, external_id);

-- OAuth unified platform (v19)
CREATE TABLE IF NOT EXISTS oauth_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    usage TEXT NOT NULL DEFAULT 'login',
    auth_flow TEXT NOT NULL DEFAULT 'pkce_loopback',
    enabled INTEGER NOT NULL DEFAULT 0,
    logo_svg TEXT NOT NULL DEFAULT '',
    logo_path TEXT NOT NULL DEFAULT '',
    logo_type TEXT NOT NULL DEFAULT 'svg',
    client_id TEXT NOT NULL DEFAULT '',
    client_secret_encrypted TEXT NOT NULL DEFAULT '',
    authorize_url TEXT NOT NULL DEFAULT '',
    token_url TEXT NOT NULL DEFAULT '',
    userinfo_url TEXT NOT NULL DEFAULT '',
    device_code_url TEXT NOT NULL DEFAULT '',
    revoke_url TEXT NOT NULL DEFAULT '',
    scopes TEXT NOT NULL DEFAULT '[]',
    pkce INTEGER NOT NULL DEFAULT 1,
    tenant_specific INTEGER NOT NULL DEFAULT 0,
    tenant_config TEXT NOT NULL DEFAULT '{}',
    claim_sub TEXT NOT NULL DEFAULT 'sub',
    claim_email TEXT NOT NULL DEFAULT 'email',
    claim_name TEXT NOT NULL DEFAULT 'name',
    oidc_discovery_url TEXT NOT NULL DEFAULT '',
    token_storage TEXT NOT NULL DEFAULT 'identities',
    platform_bindable INTEGER NOT NULL DEFAULT 0,
    platform_bind_ttl INTEGER NOT NULL DEFAULT 3600,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    extra_metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES oauth_providers(id),
    member_id TEXT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_type TEXT NOT NULL DEFAULT 'bearer',
    scope TEXT NOT NULL DEFAULT '',
    expires_at REAL,
    issued_at REAL NOT NULL,
    last_used_at REAL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(provider_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_member ON oauth_tokens(member_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider_id);

CREATE TABLE IF NOT EXISTS oauth_pool_entries (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES oauth_providers(id),
    profile_scope TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    base_url TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    issued_at REAL NOT NULL,
    updated_at REAL,
    last_used_at REAL
);

CREATE INDEX IF NOT EXISTS idx_oauth_pool_provider
    ON oauth_pool_entries(provider_id, profile_scope, priority);

CREATE TABLE IF NOT EXISTS oauth_pending_states (
    nonce TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_pending_expires
    ON oauth_pending_states(expires_at);

-- =========================================================================
-- END DEPRECATED MULTI-USER TABLES
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_compression_locks_expires ON compression_locks(expires_at);

-- v24: inference provider & model registry (P1-7 Phase 1)
CREATE TABLE IF NOT EXISTS inference_providers (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    api_mode        TEXT NOT NULL DEFAULT 'chat_completions',
    auth_type       TEXT NOT NULL DEFAULT 'api_key',
    base_url        TEXT NOT NULL DEFAULT '',
    default_model   TEXT NOT NULL DEFAULT '',
    default_aux_model TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inference_models (
    id              TEXT PRIMARY KEY,
    provider_id     TEXT NOT NULL REFERENCES inference_providers(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL DEFAULT '',
    context_length  INTEGER NOT NULL DEFAULT 0,
    max_output_tokens INTEGER NOT NULL DEFAULT 0,
    supports_vision INTEGER NOT NULL DEFAULT 0,
    supports_thinking INTEGER NOT NULL DEFAULT 0,
    supports_fast_mode INTEGER NOT NULL DEFAULT 0,
    pricing_input   REAL NOT NULL DEFAULT 0.0,
    pricing_output  REAL NOT NULL DEFAULT 0.0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inference_provider_aliases (
    alias           TEXT PRIMARY KEY,
    provider_id     TEXT NOT NULL REFERENCES inference_providers(id) ON DELETE CASCADE,
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inference_models_provider ON inference_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_inference_aliases_provider ON inference_provider_aliases(provider_id);

-- Session index: session_key -> session_id mapping (replaces sessions.json).
-- SessionStore reads this on startup and writes on every state change.
CREATE TABLE IF NOT EXISTS session_index (
    session_key     TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    display_name    TEXT,
    platform        TEXT,
    chat_type       TEXT DEFAULT 'dm',
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    cost_status     TEXT DEFAULT 'unknown',
    last_prompt_tokens INTEGER DEFAULT 0,
    was_auto_reset  INTEGER DEFAULT 0,
    auto_reset_reason TEXT,
    reset_had_activity INTEGER DEFAULT 0,
    is_fresh_reset  INTEGER DEFAULT 0,
    expiry_finalized INTEGER DEFAULT 0,
    suspended       INTEGER DEFAULT 0,
    resume_pending  INTEGER DEFAULT 0,
    resume_reason   TEXT,
    last_resume_marked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_session_index_platform ON session_index(platform);
"""

FTS_SQL = """
-- Single trigram FTS5 table for both CJK and Latin search.
-- Trigram handles all scripts natively — eliminates the previous
-- double-trigger overhead (unicode61 + trigram) per INSERT.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
    );
END;
"""

FTS_TRIGRAM_SQL = ""
