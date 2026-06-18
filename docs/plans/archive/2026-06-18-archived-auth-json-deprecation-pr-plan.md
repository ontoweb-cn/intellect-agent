# `auth.json` 全面弃用迁移 PR 计划（A5–A10）

**日期：** 2026-06-04  
**状态：** ✅ A5–A10、W5–W7 已实施（`auth.json` OAuth 默认关闭；迁移/回滚开关保留）  
**关联：**

- 前置：[oauth-db-only-migration-pr-plan.md](oauth-db-only-migration-pr-plan.md)（成员登录 YAML → DB；模型 OAuth 双写 A4）
- 架构：[oauth-unified-platform.md](oauth-unified-platform.md)
- WebUI stub：[intellect-webui/docs/plans/auth-json-deprecation-pr-plan.md](../../intellect-webui/docs/plans/auth-json-deprecation-pr-plan.md)
- 相关：[2026-06-02-provider-registry-db-unification-design.md](2026-06-02-provider-registry-db-unification-design.md)（Provider 注册表，可并行但非阻塞）

---

## 1. 背景

Intellect 将 **模型侧 / CLI OAuth** 凭证长期保存在 `$INTELLECT_HOME/auth.json`（`intellect_cli/auth.py` 的 Auth Store）。运行时大量模块经 **`credential_pool`** 读该文件：

| 消费者 | 典型用途 |
|--------|----------|
| `intellect_cli/auth.py` | `get_auth_status`、`resolve_*_runtime_credentials`、`active_provider` |
| `agent/credential_pool.py` | 多凭证轮换、Codex/xAI DEAD 状态、failover |
| `api/oauth.py`（WebUI） | Codex 设备码、Anthropic 链接、disconnect |
| `run_agent.py` / `agent/auxiliary_client.py` | 推理取 key |
| Profile 模式 | profile `auth.json` 与 global `~/.intellect/auth.json` **只读 shadow**（#18594） |

Schema v19 已引入 `oauth_providers` + `oauth_tokens`（Fernet 加密）。**PR-A4** 已实现：

- 一次性 `auth_json_migration`（`providers.*` + `credential_pool` 最优条目 → DB）
- Codex/xAI/Qwen **status** DB 优先；Codex WebUI **双写** DB + `auth.json`
- `OAuthEngine.store_model_token` / `model_tokens.py` 运行时 id 别名

**仍未完成：** 推理与 pool **仍以 `auth.json` 为真源**；写入路径大多只更新文件；多凭证语义未完整落入 DB。

本计划将 **A5–A10** 定义为弃用 `auth.json` 的正式里程碑（与 YAML→DB 的 A0–A3、模型双写的 A4 衔接）。

---

## 2. 目标与非目标

### 2.1 目标

1. **读路径统一**：所有 OAuth 运行时取 token → `oauth_tokens`（+ 可选 pool 表）**优先**，`auth.json` 仅作可关闭的回退。
2. **写路径统一**：`intellect auth`、设备码换票、refresh、WebUI onboarding **默认只写 DB**；停止更新 `auth.json`（保留迁移期双写开关）。
3. **`credential_pool` 语义迁完**：多账号、priority、label、DEAD/quarantine、终端错误原因 — 在 DB 可表达且 `load_pool()` 行为不变。
4. **`active_provider` 迁出**：迁入 `config.yaml` / `state.db` 设置或显式 CLI 参数，不依赖 `auth.json` 顶层字段。
5. **运维**：`intellect oauth migrate-from-auth-json`、`doctor` 一致性检查、升级文档与回滚开关。
6. **WebUI（W5–W7）**：Settings / onboarding / Providers 面板只认 DB token 状态。

### 2.2 非目标（本里程碑不做或另开）

| 项 | 说明 |
|----|------|
| 成员登录 OAuth | 已由 A0–A3 覆盖（`members.oauth.providers` 废弃） |
| OAuth `state` 文件 → DB | 仍用 `create_oauth_state` 等文件态；另开 PR |
| 外部工具自有凭证文件 | `~/.codex/auth.json`、Claude Code `~/.claude/.credentials.json` — **链接/同步**，不替代 Intellect 主存储（见 §3.3） |
| 纯 API Key（`.env` / `config.yaml`） | 本来就不在 `auth.json` |
| 删除 `intellect_cli/auth.py` | 保留为薄封装，内部委托 `OAuthEngine` |
| MCP / Spotify 等工具 OAuth | A8 子集处理；未列出的可保留独立文件并文档化 |

---

## 3. `auth.json` 现状与 DB 目标

### 3.1 典型 `auth.json` 结构（v1 Auth Store）

```json
{
  "version": 1,
  "updated_at": "2026-06-04T12:00:00Z",
  "active_provider": "openai-codex",
  "providers": {
    "openai-codex": {
      "access_token": "…",
      "refresh_token": "…",
      "auth_mode": "chatgpt"
    }
  },
  "credential_pool": {
    "openai-codex": [
      {
        "id": "codex-oauth-abc123",
        "label": "Codex OAuth",
        "source": "manual:device_code",
        "priority": 0,
        "access_token": "…",
        "refresh_token": "…",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "last_refresh": "…"
      }
    ],
    "xai-oauth": [ "…" ]
  }
}
```

| 区块 | 用途 | DB 迁移策略 |
|------|------|-------------|
| `active_provider` | 默认 OAuth 模型商 | → `config.yaml` 键 `auth.active_provider`（`get_active_provider()`，`v0.4.1`）；legacy `auth.json` 仅当 `write/read_auth_json` |
| `providers.<id>` | 单例 provider 状态 | → `oauth_tokens`（`member_id IS NULL`），A5 读合并 |
| `credential_pool.<id>[]` | **运行时主路径** 多凭证 | → §3.2 pool 行或 `oauth_tokens.metadata`（A6–A7） |

**路径：** `{INTELLECT_HOME}/auth.json`；Profile 模式下另有 global 回退读（只读 shadow）。

### 3.2 凭证池的 DB 表达（二选一，A6 定稿）

**推荐：方案 B（新表，利于 UNIQUE 与轮换索引）**

```sql
-- Schema v20（草案）
CREATE TABLE IF NOT EXISTS oauth_pool_entries (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES oauth_providers(id),
    profile_scope TEXT NOT NULL DEFAULT '',  -- '' = default INTELLECT_HOME scope hash
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',       -- ok | exhausted | dead
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    base_url TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',     -- auth_mode, last_refresh, terminal_reason, …
    issued_at REAL NOT NULL,
    updated_at REAL,
    last_used_at REAL
);
CREATE INDEX IF NOT EXISTS idx_oauth_pool_provider ON oauth_pool_entries(provider_id, profile_scope, priority);
```

**备选：方案 A** — 仅扩展 `oauth_tokens.metadata` JSON 存 pool 字段；多行 per provider，`metadata.pool_entry_id` 唯一。适合先快速落地，长期查询/清理较差。

**映射规则：**

| `credential_pool` 字段 | DB 列 / metadata |
|------------------------|------------------|
| `id` | `oauth_pool_entries.id` |
| `label` | `label` |
| `source` | `source` |
| `priority` | `priority` |
| `access_token` | `access_token_encrypted` |
| `refresh_token` | `refresh_token_encrypted` |
| `base_url` | `base_url` |
| DEAD / exhausted | `status` |
| JWT 过期策略 | 仍由 runtime 解码 + `metadata` |

### 3.3 平行凭证文件（A8 收敛清单）

| 文件 / 路径 | Provider | 策略 |
|-------------|----------|------|
| `{INTELLECT_HOME}/auth.json` | 多模型 OAuth | **本计划删除写入** |
| `agent/google_oauth.py` → `auth/google_oauth.json` | `google-gemini-cli` | 迁入 `oauth_tokens` 或 adapter 只读同步 |
| `agent/anthropic_adapter` → `.anthropic_oauth.json` | `anthropic` | 同上；保留 Claude Code **链接** |
| Qwen CLI 路径 | `qwen-oauth` | `resolve_qwen_*` 读 DB 优先 |
| `intellect_cli/auth` xAI 块 | `xai-oauth` | 已部分 DB；A5 统一 |
| `tools/mcp_oauth.py` | MCP 服务器 | 文档化「外置」或单独 `oauth_tokens` usage=`tool` |
| Spotify / 其它工具 | 工具 OAuth | A8 子 PR 或明确永久外置 |

### 3.4 Runtime id 与 DB `provider_id` 别名

与 A4 `agent/oauth/model_tokens.py` 一致，全计划统一使用：

| Runtime（CLI / WebUI / pool key） | DB `oauth_providers.id` |
|--------------------------------|-------------------------|
| `openai-codex` | `openai_codex` |
| `xai-oauth` | `xai` |
| `qwen-oauth` | `qwen` |
| `google-gemini-cli` | `gemini` |
| `minimax-oauth` | `minimax` |
| `ontoweb` | `ontoweb` |

---

## 4. Feature flag 与迁移命令

### 4.1 配置键（`config.yaml` 或 env）

| 键 | 默认（阶段） | 说明 |
|----|--------------|------|
| `oauth.read_auth_json_fallback` | `true` → A9 后 `false` | 读：DB 无 token 时回退 `auth.json` |
| `oauth.write_auth_json` | `true` → A6 后 `false` | 写：是否继续双写文件 |
| `oauth.credential_pool_backend` | `auth_json` → `db` | pool 加载后端 |

Env 覆盖（实现时二选一文档写死）：`INTELLECT_OAUTH_READ_AUTH_JSON=0`、`INTELLECT_OAUTH_WRITE_AUTH_JSON=0`。

### 4.2 CLI

```bash
# 预览：providers + credential_pool → oauth_tokens / oauth_pool_entries
intellect oauth migrate-from-auth-json --dry-run

# 执行迁移；可选在成功后清空 auth.json 中的 OAuth 区块（保留 version 壳）
intellect oauth migrate-from-auth-json --write --prune-auth-json

# Doctor：DB vs auth.json 漂移、仍启用 write_auth_json 提示
intellect doctor
```

**Marker：** `{INTELLECT_HOME}/.oauth_auth_json_migrated`（时间戳 + 条目数）。

### 4.3 与 A4 现有迁移的关系

| 迁移 | Marker | 范围 |
|------|--------|------|
| A4 `migrate_auth_tokens` | `.oauth_tokens_migrated` | 每条 provider 一条最优 pool 行 → `oauth_tokens` |
| A5+ `migrate-from-auth-json` | `.oauth_auth_json_migrated` | **完整** pool 多行 + `providers` + 校验 |

A9 可合并两个 marker 逻辑，避免重复插入。

---

## 5. 合并顺序

```text
agent A5（读统一 + flag）
    → agent A6（写统一，停默认双写）
    → agent A7（pool 表 + profile_scope）
    → agent A8（外置 adapter 收敛，可并行 A7 后期)
    → agent A9（migrate CLI + doctor + 默认 fallback off）
    → agent A10（删除 auth.json 读写实现）
         ↘ webui W5（读/状态）→ W6（写/onboarding）→ W7（Providers 面板）
```

**阻塞：** WebUI W5+ 依赖 agent A5（至少 DB-first 读 status）。

---

## 6. intellect-agent PR 拆分（A5–A10）

### PR-A5：读路径 DB 优先（`credential_pool` + `resolve_*`）✅

**标题：** `feat(oauth): read model credentials from oauth_tokens before auth.json`

| 模块 | 改动 |
|------|------|
| `agent/oauth/model_tokens.py` | `load_pool_entries_from_db()`、`resolve_runtime_token()` |
| `agent/credential_pool.py` | `load_pool()`：若 `credential_pool_backend=db` 或 flag，从 DB 构造 `PoolEntry` |
| `intellect_cli/auth.py` | 所有 `resolve_*_runtime_credentials`、`get_*_auth_status` 统一经 `OAuthEngine` / DB |
| `run_agent.py`、`agent/auxiliary_client.py`、`agent/credential_sources.py` | 去直接 `_load_auth_store` 热路径 |
| `tests/agent/test_oauth_model_tokens.py`、`tests/agent/test_credential_pool.py` | DB fixture；`auth.json` 缺失仍可用 |

**验收：**

```bash
# 测试环境：仅有 DB token，auth.json 无对应 pool 条目
INTELLECT_HOME=/tmp/ih intellect oauth migrate-from-auth-json --write
mv ~/.intellect/auth.json ~/.intellect/auth.json.bak   # 仅试验
intellect auth list          # openai-codex 仍 logged_in
intellect model -m …         # 推理成功
```

---

### PR-A6：写路径只写 DB（可配置双写）✅

**标题：** `feat(oauth): stop writing auth.json by default for model OAuth`

| 模块 | 改动 |
|------|------|
| `intellect_cli/auth.py` | `_save_auth_store` / pool 写入 gated by `oauth.write_auth_json` |
| `intellect_cli/auth_commands.py`、`main.py` | `auth add` / refresh 只调 `OAuthEngine.store_model_token` + pool insert |
| `agent/credential_pool.py` | persist 写 DB |
| `agent/oauth/auth_json_migration.py` | 与 A9 命令共享 insert  helper |
| `tests/intellect_cli/test_auth_*.py` | 默认断言不写 auth.json（或 isolated tmp） |

**验收：** `oauth.write_auth_json: false` 下完成 Codex 设备码登录，DB 有行、`auth.json` mtime 不变。

---

### PR-A7：`credential_pool` 表 + Profile 作用域 ✅

**标题：** `feat(oauth): credential pool in state.db with profile scope`

**已实施：** Schema v22、`oauth_pool_entries`、`pool_storage.py`、读/写/迁移/断开路径。

| 模块 | 改动 |
|------|------|
| `intellect_state.py` | Schema v20 + `oauth_pool_entries` |
| `agent/oauth/pool_storage.py`（新） | CRUD、与 `credential_pool.PoolEntry` 互转 |
| `intellect_cli/auth.py` | global shadow：profile 无条目时读 `profile_scope='global'` |
| `tests/` | 轮换、DEAD、priority、#18594 shadow 用例 |

**验收：** 同一 provider 两条 pool 条目，轮换顺序与现 `auth.json` 行为一致（对照测试）。

---

### PR-A8：外置凭证 adapter 收敛 ✅

**标题：** `feat(oauth): unify gemini/anthropic/qwen token storage with oauth_tokens`

**已实施：** `google_oauth` / `anthropic_adapter` / Qwen CLI 路径 DB 优先读写；WebUI Anthropic 链接与 disconnect 清 DB；`onboarding._provider_oauth_authenticated` 查 `state.db`。

| 模块 | 改动 |
|------|------|
| `agent/google_oauth.py` | 可选双写 DB；读优先 DB |
| `agent/anthropic_adapter.py` | 链接流程写 `oauth_tokens` + pool marker |
| `intellect_cli/auth.py` | `get_gemini_oauth_auth_status` / Anthropic 状态 DB 优先 |
| `docs/` | 标明 MCP/Spotify 外置策略 |
| `tests/` | 各 adapter 回归 |

**验收：** Gemini CLI OAuth 后，`oauth_tokens` 有 `gemini` 行；删除 `google_oauth.json` 后 status 仍 logged_in（仅 DB）。

---

### PR-A9：迁移命令 + doctor + 默认关闭回退 ✅

**标题：** `feat(oauth): migrate-from-auth-json and doctor drift checks`

**已实施：** `migrate_from_auth_json.py`（含 pool 表）、CLI、`auth_json_drift.py` + doctor 警告。

| 模块 | 改动 |
|------|------|
| `agent/oauth/migrate_from_auth_json.py`（新） | 完整 pool 迁移、`--prune-auth-json` |
| `intellect_cli/main.py` | `intellect oauth migrate-from-auth-json` |
| `intellect_cli/doctor.py` | DB/file 不一致、建议 prune、flag 状态 |
| `config` 默认 | `read_auth_json_fallback: false`（新装可 true 一版本） |
| `website/docs/` | 升级说明、备份 `auth.json` |

**验收：**

```bash
intellect oauth migrate-from-auth-json --dry-run
intellect oauth migrate-from-auth-json --write --prune-auth-json
intellect doctor   # 无 auth.json OAuth 漂移警告
```

---

### PR-A10：移除 `auth.json` 读写（保留只读迁移一版）✅

**标题：** `refactor(oauth): remove auth.json runtime dependency`

**已实施：** 默认 `read_auth_json_fallback: false`、`credential_pool_backend: db`；`read_credential_pool` / pool sync / WebUI persist 与 status 默认不触盘 `auth.json`；`oauth_tokens` upsert 修复 NULL UNIQUE 重复行。

| 模块 | 改动 |
|------|------|
| `intellect_cli/auth.py` | 删除 `_load_auth_store` 热路径；保留 migrate 只读 |
| `agent/credential_pool.py` | 移除 `read_credential_pool` 文件实现 |
| `api/oauth.py`（agent 若存在） | — |
| 全仓库 grep `auth.json` | 测试改为 DB；文档更新 |
| `CHANGELOG` / `credential-pools.md` | 声明弃用 |

**验收：** 无 flag 时，代码路径不打开 `auth.json`；CI 全绿。

---

## 7. intellect-webui PR 拆分（W5–W7）

> 索引：[intellect-webui/docs/plans/auth-json-deprecation-pr-plan.md](../../intellect-webui/docs/plans/auth-json-deprecation-pr-plan.md)

### PR-W5：状态与 Providers 读 DB（依赖 A5）

| 模块 | 改动 |
|------|------|
| `api/providers.py` | `has_key` / `is_oauth` 来自 DB + `get_auth_status`（已无 auth.json 依赖） |
| `api/oauth.py` | `_resolve_oauth_provider_status` 仅 DB + 外置 adapter |
| `static/oauth-providers.js` | Model Auth「已认证」仅 `has_token` |

---

### PR-W6：Onboarding / 设备码单写 DB（依赖 A6）

| 模块 | 改动 |
|------|------|
| `api/oauth.py` | 移除 `_persist_codex_credentials` 写 `auth.json`；只 `persist_model_token` |
| `api/streaming.py` | 凭证缓存失效改 DB 事件 |
| `static/onboarding.js` | 文案去掉「auth.json」 |

---

### PR-W7：Disconnect / 导出与文档（依赖 A7–A9）

| 模块 | 改动 |
|------|------|
| `api/oauth.py` | `disconnect_oauth_provider` 清 pool 表 + tokens |
| `docs/members-oauth-webui.md`、`CHANGELOG.md` | 与 agent 文档对齐 |
| `tests/test_oauth_model_tokens_webui.py` | 无 auth.json fixture |

---

## 8. 弃用完成后运行时形态

```text
config.yaml          → model.default、api keys、auth.active_provider（可选）
state.db
  oauth_providers    → 提供商定义（内置 + 自定义）
  oauth_tokens       → 单例 token（或 member 绑定）
  oauth_pool_entries → 多凭证轮换（v20）
.env                 → API key 环境变量（非 OAuth）

auth.json            → 不存在或仅迁移工具只读；不再写入
```

**用户操作：**

- 登录模型 OAuth：WebUI onboarding / `intellect auth add` → 只更新 DB
- 查看状态：`intellect auth list`、`intellect doctor`
- 升级：`intellect oauth migrate-from-auth-json --write`

---

## 9. 联调验收清单

| # | 检查项 |
|---|--------|
| 1 | 新装 + A9 默认：`auth.json` 不存在，`intellect auth add openai-codex` 后 DB 有 `openai_codex` token + pool 行 |
| 2 | 重命名 `auth.json` 后 Codex 推理仍成功（仅 DB） |
| 3 | Pool 双条目：429/DEAD 后轮换下一条，与旧版行为一致 |
| 4 | Profile A 登录 OAuth，Profile B 不 shadow 除非配置 global 回退 |
| 5 | WebUI Settings → Providers：OAuth 卡「已配置」与 `intellect auth list` 一致 |
| 6 | WebUI Codex 设备码：成功后无 `auth.json` mtime 变化（A6+） |
| 7 | `intellect oauth migrate-from-auth-json` + `doctor` 无漂移 |
| 8 | `intellect logout openai-codex` 清空 DB 行；status 未登录 |
| 9 | Gemini/Anthropic（A8 范围）删平行 json 后仍可用 |
| 10 | 回滚：`oauth.read_auth_json_fallback: true` 恢复读文件（A10 前） |

---

## 10. 回滚

| 层级 | 做法 |
|------|------|
| 配置 | `oauth.read_auth_json_fallback: true`、`oauth.write_auth_json: true`、`credential_pool_backend: auth_json` |
| 数据 | 保留 `auth.json` 备份；DB 行不自动删除 |
| 代码 | A10 前各 PR 可独立 revert；A10 后需恢复 auth store 模块 |

---

## 11. PR 粒度参考

| 仓库 | PR | 约 LOC | 依赖 |
|------|-----|--------|------|
| agent | **A5** | ~800 | A4 |
| agent | A6 | ~500 | A5 |
| agent | A7 | ~900 | A6 |
| agent | A8 | ~600 | A5（可与 A7 并行部分） |
| agent | A9 | ~400 | A7 |
| agent | A10 | ~1200 | A9 |
| webui | W5 | ~300 | A5 |
| webui | W6 | ~400 | A6 |
| webui | W7 | ~250 | A9 |

**预计 9 个 agent PR + 3 个 webui PR**（A8 可按 provider 拆子 PR）。

---

## 12. 与 oauth-db-only 计划的状态对照

| 里程碑 | oauth-db-only 计划 | 本计划 |
|--------|-------------------|--------|
| 成员登录 provider 定义 | A0–A3 ✅ | — |
| 模型 token 双写 | A4 ✅ | A5–A6 完成读写真源 |
| 弃用 auth.json | 原列非目标 | **A5–A10 全文** |
| OAuth state → DB | 非目标 | 仍非目标 |

完成 A10 后，在 [oauth-db-only-migration-pr-plan.md](oauth-db-only-migration-pr-plan.md) §2.2 将「模型 OAuth 全面弃用 auth.json」标为 ✅ 并链接本文件。
