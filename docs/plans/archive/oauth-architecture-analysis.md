# OAuth 架构分析与统一设计

**日期：** 2026-06-02

---

## 一、两套 OAuth 体系对比

### 1.1 登录 OAuth（Member Authentication）

**目的：** 证明"我是谁"——绑定外部身份到 member

**文件：** `agent/members_oauth.py`

| 维度 | 现状 |
|------|------|
| 配置 | `config.yaml` → `members.oauth.providers[]` 列表 |
| Provider | GitHub, Google, Gitee, Azure AD（4 个内置） |
| 流程 | PKCE → 浏览器授权 → 回调 → token 交换 → claims 提取 → identities 表绑定 |
| Token 存储 | `identities.raw` 列（JSON blob） |
| 代码位置 | `agent/members_oauth.py`（独立模块） |

```
用户 → members login --oauth github
  → 浏览器打开 GitHub 授权页
  → 回调 http://127.0.0.1:18923/callback
  → 交换 code → access_token
  → 提取 claims (sub, email, name)
  → 查 identities 表 (provider='oauth:github', provider_id='gh-12345')
  → 找到 member_id → 登录成功
  → 找不到 → 提示先注册
```

### 1.2 模型 Provider OAuth（API Access）

**目的：** 获取调用 LLM API 的凭证

**文件：** `intellect_cli/auth.py`（分散在多个函数中）

| Provider | 函数 | 流程 |
|----------|------|------|
| ONTOWEB | `_nous_device_code_login()` | Device Code → 浏览器 → 轮询 → inference JWT |
| OpenAI Codex | `refresh_codex_oauth_pure()` | PKCE → 浏览器 → token → refresh token 管理 |
| xAI Grok | `_xai_oauth_discovery()` | OIDC Discovery → PKCE → token |
| Gemini | `resolve_gemini_oauth_runtime_credentials()` | Google OAuth → ADC/CLI token |
| Spotify | `_refresh_spotify_oauth_state()` | PKCE → token → refresh |

| 维度 | 现状 |
|------|------|
| 配置 | 硬编码在各 provider 注册函数中 + `config.yaml` |
| 流程 | 每个 provider 独立实现，无共享抽象 |
| Token 存储 | `auth.json` → `providers.<name>` → `access_token`, `refresh_token` |
| 刷新 | 各自独立实现 refresh 逻辑 |

---

## 二、关键差异

| 维度 | 登录 OAuth | Provider OAuth |
|------|-----------|---------------|
| **目的** | 身份证明 | API 访问 |
| **输出** | `member_id` | `access_token` / `api_key` |
| **存储** | `identities` 表 | `auth.json` 文件 |
| **Token 刷新** | 不适用（PKCE 一次性） | refresh_token → access_token |
| **作用域** | `openid profile email` | provider-specific (`inference:invoke`, `repo`, etc.) |
| **回调** | `http://127.0.0.1:18923/callback` | 各 provider 不同 |
| **配置位置** | `members.oauth.providers` | 散落各处 |
| **抽象层** | 共享（`members_oauth.py`） | 无共享（每个 provider 独立实现） |

---

## 三、通用架构设计

### 3.1 `OAuthProvider` 协议

```python
# agent/oauth_provider.py (新)

@dataclass
class OAuthProviderConfig:
    id: str                          # 'github', 'google', 'ontoweb', 'xai'
    name: str                        # display name
    type: str                        # 'identity' | 'api' | 'both'
    auth_method: str                 # 'pkce_loopback' | 'device_code' | 'oidc'
    client_id: str
    client_secret: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    scopes: list[str] = field(default_factory=list)
    pkce: bool = True
    tenant_specific: bool = False     # Azure AD multi-tenant


class OAuthProvider(ABC):
    """Shared OAuth flow abstraction for identity + API providers."""

    config: OAuthProviderConfig

    # -- 子类必须实现 --
    @abstractmethod
    def build_authorize_url(self, redirect_uri: str, state: str, challenge: str | None) -> str: ...
    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> dict: ...
    @abstractmethod
    def extract_claims(self, token_response: dict) -> dict: ...

    # -- 可选覆盖 --
    def refresh_token(self, refresh_token: str) -> dict | None:
        """API-type providers override this."""
        return None
    def revoke_token(self, token: str) -> bool:
        return False

    # -- 共享方法 --
    def run_loopback_flow(self, redirect_uri: str, port: int = 18923) -> dict: ...
    def run_device_code_flow(self) -> dict: ...
```

### 3.2 数据库驱动配置

将 OAuth provider 配置从 `config.yaml` 迁入 `oauth_providers` 表：

```sql
-- Schema v19
CREATE TABLE IF NOT EXISTS oauth_providers (
    id TEXT PRIMARY KEY,              -- 'github', 'google', 'ontoweb', 'xai'
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'identity',  -- 'identity' | 'api' | 'both'
    auth_method TEXT NOT NULL,        -- 'pkce_loopback' | 'device_code' | 'oidc'
    enabled INTEGER NOT NULL DEFAULT 0,
    client_id TEXT NOT NULL DEFAULT '',
    client_secret TEXT NOT NULL DEFAULT '',
    authorize_url TEXT NOT NULL DEFAULT '',
    token_url TEXT NOT NULL DEFAULT '',
    userinfo_url TEXT NOT NULL DEFAULT '',
    scopes TEXT NOT NULL DEFAULT '[]',      -- JSON array
    pkce INTEGER NOT NULL DEFAULT 1,
    tenant_specific INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL
);
```

**预置数据：** 4 个内置登录 provider + 5 个 API provider

### 3.3 配置加载优先级

```
1. oauth_providers 表（数据库）       ← 运行时可变，支持 CLI 增删
2. config.yaml members.oauth.providers ← 文件回退（向后兼容）
3. 硬编码内置列表                      ← 最终回退（保证基础功能）
```

加载逻辑：
```python
def load_oauth_providers(config, db) -> list[OAuthProviderConfig]:
    # 1. Try database
    if db:
        rows = db.conn.execute(
            "SELECT * FROM oauth_providers WHERE enabled=1 ORDER BY display_order"
        ).fetchall()
        if rows:
            return [OAuthProviderConfig(**dict(r)) for r in rows]
    # 2. Fall back to config.yaml
    yaml_providers = config.get("members", {}).get("oauth", {}).get("providers", [])
    if yaml_providers:
        return [OAuthProviderConfig(**p) for p in yaml_providers]
    # 3. Built-in defaults
    return BUILTIN_OAUTH_PROVIDERS
```

---

## 四、迁移路径

### Phase 1: 抽象层（不破坏现有功能）

| Step | 内容 | 文件 |
|------|------|------|
| 1.1 | 定义 `OAuthProviderConfig` + `OAuthProvider` ABC | `agent/oauth_provider.py`（新） |
| 1.2 | 将 GitHub OAuth 实现为 `GitHubOAuthProvider(OAuthProvider)` | `agent/oauth_providers/github.py`（新） |
| 1.3 | `resolve_oauth_member()` 改为通过 `OAuthProvider` 接口调用 | `agent/members_oauth.py` |
| 1.4 | 测试：GitHub OAuth 走新路径，行为不变 | tests |

### Phase 2: 数据库驱动配置

| Step | 内容 |
|------|------|
| 2.1 | Schema v19: `oauth_providers` 表 + 预置数据迁移 |
| 2.2 | `load_oauth_providers()` 实现三级回退 |
| 2.3 | CLI: `intellect members oauth list/enable/disable` |
| 2.4 | 测试 |

### Phase 3: Provider OAuth 迁移

| Step | 内容 |
|------|------|
| 3.1 | ONTOWEB provider → `OntowebOAuthProvider(OAuthProvider)` |
| 3.2 | xAI provider → `XAIOAuthProvider(OAuthProvider)` |
| 3.3 | Codex provider → `CodexOAuthProvider(OAuthProvider)` |
| 3.4 | 从 `auth.py` 移除旧实现 |
| 3.5 | 测试 |

---

## 五、回答两个问题

### Q1: 能否设计通用架构？

**可以。** 当前两套系统共享 ~70% 的流程（PKCE 生成、浏览器打开、回调接收、token 交换、claims 提取），差异仅在：
- Provider-specific URL 和参数
- ID 存储（identities 表 vs auth.json）
- Token 生命周期（一次性 vs refreshable）

上述 `OAuthProvider` ABC 将这些差异封装为子类的方法实现，共享的 PKCE/DeviceCode 流程、HTTP 回调服务器、state 管理全部提取到基类。

### Q2: 是否可以不用配置文件，用数据库管理？

**可以，且建议分阶段迁移。**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 纯文件（现状） | 简单，可版本控制，离线可用 | 运行时不可变，需重启 |
| 纯数据库 | 运行时可变，支持 CLI 管理 | 迁移复杂，出问题时难以恢复 |
| **数据库优先 + 文件回退**（推荐） | 灵活性 + 可靠性 | 需要加载逻辑 |

推荐的三级回退已在 §3.3 设计。

---

## 六、预计工作量

| Phase | 内容 | 时间 |
|-------|------|------|
| 1: 抽象层 | ABC + GitHub 实现 + 集成 | 4h |
| 2: DB 配置 | Schema + 迁移 + CLI | 3h |
| 3: Provider 迁移 | 5 个 provider 逐一迁移 | 5h |
| **合计** | | **12h** |
