# OAuth 统一平台设计方案

**日期：** 2026-06-02
**状态：** ✅ Phase 1-8 已实现（commit `c883f1e7b`），10 项 code review 修复完成

---

## 一、目标

将当前分散在三处的 OAuth 统一为一个可扩展平台：

```
现状（分散）:
  ├─ 登录 OAuth     — agent/members_oauth.py (config.yaml)
  ├─ 模型 OAuth     — intellect_cli/auth.py (auth.json + 硬编码)
  └─ APP 服务端认证  — 不存在

目标（统一）:
  └─ OAuth Platform  — 一个注册表，三种用途，数据库驱动，可扩展
```

---

## 二、OAuth 三种用途

| 用途 | 场景 | 输出 | 示例 |
|------|------|------|------|
| **Login** | 用户登录 CLI / WebUI | `member_id` + session | GitHub OAuth → 绑定到 member |
| **Model** | 获取 LLM API 凭证 | `access_token` / `api_key` | ONTOWEB Device Code → inference JWT |
| **Server** | 外部服务调用 Intellect API | `bearer_token` + `scope` | CI/CD pipeline 调用 API Server |

三种用途共用同一个 OAuth 流程引擎，差异仅在于：
- **Token 存储位置**（identities 表 vs credential_pool vs member_api_tokens）
- **Token 生命周期**（一次性 vs refreshable vs expirable）
- **授权后动作**（绑定 member vs 存入凭证池 vs 返回 API token）

---

## 三、数据模型

### 3.1 `oauth_providers` 表（Schema v19）

```sql
CREATE TABLE IF NOT EXISTS oauth_providers (
    id TEXT PRIMARY KEY,              -- 'github', 'google', 'ontoweb', 'xai'
    name TEXT NOT NULL,               -- display name
    usage TEXT NOT NULL DEFAULT 'login',  -- 'login' | 'model' | 'server' | 'both'
    auth_flow TEXT NOT NULL DEFAULT 'pkce_loopback',
        -- 'pkce_loopback' | 'device_code' | 'oidc_discovery' | 'trusted_header'
    enabled INTEGER NOT NULL DEFAULT 0,

    -- Endpoint configuration
    client_id TEXT NOT NULL DEFAULT '',
    client_secret_encrypted TEXT NOT NULL DEFAULT '',  -- Fernet-encrypted
    authorize_url TEXT NOT NULL DEFAULT '',
    token_url TEXT NOT NULL DEFAULT '',
    userinfo_url TEXT NOT NULL DEFAULT '',
    device_code_url TEXT NOT NULL DEFAULT '',
    revoke_url TEXT NOT NULL DEFAULT '',

    -- OAuth parameters
    scopes TEXT NOT NULL DEFAULT '[]',        -- JSON array
    pkce INTEGER NOT NULL DEFAULT 1,
    tenant_specific INTEGER NOT NULL DEFAULT 0,
    tenant_config TEXT NOT NULL DEFAULT '{}',  -- JSON: {tenant_id, tenant_name}

    -- Claims mapping
    claim_sub TEXT NOT NULL DEFAULT 'sub',
    claim_email TEXT NOT NULL DEFAULT 'email',
    claim_name TEXT NOT NULL DEFAULT 'name',

    -- OIDC Discovery
    oidc_discovery_url TEXT NOT NULL DEFAULT '',

    -- Token storage policy
    token_storage TEXT NOT NULL DEFAULT 'identities',
        -- 'identities' | 'credential_pool' | 'auth_json' | 'member_tokens'

    -- Display / ordering
    display_order INTEGER NOT NULL DEFAULT 0,
    icon_url TEXT NOT NULL DEFAULT '',

    -- Metadata
    is_builtin INTEGER NOT NULL DEFAULT 0,    -- 1 = cannot be deleted via CLI
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL
);
```

### 3.2 `oauth_tokens` 表（统一 Token 存储）

```sql
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES oauth_providers(id),
    member_id TEXT,                          -- NULL for model providers
    access_token_encrypted TEXT NOT NULL,     -- Fernet-encrypted
    refresh_token_encrypted TEXT,             -- Fernet-encrypted
    token_type TEXT NOT NULL DEFAULT 'bearer',
    scope TEXT NOT NULL DEFAULT '',
    expires_at REAL,
    issued_at REAL NOT NULL,
    last_used_at REAL,
    metadata TEXT NOT NULL DEFAULT '{}',      -- JSON: raw claims, id_token, etc.
    UNIQUE(provider_id, member_id)            -- one token per provider per member
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_member ON oauth_tokens(member_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider_id);
```

### 3.3 预置数据

```sql
-- 登录 OAuth (4 built-in)
INSERT INTO oauth_providers (id, name, usage, auth_flow, ...) VALUES
  ('github', 'GitHub', 'login', 'pkce_loopback', ...),
  ('google', 'Google', 'login', 'pkce_loopback', ...),
  ('gitee', 'Gitee', 'login', 'pkce_loopback', ...),
  ('azure_ad', 'Azure AD', 'login', 'oidc_discovery', ...);

-- 模型 OAuth (5 built-in)
INSERT INTO oauth_providers (id, name, usage, auth_flow, token_storage, ...) VALUES
  ('ontoweb', 'ONTOWEB Portal', 'model', 'device_code', 'credential_pool', ...),
  ('openai_codex', 'OpenAI Codex', 'model', 'pkce_loopback', 'credential_pool', ...),
  ('xai', 'xAI Grok', 'model', 'oidc_discovery', 'credential_pool', ...),
  ('gemini', 'Gemini Code Assist', 'model', 'pkce_loopback', 'credential_pool', ...),
  ('qwen', 'Qwen OAuth', 'model', 'pkce_loopback', 'credential_pool', ...);

-- 服务器认证 (0 built-in, user-configured)
-- 用户可以添加自己的 OAuth server provider
```

---

## 四、引擎架构

### 4.1 `OAuthEngine` 核心类

```
agent/oauth/
  ├── __init__.py          # OAuthEngine, register_provider()
  ├── flows/
  │   ├── pkce_loopback.py    # PKCE + localhost:18923 callback
  │   ├── device_code.py      # Device authorization grant
  │   ├── oidc_discovery.py   # OIDC .well-known/openid-configuration
  │   └── trusted_header.py   # X-Authenticated-User proxy injection
  ├── storage.py              # Token CRUD (encrypt/decrypt)
  ├── providers/
  │   ├── login/
  │   │   ├── github.py       # GitHubOAuthProvider
  │   │   ├── google.py
  │   │   ├── gitee.py
  │   │   └── azure_ad.py
  │   └── model/
  │       ├── ontoweb.py
  │       ├── codex.py
  │       ├── xai.py
  │       ├── gemini.py
  │       └── qwen.py
  └── server_auth.py          # Server-to-server OAuth validation
```

### 4.2 核心流程

```python
class OAuthEngine:
    """Single entry point for all OAuth flows."""

    def __init__(self, config, db):
        self._providers = self._load_providers(db, config)  # DB → config.yaml → builtins

    # ── 统一授权入口 ──
    def start_authorize(self, provider_id: str, usage: str, **kwargs) -> OAuthSession:
        """Start PKCE/DeviceCode/OIDC flow. Returns OAuthSession with state."""
        ...

    def complete_authorize(self, session: OAuthSession, callback_params: dict) -> OAuthResult:
        """Complete the flow: exchange code → token → claims → store → return result."""
        ...

    # ── Token 管理 ──
    def get_token(self, provider_id: str, member_id: str = None) -> OAuthToken | None:
        """Get stored token, auto-refresh if needed."""
        ...

    def refresh_token(self, provider_id: str, member_id: str = None) -> OAuthToken:
        ...

    def revoke_token(self, provider_id: str, member_id: str = None) -> bool:
        ...

    # ── 回调服务器 ──
    def start_callback_server(self, port: int = 18923) -> None:
        """Start the shared localhost HTTP callback server."""
        ...
```

### 4.3 三种用途的差异化处理

```python
@dataclass
class OAuthResult:
    provider_id: str
    usage: str            # 'login' | 'model' | 'server'
    access_token: str
    refresh_token: str | None
    expires_in: int
    claims: dict           # {sub, email, name, ...}

    # ── 用途特定的输出 ──
    def resolve(self, db) -> Any:
        """Resolve the OAuth result based on usage type."""
        match self.usage:
            case 'login':
                member_id = resolve_oauth_member(self.provider_id, self.claims, db)
                return OAuthLoginResult(member_id=member_id)
            case 'model':
                credential = store_to_credential_pool(self.provider_id, self.access_token)
                return OAuthModelResult(credential=credential)
            case 'server':
                api_token = issue_member_api_token(self.claims, self.access_token, db)
                return OAuthServerResult(api_token=api_token)
```

---

## 五、CLI 管理命令

```bash
# 列出所有 OAuth provider
intellect oauth list [--usage login|model|server]

# 启用/禁用
intellect oauth enable <provider_id>
intellect oauth disable <provider_id>

# 添加第三方 OAuth provider
intellect oauth add \
  --id my-okta \
  --name "Okta SSO" \
  --usage login \
  --flow oidc_discovery \
  --client-id xxx \
  --client-secret xxx \
  --discovery-url https://myorg.okta.com/.well-known/openid-configuration

# 删除第三方 provider（内置不可删除）
intellect oauth remove my-okta

# 查看 provider 详情
intellect oauth show github

# 重新配置
intellect oauth config github \
  --client-id new-id \
  --client-secret new-secret
```

---

## 六、扩展性设计

### 6.1 企业 SSO 示例

```bash
# 添加企业 Okta
intellect oauth add \
  --id mycorp-okta \
  --name "MyCorp Okta" \
  --usage login \
  --flow oidc_discovery \
  --client-id "0oa1a2b3c4d5e6f7g8h9" \
  --discovery-url "https://mycorp.okta.com/.well-known/openid-configuration" \
  --scopes "openid,profile,email,groups" \
  --claim-sub "sub" \
  --claim-email "email"

# 启用
intellect oauth enable mycorp-okta

# 用户登录
intellect members login --oauth mycorp-okta
```

### 6.2 第三方 OAuth 配置文件（WebUI 友好格式）

```json
// oauth-providers/okta.json
{
  "id": "mycorp-okta",
  "name": "MyCorp Okta",
  "usage": "login",
  "auth_flow": "oidc_discovery",
  "client_id": "0oa1a2b3c4d5e6f7g8h9",
  "client_secret_encrypted": "<fernet>",
  "oidc_discovery_url": "https://mycorp.okta.com/.well-known/openid-configuration",
  "scopes": ["openid", "profile", "email", "groups"],
  "claim_sub": "sub",
  "claim_email": "email"
}
```

WebUI 可通过 API 上传此 JSON 文件完成配置。

### 6.3 API 端点

```
GET  /api/oauth/providers              — 列出所有 provider
POST /api/oauth/providers              — 添加 provider (JSON body)
PUT  /api/oauth/providers/<id>          — 更新配置
DELETE /api/oauth/providers/<id>       — 删除 provider

POST /api/oauth/authorize              — 启动授权流程
  → {provider_id, redirect_uri, state}

GET  /api/oauth/callback?code=...&state=...  — 回调处理
  → {member_id, access_token, ...}

POST /api/oauth/token/refresh          — 刷新 token
```

---

## 七、向后兼容

| 现有功能 | 迁移后行为 |
|----------|-----------|
| `config.yaml` → `members.oauth.providers[]` | **已废弃**；用 `intellect oauth migrate-from-config` 迁入 DB，运行时仅作凭证 overlay |
| `auth.json` → `providers.*` / `credential_pool` | A4：首次启动部分迁移到 `oauth_tokens`；**全面弃用**见 [auth-json-deprecation-pr-plan.md](auth-json-deprecation-pr-plan.md) |
| `agent/members_oauth.py` | 内部调用 `OAuthEngine`，外部 API 不变 |
| `intellect_cli/auth.py` OAuth 函数 | 逐步替换为 `OAuthEngine` 调用 |
| `--oauth github` CLI 标志 | 不变，内部路由到 `OAuthEngine` |

---

## 八、实施计划

| Phase | 内容 | 文件 | 时间 |
|-------|------|------|------|
| **1** | Schema v19: `oauth_providers` + `oauth_tokens` 表 + 预置数据 | `intellect_state.py` | 1h |
| **2** | `OAuthEngine` 核心类 + `OAuthFlow` ABC | `agent/oauth/` (新目录) | 3h |
| **3** | PKCE Loopback + Device Code 流程实现 | `agent/oauth/flows/` | 2h |
| **4** | Token 加密存储层（Fernet） | `agent/oauth/storage.py` | 1h |
| **5** | GitHub 登录 OAuth 迁移至新架构 | `agent/oauth/providers/login/github.py` | 1.5h |
| **6** | ONTOWEB 模型 OAuth 迁移 | `agent/oauth/providers/model/ontoweb.py` | 1.5h |
| **7** | CLI `intellect oauth *` 命令 | `intellect_cli/main.py` | 2h |
| **8** | 其余 8 个 provider 迁移 | 8 文件 | 4h |
| **9** | 向后兼容层 + 测试 | 各文件 | 3h |
| **10** | WebUI API 端点 | `gateway/` | 2h |
| **合计** | | | **21h** |

---

## 九、风险与缓解

| 风险 | 缓解 |
|------|------|
| 迁移期间破坏现有 OAuth 登录 | 向后兼容层保证旧路径仍然工作 |
| `client_secret` 明文存储风险 | Fernet 加密，密钥存于 `INTELLECT_HOME/.oauth-key` |
| Token 表并发写入 | SQLite WAL + `BEGIN IMMEDIATE` |
| 内置 provider 不可删除但可被错误修改 | `is_builtin=1` 保护，CLI 禁止删除，修改需 `--force` |

---

## 十、后续：DB-only provider 配置

废弃 `config.yaml` 的 `members.oauth.providers` 列表、统一登录流走 `OAuthEngine` 的分 PR 实施计划见 [`oauth-db-only-migration-pr-plan.md`](oauth-db-only-migration-pr-plan.md)。弃用 `auth.json` 见 [`auth-json-deprecation-pr-plan.md`](auth-json-deprecation-pr-plan.md)。含：

- 内置清单文件 `agent/oauth/catalog/builtin_providers.json` + 图标目录
- 企业微信 / 钉钉 / 飞书内置登录 Provider
- 首次 `intellect setup` 种子写入 `oauth_providers`

WebUI 索引：[`intellect-webui/docs/plans/oauth-db-only-migration-pr-plan.md`](../../intellect-webui/docs/plans/oauth-db-only-migration-pr-plan.md)。
