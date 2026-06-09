# OAuth「只配 DB、不配 YAML」迁移 PR 计划

**日期：** 2026-06-04（2026-06-04 修订：企业微信/钉钉/飞书内置目录 + `intellect setup` 种子）  
**状态：** ✅ A0–A4 + WebUI W3–W4 已实施；模型 OAuth 弃用 `auth.json` 见 [auth-json-deprecation-pr-plan.md](auth-json-deprecation-pr-plan.md)  
**关联：** [`oauth-unified-platform.md`](oauth-unified-platform.md)、[`../intellect-webui/docs/plans/oauth-unified-webui-implementation.md`](../../intellect-webui/docs/plans/oauth-unified-webui-implementation.md)（WebUI stub：[`oauth-db-only-migration-pr-plan.md`](../../intellect-webui/docs/plans/oauth-db-only-migration-pr-plan.md)）、**后续** [`auth-json-deprecation-pr-plan.md`](auth-json-deprecation-pr-plan.md)（A5–A10 弃用 `auth.json`）

---

## 1. 背景

OAuth 统一平台（Schema v19）已具备 `oauth_providers` / `oauth_tokens` 表与 `OAuthEngine`，但运行时仍存在 **双路径**：

| 路径 | Provider 来源 | 授权/回调 |
|------|---------------|-----------|
| **新** | `OAuthEngine`：DB → config.yaml → builtins | 部分 CLI / `intellect oauth` |
| **旧** | `members_oauth`：仅 `members.oauth.providers[]` | WebUI `/api/members/oauth/*`、部分 CLI |

此外，内置登录 Provider 仍硬编码在 `intellect_state._seed_oauth_providers()` 的 Python 元组中，**未包含** 企业微信（WeCom）、钉钉（DingTalk）、飞书（Feishu/Lark）；飞书仅在 WebUI 前端 `member-oauth-providers.js` 有图标，agent 登录流尚未纳入。

本计划将：

1. **成员登录 OAuth 的 provider 定义** 迁为 **DB 唯一真源**（废弃 `config.yaml` 的 `members.oauth.providers` 列表）。
2. **内置 Provider 目录** 改为仓库内 **单一文本型清单文件** + 图标资源目录，在 **首次 `intellect setup`**（及 `SessionDB` 首次迁移）时 **幂等写入** `oauth_providers`。
3. **企业微信、钉钉、飞书** 作为 **内置登录** Provider 纳入目录（`is_builtin=1`，默认 `enabled=0`，待管理员在 Settings 填写凭证后启用）。

---

## 2. 目标与非目标

### 2.1 目标

- 内置 Provider（含 github / google / gitee / azure_ad / **wecom / dingtalk / feishu** + 模型类 ontoweb 等）从 **目录文件** 种子到 DB，不再维护 Python 硬编码元组。
- **第一次**（及升级后首次打开 DB）运行 `intellect setup` 或 `SessionDB` 迁移时，执行 `seed_builtin_oauth_providers()`（`INSERT OR IGNORE` + 可选 metadata 刷新）。
- `resolve_provider()` / 列表 / authorize 与 **同一 DB 行** 一致（见 PR-A1/A2）。
- 图标：`icon.path` 指向包内 SVG/PNG，种子时写入 `logo_svg` / `logo_path` / `logo_type`。
- YAML → DB 迁移命令（PR-A3）与 doctor 更新。

### 2.2 非目标（另开里程碑）

- ~~模型 OAuth **全面弃用 `auth.json`**~~ → ✅ [**auth-json-deprecation-pr-plan.md**](auth-json-deprecation-pr-plan.md)（A5–A10 / W5–W7，`v0.4.1`）。
- OAuth `state` 从文件迁 DB。
- 删除整个 `members.oauth` config 段（仅废弃 `providers` 数组；`enabled`、`trusted_header` 等保留）。

---

## 3. 内置 Provider 目录文件（数据结构设计）

### 3.1 文件布局（agent 包内，随 wheel 分发）

```text
agent/oauth/catalog/
  builtin_providers.json    # 主清单（UTF-8 JSON，人类可编辑的「文本型」文件）
  icons/
    github.svg
    google.svg
    gitee.svg
    azure_ad.svg
    wecom.svg
    dingtalk.svg
    feishu.svg               # 飞书（Lark 国际版可共用或另备 lark.svg）
  README.md                  # 字段说明与新增内置 provider 流程
```

**格式选择：** 使用 **JSON 数组**（单文件、可 `jq` 校验、比 JSONL 更易一次加载）。顶层带 `schema_version` 便于向前兼容。

### 3.2 顶层结构

```json
{
  "schema_version": 1,
  "catalog_id": "intellect-oauth-builtin-v1",
  "providers": [ /* BuiltinProviderRecord[] */ ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | int | 解析器版本；未知版本时拒绝写入并打日志 |
| `catalog_id` | string | 可选；写入 marker 文件便于检测目录升级 |
| `providers` | array | 每条对应一行 `oauth_providers`（部分列默认） |

### 3.3 单条 Provider 记录：`BuiltinProviderRecord`

```json
{
  "id": "wecom",
  "name": "WeCom",
  "name_i18n": { "zh-Hans": "企业微信", "en": "WeCom" },
  "usage": "login",
  "auth_flow": "oauth2_wecom",
  "enabled_default": false,
  "display_order": 10,
  "is_builtin": true,
  "pkce": false,
  "token_storage": "identities",
  "scopes": ["snsapi_base"],
  "claim_sub": "UserId",
  "claim_email": "",
  "claim_name": "UserName",
  "endpoints": {
    "authorize_url": "https://open.weixin.qq.com/connect/oauth2/authorize",
    "token_url": "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
    "userinfo_url": "",
    "device_code_url": "",
    "revoke_url": "",
    "oidc_discovery_url": ""
  },
  "icon": {
    "type": "svg",
    "path": "icons/wecom.svg",
    "brand_bg": "#0082ef"
  },
  "tenant_config_defaults": {},
  "credential_fields": [
    { "key": "corp_id", "label": "Corp ID", "required": true, "scope": "tenant" },
    { "key": "agent_id", "label": "Agent ID", "required": true, "scope": "tenant" },
    {
      "key": "client_secret",
      "label": "Secret",
      "required": true,
      "secret": true,
      "env": "WECOM_OAUTH_CLIENT_SECRET",
      "db_column": "client_secret_encrypted"
    }
  ],
  "aliases": [],
  "description": "WeCom OAuth2 (corpId + agentId)."
}
```

#### 字段说明

| 字段 | 必填 | 映射 `oauth_providers` 列 |
|------|:---:|----------------------------|
| `id` | ✓ | `id` |
| `name` | ✓ | `name` |
| `usage` | ✓ | `usage`：`login` \| `model` \| `server` \| `both` |
| `auth_flow` | ✓ | `auth_flow`（见 §3.4） |
| `enabled_default` | | `enabled`（0/1）；内置登录默认 **0**，避免无凭证时出现在登录页 |
| `display_order` | | `display_order` |
| `is_builtin` | | `is_builtin`（恒为 1） |
| `pkce` | | `pkce` |
| `token_storage` | | `token_storage` |
| `scopes` | | `scopes`（JSON 数组字符串） |
| `claim_*` | | `claim_sub` / `claim_email` / `claim_name` |
| `endpoints.*` | | 对应 URL 列 |
| `icon` | | `logo_type`, `logo_path`, `logo_svg`（见 §3.5） |
| `tenant_config_defaults` | | `tenant_config`（JSON）；wecom/dingtalk/feishu 特有键 |
| `credential_fields` | | **不直接落库**；供 WebUI Settings 动态表单与 doctor 提示 |
| `aliases` | | **不直接落库**；如 `["lark"]`，运行时解析 `feishu` |

**说明：** `client_id` / `client_secret` 在种子时 **留空**；管理员通过 WebUI Settings 或 `intellect oauth config` 写入 DB（secret 加密）。

### 3.4 `auth_flow` 枚举（登录内置）

| `auth_flow` | Provider | 运行时 |
|-------------|----------|--------|
| `pkce_loopback` | github, google, gitee | OAuthEngine / members_oauth PKCE |
| `oidc_discovery` | azure_ad | OIDC discovery |
| `oauth2_wecom` | wecom | `build_wecom_authorization_url` / `_exchange_wecom_code` |
| `oauth2_dingtalk` | dingtalk | `build_dingtalk_authorization_url` / `_exchange_dingtalk_code` |
| `oauth2_feishu` | feishu | **新增** Feishu/Lark 授权与换票（对齐开放平台文档） |
| `device_code` | ontoweb 等 | `usage=model` |

PR-A2 须保证 `OAuthEngine` / `members_oauth` 对 `oauth2_*` 从 DB 行还原 `corp_id` / `agent_id` / `app_key`（存入 `tenant_config` JSON）。

### 3.5 `icon` 对象

```json
"icon": {
  "type": "svg",
  "path": "icons/feishu.svg",
  "brand_bg": "#ffffff"
}
```

| 字段 | 说明 |
|------|------|
| `type` | `svg` \| `png` \| `path` → `logo_type` |
| `path` | 相对 `agent/oauth/catalog/` 的路径；种子时若文件存在则读入 |
| `inline_svg` | 可选；若提供则优先写入 `logo_svg`，忽略 `path` |
| `brand_bg` | 可选；WebUI 登录按钮背景（API 可映射为 `brand_bg` 扩展字段，或仅存 `description` JSON） |

**种子规则：**

1. 若 `inline_svg` 非空 → `logo_svg` = 内容，`logo_type` = `svg`。
2. 否则若 `path` 指向 `.svg` → 读文件写入 `logo_svg`。
3. 否则若 `.png` → `logo_path` = 相对路径或复制到 `{INTELLECT_HOME}/oauth/icons/<id>.png`（实现时二选一，文档写死一种）。
4. `logo_path` 列供 WebUI `GET /api/oauth/providers/{id}/logo` 回退。

### 3.6 内置登录 Provider 清单（v1 种子）

| `id` | `name` | `auth_flow` | `display_order` | 备注 |
|------|--------|-------------|-----------------|------|
| github | GitHub | pkce_loopback | 0 | |
| google | Google | pkce_loopback | 1 | |
| gitee | Gitee | pkce_loopback | 2 | |
| azure_ad | Azure AD | oidc_discovery | 3 | |
| **wecom** | WeCom / 企业微信 | oauth2_wecom | 10 | `tenant`: corp_id, agent_id |
| **dingtalk** | DingTalk / 钉钉 | oauth2_dingtalk | 11 | client_id = app_key |
| **feishu** | Feishu / 飞书 | oauth2_feishu | 12 | `aliases`: `lark`；domain 可配置 |
| ontoweb | ONTOWEB Portal | device_code | 20 | `usage=model` |
| openai_codex | OpenAI Codex | pkce_loopback | 21 | `usage=model` |
| … | 其余模型内置 | … | … | 与现 `_seed_oauth_providers` 对齐 |

### 3.7 示例：`feishu` 记录（草案）

```json
{
  "id": "feishu",
  "name": "Feishu",
  "name_i18n": { "zh-Hans": "飞书", "en": "Feishu" },
  "usage": "login",
  "auth_flow": "oauth2_feishu",
  "enabled_default": false,
  "display_order": 12,
  "is_builtin": true,
  "pkce": true,
  "token_storage": "identities",
  "scopes": ["contact:user.base:readonly"],
  "claim_sub": "open_id",
  "claim_email": "email",
  "claim_name": "name",
  "endpoints": {
    "authorize_url": "https://accounts.feishu.cn/open-apis/authen/v1/authorize",
    "token_url": "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
    "userinfo_url": "https://open.feishu.cn/open-apis/authen/v1/user_info"
  },
  "icon": { "type": "svg", "path": "icons/feishu.svg", "brand_bg": "#ffffff" },
  "tenant_config_defaults": { "domain": "feishu" },
  "credential_fields": [
    { "key": "client_id", "label": "App ID", "required": true, "db_column": "client_id" },
    { "key": "client_secret", "label": "App Secret", "required": true, "secret": true, "db_column": "client_secret_encrypted" }
  ],
  "aliases": ["lark"]
}
```

（具体 URL 以实现时对照飞书开放平台当前文档为准。）

---

## 4. 种子写入时机与行为

### 4.1 函数：`seed_builtin_oauth_providers(db, *, force_metadata=False)`

**位置：** `agent/oauth/builtin_catalog.py`（新模块）

| 步骤 | 行为 |
|------|------|
| 1 | 加载 `builtin_providers.json`，校验 `schema_version` |
| 2 | 对每条 provider：`INSERT OR IGNORE` 按 `id` |
| 3 | 新插入行：写入 endpoints、scopes、claims、`is_builtin=1`、`enabled=enabled_default` |
| 4 | 图标：按 §3.5 填充 `logo_*` |
| 5 | **不覆盖** 已有行的 `client_id` / `client_secret_encrypted` / `enabled`（管理员已改） |
| 6 | `force_metadata=true`（仅 CLI）：刷新内置行的 URL/scopes/logo（仍不碰 secret） |

### 4.2 调用点

| 调用点 | 何时 |
|--------|------|
| `SessionDB` schema 迁移至 ≥ v19 | 替代 `intellect_state._seed_oauth_providers()` 内联 SQL，改为调用 `seed_builtin_oauth_providers` |
| **`intellect setup`（完整向导）** | `ensure_intellect_home()` 之后、`run_setup_wizard` 结束前：打开 `SessionDB()` 触发迁移 + 种子（保证 **第一次 setup** 必有 DB 行） |
| `intellect oauth seed-builtin` | 手动幂等重放（运维） |

**Marker：** `{INTELLECT_HOME}/.oauth_builtin_catalog_seeded` 记录 `catalog_id` + 时间戳；`catalog_id` 升级时可提示运行 `intellect oauth seed-builtin --refresh-metadata`。

### 4.3 与 `intellect setup` 的 UX

- Setup 完成摘要增加一行：`OAuth: N built-in providers loaded into state.db`（不打印 secret）。
- 非交互 `intellect setup`：同样执行种子（仅 DB，无 TTY 菜单）。

---

## 5. 合并顺序（修订）

```text
agent PR-A0（目录 + setup 种子）
    → agent PR-A1 → A2 → A3
         ↘ webui PR-W0（图标/企业 provider 展示，可选与 A0 并行）
         ↘ webui PR-W1 → W2 → W3
```

---

## 6. intellect-agent PR 拆分

### PR-A0：内置目录文件 + `intellect setup` 种子 ✅

**标题：** `feat(oauth): builtin provider catalog file and setup-time DB seed`

| 模块 | 改动 |
|------|------|
| 新 `agent/oauth/catalog/builtin_providers.json` | §3 清单 + 图标资源 |
| 新 `agent/oauth/builtin_catalog.py` | 解析、校验、种子写入 |
| `intellect_state.py` | `_seed_oauth_providers` → 委托 `builtin_catalog.seed_*` |
| `intellect_cli/setup.py` | `run_setup_wizard` / `ensure_intellect_home` 路径调用种子 |
| `intellect_cli/main.py` | `intellect oauth seed-builtin [--refresh-metadata]` |
| `agent/members_oauth.py` | 增加 `feishu` preset + `oauth2_feishu` 授权/换票；`OAUTH_PROVIDER_PRESETS` 与目录对齐 |
| `agent/oauth/__init__.py` | `_builtin_providers()` 可从 DB 读；硬编码降为回退 |
| `tests/agent/test_oauth_builtin_catalog.py` | 解析、种子幂等、wecom/dingtalk/feishu 行存在 |
| `tests/intellect_cli/test_setup_oauth_seed.py` | setup 后 DB 含 github + feishu 内置行 |

**验收：**

```bash
rm -f ~/.intellect/state.db   # 仅测试环境
intellect setup               # 交互或 quick
sqlite3 ~/.intellect/state.db "SELECT id, usage, auth_flow, is_builtin FROM oauth_providers ORDER BY display_order;"
# 期望: github, google, gitee, azure_ad, wecom, dingtalk, feishu, ontoweb, ...
```

---

### PR-A1：Provider 解析真源改为 DB ✅

（`provider_resolution.py`、`OAuthEngine` YAML overlay；**依赖 A0**。）

- `OAuthEngine.list_providers` 合并顺序改为：**catalog/DB 为准**，YAML 仅 legacy。
- doctor：DB 中 `enabled=1` 的 login provider；提示 wecom/dingtalk/feishu 需 `credential_fields` 完整。

---

### PR-A2：登录 OAuth 流程走 OAuthEngine ✅

（`login_flow.py`；企业 OAuth 须实网/集成测试验证 `tenant_config`。）

---

### PR-A3：YAML → DB 迁移 + 文档 ✅

- `agent/oauth/migrate_from_config.py` — `migrate_yaml_providers_to_db()`
- CLI：`intellect oauth migrate-from-config [--dry-run] [--write-config] [--force-secrets] [--force-client-id]`
- Marker：`~/.intellect/.oauth_yaml_providers_migrated`
- Docs：`website/docs/user-guide/features/teams-and-members.md`

---

### PR-A4：模型 OAuth 经 `oauth_providers` + `oauth_tokens` ✅

- `agent/oauth/model_tokens.py` — runtime id 别名、`persist_model_token` / `delete_model_token` / `model_token_auth_status`
- `OAuthEngine.has_model_token` / `store_model_token` / `get_model_token` / `revoke_model_token`
- `auth_json_migration` — 同时迁移 `credential_pool` → `oauth_tokens`（加密）
- `intellect_cli.auth.get_auth_status` — Codex/xAI/Qwen 优先读 DB；`clear_provider_auth` 删除 DB 行

---

## 7. intellect-webui PR 拆分

> 索引：[`intellect-webui/docs/plans/oauth-db-only-migration-pr-plan.md`](../../intellect-webui/docs/plans/oauth-db-only-migration-pr-plan.md)

### PR-W0（可选，建议与 agent A0 同期）：企业内置 Provider UI

- `static/oauth-providers.js` / `member-oauth-providers.js`：优先使用 API 返回的 `logo_svg` / `logo_path`，减少硬编码 `ICON_ART` 重复。
- Settings 动态表单：读取 API 扩展字段 `credential_fields`（或由 agent 在 `GET /api/oauth/providers` 附带）。
- `docs/members-oauth-webui.md`：飞书/企微/钉钉配置说明。

### PR-W1～W3

（同前版。）

---

## 8. 迁移后配置示例

```yaml
members:
  enabled: true
  oauth:
    enabled: true
    # providers: []   # DEPRECATED
```

**内置行：** 由 `intellect setup` 种子写入，**无需** YAML。启用登录：

- WebUI：**Settings → Auth Services** → 选择 WeCom / DingTalk / Feishu → 填写 AppId/Secret（或 corp/agent）→ Enable
- CLI：`intellect oauth config wecom --corp-id ... --agent-id ...`（命令以实现为准）

---

## 9. 联调验收清单（增补）

| # | 检查项 |
|---|--------|
| 0 | 新装：`intellect setup` 后 `oauth_providers` 含 wecom、dingtalk、feishu 且 `is_builtin=1` |
| 1 | 仅 DB 启用 feishu + 凭证后，`/login` 显示飞书图标且可发起授权 |
| 2～6 | （同前版 DB-only / 登录 / doctor / migrate-from-config） |

---

## 10. 回滚

| 层级 | 做法 |
|------|------|
| Agent | 保留旧 `_seed_oauth_providers` 元组函数，feature flag `oauth.use_catalog_file: false` |
| 数据 | 不删除已种子行；回滚代码即可 |

---

## 11. PR 粒度参考（修订）

| 仓库 | PR | 约 LOC | 阻塞 |
|------|-----|--------|------|
| agent | **A0** | ~550 | A1, W0 |
| agent | A1 | ~400 | W1 |
| agent | A2 | ~700 | W2 |
| agent | A3 | ~250 | W3 |
| webui | W0 | ~200 | 可选 |
| webui | W1～W3 | ~700 | 同前 |

**预计 7～8 个 PR**（含 A0 企业内置与 setup 种子）。
