# intellect-agent Provider 数据库统一管理设计文档

**日期：** 2026-06-02  
**作者：** Code Review Follow-up  
**状态：** Draft (待评审)  
**适用范围：** `intellect-agent` + `intellect-webui` 推理大模型 Provider / Model 管理链路  
**关联仓库：** `intellect-agent`（运行时与 Registry）、`intellect-webui`（Settings UI 与 BFF API）

---

## 1. 背景与目标

当前 `intellect-agent` 的 Provider 管理能力具备较强扩展性基础（`ProviderProfile`、插件动态发现、运行时 resolver），但 Provider 选择、别名解析、鉴权映射、路由规则在多个模块中分散实现，新增 Provider 仍需跨文件修改，维护成本较高。

本设计目标是将 **推理 Provider 与模型相关的全部可配置状态** 以 **SQLite 为唯一权威数据源** 管理，同时保留代码层 hook 能力，形成「数据库驱动 + 代码扩展」的统一架构。

> **原则变更（相对初稿）：** 不再长期维持「Models 页 + Providers 页」双入口，也不再以 `config.yaml` 的 `model.*` / `providers.*` 与 DB 双写为主路径。升级后，**现有模型与 Provider 配置均须完成一次性/可回滚的迁移**；WebUI Settings 以 **「模型 / Models」** 为唯一管理入口（承接原 Providers 面板的模型相关能力）。

### 1.1 核心目标

1. 建立统一 Registry 数据模型：**Provider、Model 绑定、默认模型、Fallback 链、Auxiliary 任务模型** 均落库。
2. 将 alias/env/routing 等声明性规则从硬编码与 YAML 碎片字段迁入 DB。
3. 收口运行时决策入口：CLI、Gateway、WebUI、Cron 均经 `ProviderRegistryService` 读 DB。
4. 提供 **可验证、可回滚的配置迁移**（`config.yaml` → DB），迁移完成后 YAML 中对应字段降为只读镜像或废弃。

### 1.2 非目标

1. 不将复杂行为逻辑（如 `prepare_messages`、`build_extra_body`）完全 SQL 化。
2. 不在本阶段替换 TTS/STT 的独立 provider 抽象体系。
3. 不要求一次发布完成全量迁移（采用分阶段双轨策略）。

### 1.3 双端协同范围

本方案必须 **agent 与 webui 同步演进**；WebUI **不得** 在迁移完成后继续直接改写 `config.yaml` 的 `model` / `fallback_providers` / `auxiliary` / `providers` / `custom_providers` 作为主存储。

| 端 | 职责 |
|----|------|
| **intellect-agent** | DB schema、迁移器、Registry Service、运行时 resolver、CLI `intellect model` 改读/写 DB |
| **intellect-webui** | Settings → **「模型 / Models」** 为唯一配置入口；BFF 仅调用 Registry API（不直写 YAML 模型字段） |

原 Settings **Providers** 面板（`loadProvidersPanel()`）在迁移期保留为 **重定向壳**（1 个版本周期），最终移除或合并进「模型」页（见第 13 节）。

---

## 2. 现状分析摘要

### 2.1 现有优势

- `providers/base.py` 定义了统一 `ProviderProfile` 抽象。
- `providers/__init__.py` 支持内置插件 + 用户插件动态发现。
- `intellect_cli/runtime_provider.py` 已是运行时解析主入口。

### 2.2 主要问题

1. **决策逻辑分散**：`auth.py`、`runtime_provider.py`、`providers.py`、`model_metadata.py` 等多处并行维护规则。
2. **硬编码较多**：api_mode/base_url/provider alias 特判存在多处分支。
3. **可审计性不足**：Provider 生效规则、优先级与来源不易统一追踪。
4. **扩展改造面大**：新增 Provider 常需修改多个模块，回归成本高。

---

## 3. 目标架构

采用三层模型：

1. **Registry Data Layer (DB)**  
   存储 Provider 声明性元数据、alias、env 绑定、路由规则、启用状态等。

2. **Registry Service Layer (Python)**  
   统一实现 `resolve_provider` / `resolve_runtime_provider` 所需查询与决策逻辑。

3. **Runtime Hook Layer (Python Hook)**  
   保留 `ProviderProfile` 的行为 hook（复杂非声明式逻辑）并支持按 provider 绑定。

### 3.1 关键原则

- **声明性模型配置以 DB 为准**；行为性逻辑（hook）留代码。
- **密钥不落 registry 表**：API Key / OAuth token 仍在 `auth.json`、加密列或 env；DB 仅存 `credential_ref`（provider_id + auth 槽位）。
- 运行时单入口决策；`config.yaml` 在迁移后仅作 **导出/备份** 或 doctor 对照，不作写入主路径。
- 环境变量可作为 **启动覆盖**（与 today 类似），但需写入 `inference_env_overrides` 审计表或启动时合并进内存快照，避免静默分叉。

---

## 4. 数据库模型设计

以下表建议作为 `intellect_state.py` 新 migration（示例版本：v20+）。

### 4.1 `inference_providers`

存储 Provider 主定义。

建议字段：

- `id` TEXT PRIMARY KEY
- `name` TEXT NOT NULL
- `enabled` INTEGER NOT NULL DEFAULT 1
- `auth_type` TEXT NOT NULL DEFAULT 'api_key'
- `api_mode_default` TEXT NOT NULL DEFAULT 'chat_completions'
- `base_url_default` TEXT NOT NULL DEFAULT ''
- `models_url` TEXT NOT NULL DEFAULT ''
- `is_aggregator` INTEGER NOT NULL DEFAULT 0
- `source` TEXT NOT NULL DEFAULT 'builtin'  -- builtin|plugin|user
- `priority` INTEGER NOT NULL DEFAULT 100
- `metadata_json` TEXT NOT NULL DEFAULT '{}'
- `created_at` REAL NOT NULL
- `updated_at` REAL

### 4.2 `inference_provider_aliases`

存储 provider 别名映射。

- `alias` TEXT PRIMARY KEY
- `provider_id` TEXT NOT NULL REFERENCES inference_providers(id)
- `created_at` REAL NOT NULL

### 4.3 `inference_provider_env_bindings`

存储 env 映射规则。

- `id` TEXT PRIMARY KEY
- `provider_id` TEXT NOT NULL REFERENCES inference_providers(id)
- `kind` TEXT NOT NULL              -- api_key|base_url|other
- `env_var` TEXT NOT NULL
- `priority` INTEGER NOT NULL DEFAULT 100
- `required` INTEGER NOT NULL DEFAULT 0
- `created_at` REAL NOT NULL

### 4.4 `inference_provider_routing_rules`

存储路由规则（声明式匹配）。

- `id` TEXT PRIMARY KEY
- `provider_id` TEXT NOT NULL REFERENCES inference_providers(id)
- `match_type` TEXT NOT NULL        -- explicit_provider|host_suffix|model_regex|api_mode_hint
- `match_expr` TEXT NOT NULL
- `resolved_api_mode` TEXT NOT NULL DEFAULT ''
- `weight` INTEGER NOT NULL DEFAULT 100
- `enabled` INTEGER NOT NULL DEFAULT 1
- `created_at` REAL NOT NULL

### 4.5 `inference_provider_hooks`（可选）

将 provider 与代码 hook 绑定（行为逻辑仍在 Python）。

- `provider_id` TEXT PRIMARY KEY REFERENCES inference_providers(id)
- `hook_entrypoint` TEXT NOT NULL   -- 例如 providers.kimi_coding:KimiProfile
- `created_at` REAL NOT NULL

### 4.6 `inference_models`（模型目录与绑定）

用户可见的「模型」实体（可与 models.dev 目录对齐，也可为 custom model id）。

- `id` TEXT PRIMARY KEY              -- 如 `anthropic/claude-sonnet-4.6` 或 `local:qwen3`
- `provider_id` TEXT NOT NULL REFERENCES inference_providers(id)
- `display_name` TEXT NOT NULL DEFAULT ''
- `context_length` INTEGER
- `capabilities_json` TEXT NOT NULL DEFAULT '{}'  -- vision, tools, reasoning, ...
- `source` TEXT NOT NULL DEFAULT 'catalog'        -- catalog|user|session_import
- `enabled` INTEGER NOT NULL DEFAULT 1
- `metadata_json` TEXT NOT NULL DEFAULT '{}'
- `created_at` REAL NOT NULL
- `updated_at` REAL

### 4.7 `inference_runtime_profile`（单例/按 profile 一行）

替代 `config.yaml` → `model.*` 的全局默认运行时。

- `id` TEXT PRIMARY KEY DEFAULT 'default'
- `primary_provider_id` TEXT REFERENCES inference_providers(id)
- `primary_model_id` TEXT REFERENCES inference_models(id)
- `api_mode` TEXT
- `base_url_override` TEXT NOT NULL DEFAULT ''
- `updated_at` REAL NOT NULL

### 4.8 `inference_fallback_chain`

替代 `fallback_providers` / `fallback_model`。

- `position` INTEGER NOT NULL
- `provider_id` TEXT NOT NULL REFERENCES inference_providers(id)
- `model_id` TEXT REFERENCES inference_models(id)
- `enabled` INTEGER NOT NULL DEFAULT 1
- PRIMARY KEY (`position`)

### 4.9 `inference_auxiliary_bindings`

替代 `config.yaml` → `auxiliary.<task>.*`。

- `task_key` TEXT PRIMARY KEY          -- vision, compression, web_extract, ...
- `provider_id` TEXT REFERENCES inference_providers(id)
- `model_id` TEXT REFERENCES inference_models(id)
- `max_tokens` INTEGER
- `reasoning_effort` TEXT
- `metadata_json` TEXT NOT NULL DEFAULT '{}'
- `updated_at` REAL NOT NULL

### 4.10 `inference_migration_state`

记录迁移版本与 checksum，支持回滚与 doctor。

- `key` TEXT PRIMARY KEY               -- e.g. `yaml_to_db_v1`
- `source_checksum` TEXT NOT NULL      -- config.yaml 相关段落的 hash
- `migrated_at` REAL NOT NULL
- `rollback_snapshot_path` TEXT        -- 可选：迁移前 YAML 备份路径

---

## 5. 运行时统一决策流程

目标是将现有分散逻辑收敛到 `ProviderRegistryService.resolve_runtime(...)`：

1. 解析显式参数（CLI/调用方强制传入）。
2. 读取会话/配置上下文（`model.provider`、`model.base_url`、`model.api_mode`）。
3. 通过 alias 表解析 canonical provider。
4. 按 DB 规则计算候选 provider（enabled + priority + routing_rules）。
5. 组装 credentials（env_bindings + auth store + credential pool）。
6. 应用 hook（如 profile 的 request 预处理）。
7. 产出统一 RuntimeProviderResult：
   - `provider`
   - `api_mode`
   - `base_url`
   - `api_key` / token provider
   - `source`
   - `request_overrides`

---

## 6. 分阶段实施方案

## Phase 1：Schema + 迁移 + 镜像（W0–W1）

### 范围

- 新增第 4 节全部 `inference_*` 表与 `intellect_state` migration v20。
- 实现 `migrate_yaml_to_db()`（见第 14 节）与 `sync_from_code_registry()`。
- 运行时仍为 legacy；迁移结果仅用于 doctor diff 与 WebUI「模型」页只读展示。

### 交付

1. migration + seed + 自动备份 `config.yaml.bak.*`
2. `intellect model migrate [--dry-run|--rollback]`
3. doctor：`--model-registry`（YAML vs DB vs effective runtime）

## Phase 2：DB 真源 + WebUI「模型」页（W1–W2）

### 范围

- `ProviderRegistryService`；resolver **DB 优先**（hybrid 回退 legacy，仅迁移失败时）。
- WebUI：Settings **Providers → 模型**；全部写操作走 `/api/models/config*`。
- 废弃 WebUI/CLI 对 `config.yaml` model 段的直接写入。

### 交付

1. 开关：`INTELLECT_PROVIDER_REGISTRY_MODE=legacy|hybrid|db`（W2 默认 `db`）
2. WebUI 全量编辑 + 迁移横幅
3. 迁移验收 DoD（第 14.6）在 CI 通过

## Phase 3：收敛（W3）

### 范围

- 运行时 **仅读 DB**；`config.yaml` 中 `model` / `fallback_providers` / `auxiliary` / `providers` / `custom_providers` 用户键忽略并 doctor 提示。
- 硬编码 alias/routing 迁入 DB；精简 `runtime_provider` 特判。
- `intellect model export-yaml`；可选 `config migrate --strip-model-yaml`。

### 交付

1. 精简 resolver
2. 移除 WebUI deprecated 端点
3. 《模型配置 DB 管理》用户文档

---

## 7. 兼容策略（迁移期 → 收敛期）

| 阶段 | `config.yaml` 模型字段 | 运行时读取 | WebUI 写入 |
|------|------------------------|------------|------------|
| **迁移期 M1** | 首次启动 import → DB；保留 YAML 只读对照 | hybrid：DB 优先，YAML 回退 | Models 页写 DB；可选镜像写 YAML（deprecated） |
| **收敛期 M2** | doctor 警告若仍存在 `model.*` 等活跃写入 | **仅 DB** | **仅 DB**（经 Registry API） |
| **稳定期 M3** | 导出命令生成 YAML 快照；不再自动 import | 仅 DB | 仅 DB |

1. **插件兼容**：插件 `register_provider()` 后由 `sync_from_code_registry()` 写入/更新 `inference_providers`（builtin/plugin 行）。
2. **环境变量**：仅作启动时 overlay，并记录到迁移报告；禁止与 DB 主配置长期双真源。
3. **回滚**：`inference_migration_state` + 迁移前 `config.yaml.bak.<timestamp>`；`INTELLECT_PROVIDER_REGISTRY_MODE=legacy` 仅用于紧急热修，非长期模式。

---

## 8. 风险与缓解

1. **行为漂移风险**（DB 规则与旧逻辑不一致）  
   - 缓解：hybrid 阶段输出 `legacy_result vs db_result` diff 日志。

2. **复杂 provider 无法声明式表达**  
   - 缓解：hook 保留在 Python；DB 仅管理声明性信息。

3. **查询性能开销**  
   - 缓解：启动预加载 + 内存缓存 + `updated_at` 版本戳热刷新。

4. **迁移复杂度**  
   - 缓解：先镜像后切流，避免一次性替换。

---

## 9. 测试与验收标准

### 9.1 测试计划

1. 单元测试
   - alias 解析
   - env binding 优先级
   - routing rule 命中
   - hook 绑定与执行

2. 集成测试
   - `resolve_runtime_provider` 在 `legacy/hybrid/db` 三模式结果一致性（允许可解释差异）
   - 常见 provider（openrouter/anthropic/bedrock/custom/azure-foundry）回归

3. 兼容性测试
   - 旧 `config.yaml` 不改动可继续运行
   - 现有插件 provider 正常发现并可调用

### 9.2 验收标准

1. 新增 provider 在标准路径下不超过两处改动（插件 + DB seed/配置）。
2. hybrid 模式下主链路结果一致率 >= 99%（可解释差异除外）。
3. 无 P0/P1 回归（鉴权失败率、请求错误率、路由错误率）。

---

## 10. 实施任务清单（建议）

1. 设计评审通过（架构 + DB schema + 迁移策略）。
2. 实现 migration 与 seed。
3. 实现 `ProviderRegistryService` 与缓存。
4. 接入 hybrid 双轨并打点日志。
5. 完成回归测试与灰度发布。
6. 切换默认模式至 `hybrid`，稳定后推进 `db`。

---

## 11. 附录：建议接口草案

```python
class ProviderRegistryService:
    def resolve_provider(
        self,
        requested: str | None,
        *,
        explicit_api_key: str | None = None,
        explicit_base_url: str | None = None,
    ) -> str:
        ...

    def resolve_runtime(
        self,
        *,
        requested: str | None = None,
        explicit_api_key: str | None = None,
        explicit_base_url: str | None = None,
        target_model: str | None = None,
    ) -> dict:
        ...

    def sync_from_code_registry(self) -> None:
        ...
```

---

## 12. 预期收益

1. Provider 扩展成本下降，新增接入路径可标准化。
2. 决策逻辑可观测、可审计、可配置，维护复杂度降低。
3. 为后续控制台化管理 Provider（UI/CLI）提供稳定数据基础。

---

## 13. intellect-webui：Settings「模型 / Models」统一入口（替代 Providers）

### 13.1 UI 策略（修订）

Settings 侧栏将 **`providers` 分区替换为 `models`**（不是并列新增）：

- 英文菜单：**Models**
- 中文菜单：**模型**
- 原 `providers` 在 `switchSettingsSection` / `settingsPaneProviders` / `loadProvidersPanel()` 的逻辑 **迁入** `loadModelsPanel()`；旧分区保留 1 个版本为 **重定向**（打开后自动切到「模型」并 toast 提示）。

Preferences 中与默认模型、auxiliary 相关的控件 **删除**，避免与 DB 真源冲突。

### 13.2 「模型」页信息架构（单页统一管理）

| 区块 | 数据表 / 服务 | 用户操作 |
|------|----------------|----------|
| **连接与凭证** | `auth.json` + `inference_providers` | 配置 API Key、OAuth 登录/登出、配额刷新（原 Providers 卡片） |
| **Provider 列表** | `inference_providers` + aliases + routing | 启用/禁用、优先级、自定义 endpoint（user source） |
| **默认主模型** | `inference_runtime_profile` + `inference_models` | 选择 provider + model + api_mode |
| **Fallback 链** | `inference_fallback_chain` | 排序、启用、增删 |
| **辅助任务模型** | `inference_auxiliary_bindings` | 各 task_key 的 provider/model |
| **目录与探测** | `inference_models` + live catalog | 刷新模型列表、导入 custom model id |
| **高级** | `inference_provider_hooks` + sync | 从代码 re-seed builtin/plugin；routing 预览 |

所有 **保存** 操作调用 Registry API 写 DB；**不再** 调用 `POST /api/default-model` 写 `config.yaml`（该端点在 M2 改为写 DB 的薄封装，见 13.4）。

### 13.3 WebUI 前端改动清单

| 文件/模块 | 改动 |
|-----------|------|
| `static/index.html` | 侧栏 `providers` → `models`；`settingsPaneProviders` → `settingsPaneModels` |
| `static/panels.js` | `loadProvidersPanel` → `loadModelsPanel`（合并凭证卡片 + 模型配置） |
| `static/i18n.js` | `settings_dropdown_models` / `models_tab_*`；废弃 `providers_tab_*` 或作别名 |
| `api/routes.py` | 废弃直写 YAML 的 model 路径；路由到 `api/model_registry.py`（新模块） |
| `api/providers.py` | 凭证 CRUD 保留，声明性 provider 字段改调 RegistryService |
| `tests/*` | 更新 `test_sidebar_tab_visibility`、`test_model_default_boot_precedence`、`test_auxiliary_models_settings` |

### 13.4 WebUI BFF API（DB 为真源）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models/config` | 聚合：runtime_profile + fallback + auxiliary + providers 摘要 |
| PUT | `/api/models/config/primary` | 写 `inference_runtime_profile` |
| PUT | `/api/models/config/fallback` | 写 `inference_fallback_chain` |
| PUT | `/api/models/config/auxiliary/{task}` | 写 `inference_auxiliary_bindings` |
| GET/PUT | `/api/models/providers` | Provider CRUD（声明性字段） |
| POST | `/api/models/providers/{id}/credentials` | 凭证（代理现有 `set_provider_key` / OAuth） |
| GET | `/api/models/catalog` | 模型目录（DB + live merge） |
| POST | `/api/models/catalog/refresh` | 拉取远端 `/models` 更新 `inference_models` |
| POST | `/api/models/migrate` | 手动触发 YAML→DB 迁移（管理员） |
| GET | `/api/models/migrate/status` | 迁移状态 / checksum / 待处理冲突 |

**兼容端点（M1–M2 过渡）：**

| 旧端点 | 新行为 |
|--------|--------|
| `POST /api/default-model` | 内部转 `PUT /api/models/config/primary`；M2 起响应头 `Deprecation: true` |
| `POST /api/model/set` | 内部转 auxiliary PUT；同上 |
| `GET/POST /api/providers` | 声明性字段走 Registry；凭证行为不变 |

### 13.5 Agent 侧暴露能力

- `ProviderRegistryService` + `ModelRegistryService`（可合并为同一模块）为 **唯一写入口**。
- WebUI **禁止** `api/config._save_yaml_config_file` 写入 `model` / `fallback_providers` / `auxiliary` / `providers` / `custom_providers` 段（M2 硬拒绝 + doctor 报错）。
- CLI：`intellect model` 与 `intellect provider` 子命令同步改读 DB。

### 13.6 双仓发布顺序（含迁移门控）

| 步骤 | intellect-agent | intellect-webui |
|------|-----------------|-----------------|
| W0 | 实现 migration v20 + `migrate_yaml_to_db()` + 回滚备份 | — |
| W1 | 启动时自动迁移；resolver hybrid | Settings 上线「模型」页（只读展示 + 「执行迁移」） |
| W2 | resolver 默认 db；CLI/WebUI 写 DB | 「模型」页全量编辑；废弃 Providers 菜单 |
| W3 | 移除 YAML 写入；export 子命令 | 移除 `/api/default-model` 对 YAML 的写入 |

**门控条件（W2 前必须满足）：** `intellect doctor --model-registry` 报告无 error；抽样 50 条历史会话 `sessions.model` 与 `inference_runtime_profile` 一致或可解释。

---

## 14. 配置迁移方案（YAML / 遗留状态 → SQLite）

### 14.1 迁移范围（必须入库的配置）

| 来源 | 路径/位置 | 目标表 |
|------|-----------|--------|
| 全局默认 | `config.yaml` → `model.default` / `provider` / `base_url` / `api_mode` / `openai_runtime` | `inference_runtime_profile` + `inference_models` |
| Fallback | `fallback_providers`、legacy `fallback_model` | `inference_fallback_chain` |
| 辅助模型 | `auxiliary.<task>.provider` / `model` / … | `inference_auxiliary_bindings` |
| 自定义 Provider | `providers:` dict、`custom_providers:` list | `inference_providers`（source=user）+ `inference_provider_env_bindings` |
| 内置/插件 | 代码 `ProviderProfile` + `PROVIDER_REGISTRY` | `inference_providers`（source=builtin/plugin）经 `sync_from_code_registry` |
| 别名 | `auth.resolve_provider` 硬编码 + profile.aliases | `inference_provider_aliases` |
| 路由规则 | `runtime_provider` 特判（可声明化部分） | `inference_provider_routing_rules` |
| 活跃 Provider | `auth.json` → `active_provider` | `inference_runtime_profile` 或 `inference_providers.metadata_json` |
| 会话覆盖（可选） | `sessions.model`、`sessions.model_config` | **不批量改写**；仅在新会话默认时读 profile；历史会话保持 JSON 快照 |
| WebUI 遗留 | `settings.json` 中已废弃的 `default_model` | 若存在且 DB 为空，导入一次后删除该键 |

**明确不迁入 registry 表的内容：**

- API Key、OAuth refresh token、client_secret（仍在 `auth.json` / `oauth_tokens` / `.env`）
- TTS/STT provider（独立 registry）
- `members` / `oauth_providers`（login 用途，与 inference 分离）

### 14.2 迁移执行时机

1. **升级后首次启动**（`intellect_state` migration v20）：若 `inference_migration_state` 无 `yaml_to_db_v1`，则运行 `migrate_yaml_to_db()`。
2. **显式命令**：`intellect model migrate [--dry-run] [--force]`（供运维与 WebUI「执行迁移」按钮）。
3. **Profile 切换**：每个 `INTELLECT_HOME` 独立执行（与 `state.db` 绑定）。

### 14.3 迁移步骤（算法）

```
1. 备份 config.yaml → config.yaml.bak.<timestamp>（同目录，chmod 600）
2. 计算 source_checksum = hash(model + fallback + auxiliary + providers + custom_providers)
3. 若 checksum 与 inference_migration_state 相同且 status=ok → SKIP
4. seed builtin/plugin：sync_from_code_registry()
5. import user providers：providers dict + custom_providers list → inference_providers (merge by slug)
6. import runtime profile：model.* → inference_runtime_profile
7. import fallback chain：fallback_providers[] → inference_fallback_chain (ordered)
8. import auxiliary：auxiliary.* → inference_auxiliary_bindings
9. import aliases：merge code aliases + YAML 无（仅代码）
10. 校验：resolve_runtime_provider() legacy vs db → diff 报告
11. 写入 inference_migration_state；config.yaml 对应段添加 _migrated_to_db: true 注释块（可选机器可读键）
```

### 14.4 冲突与合并策略

| 冲突 | 策略 |
|------|------|
| DB 已有 user provider，YAML 同名不同 `base_url` | **以 DB 为准**；YAML 差异写入 `migration_warnings.log` |
| YAML 有值、DB 为空 | 采用 YAML |
| `custom_providers` 与 `providers:` 重复 | 按 `get_compatible_custom_providers()` 合并规则去重后入库 |
| `model.default` 带 `provider:` 前缀 | 拆分为 `primary_model_id` + `primary_provider_id` |
| legacy `fallback_model` 字符串 | 解析为单条 fallback 或映射到 openrouter 模型 id |
| 插件新增 builtin provider | `sync` 不覆盖 user 行的 `base_url` / `enabled` |

### 14.5 回滚

1. 停止 gateway / webui。
2. `intellect model migrate --rollback`：从 `config.yaml.bak.*` 恢复 YAML；`DELETE FROM inference_*`（或整库恢复 migration 前 `state.db` 备份）。
3. 设置 `INTELLECT_PROVIDER_REGISTRY_MODE=legacy`。
4. Doctor 确认 effective runtime 与备份一致。

### 14.6 迁移验收（DoD）

- [ ] 空配置新装：builtin seed + 默认 openrouter/auto 可启动
- [ ] 典型单用户 YAML（含 custom_providers + auxiliary）：迁移后 `intellect doctor` 0 error
- [ ] WebUI「模型」页展示与迁移前 `POST /api/default-model` 行为一致
- [ ] CLI `intellect model` 列表与 DB `inference_models` 一致
- [ ] 回滚脚本在 CI 中跑通（fixture config）

### 14.7 对 `config.yaml` 的后续处理（M3）

- `DEFAULT_CONFIG` 保留 `model` / `auxiliary` 等键作为 **文档默认值**，但运行时 **忽略** 用户文件中这些键（doctor warning）。
- 提供 `intellect model export-yaml` 生成只读快照供 Git 备份。
- `_config_version` bump + 可选自动 strip 已迁移键（需用户确认，`intellect config migrate --strip-model-yaml`）。

---

## 15. 单用户场景影响分析

默认安装下 `members.enabled: false`（见 `intellect_cli/config.py` DEFAULT_CONFIG），系统处于 **legacy 单用户模式**。本 Registry 方案对单用户 **总体有利**，但存在以下需显式评估的影响因素。

### 15.1 影响结论（摘要）

| 维度 | 单用户影响 | 严重度 |
|------|------------|--------|
| 配置入口变化 | Settings **Providers → 模型**，Preferences 内模型项移除 | 中（需重定向与文档） |
| 数据存储位置 | **模型配置权威迁至 `state.db`**；`config.yaml` 模型段只读/废弃 | **高（必须跑迁移）** |
| 首次升级 | 自动 `migrate_yaml_to_db`；失败则保持 legacy 并阻断 W2 | **高** |
| 运行时行为 | hybrid 阶段 resolver 结果可能与 legacy 微差 | 中（迁移前后 diff） |
| 性能 | 启动多一次 registry 加载/缓存 | 低 |
| 多成员/RBAC | **无影响**（未开启 members 时不走成员鉴权） | 无 |
| Profile 隔离 | 每个 `INTELLECT_HOME` 独立 DB + config | 低（与现有一致） |
| CLI/TUI/Gateway 一致性 | 统一 resolver 后 **改善** 分裂问题 | 正向 |
| 升级/回滚 | 需同步 agent+webui 版本与 migration | 中 |

### 15.2 影响因素清单（单用户）

#### A. 配置与 UI

1. **一次性迁移失败或部分迁移**  
   - 因素：复杂 `custom_providers`、手写 YAML、多 profile。  
   - 表现：升级后无法对话、默认模型为空、fallback 丢失。  
   - 缓解：迁移前自动备份；失败时 doctor 明确错误 + `migrate --dry-run`；W2 门控。

2. **Settings 入口合并（Providers → 模型）**  
   - 因素：书签/文档仍指向 Providers。  
   - 表现：找不到配置页。  
   - 缓解：重定向 + 发布说明 + Onboarding 更新。

3. **手工编辑 `config.yaml` 失效**  
   - 因素：M2 起运行时忽略 `model.*` 等键。  
   - 表现：用户改 YAML 不生效，误以为 bug。  
   - 缓解：doctor warning；文档改为「请用 WebUI 模型页或 `intellect model`」；`export-yaml` 仅备份。

#### B. 运行时与可靠性

4. **Resolver 行为漂移（迁移前后 / hybrid）**  
   - 因素：`resolve_runtime_provider()` 改为 DB 优先后，api_mode/base_url 推断可能与迁移前 YAML 推断不同。  
   - 单用户表现：个别 custom/openrouter/anthropic 组合出现 404 或错误 api_mode。  
   - 缓解：迁移步骤 10 强制 legacy vs db diff；未通过则不得进入 W2；doctor 提供 preview。

5. **自定义 Provider（`custom_providers` / `providers:` dict）**  
   - 因素：用户 YAML 中的命名 provider 需同步进 registry 表。  
   - 单用户表现：升级后自定义 endpoint 短暂不可见或 priority 错误。  
   - 缓解：首次启动 migration 自动 import；`intellect provider registry sync`。

6. **Credential Pool 与 OAuth**  
   - 因素：凭证仍不在 inference registry 表内，resolver 需联查 auth store。  
   - 单用户表现：OAuth 过期后 Models 页仍显示「已选模型」但请求失败。  
   - 缓解：Models 页展示「运行时可用性」徽章（复用 `/api/provider/quota` 与 auth status）。

7. **会话级 model override**  
   - 因素：WebUI 会话可覆盖 `model`（`/api/chat/start` 等），与全局 default 并存。  
   - 单用户表现：改全局默认不影响已打开会话（符合预期，但需文案说明）。  
   - 缓解：沿用现有 `model_scope_advisory` 文案。

#### C. 数据与运维

8. **SQLite schema 扩展**  
   - 因素：`state.db` 新增表；备份/恢复需包含新表。  
   - 单用户表现：仅复制 `config.yaml` 迁移机器时 registry 规则丢失。  
   - 缓解：升级说明必须备份 `state.db`；`intellect model export-yaml` 导出快照；禁止只拷贝 `config.yaml` 迁机。

9. **Profile 多实例**  
   - 因素：每个 profile 独立 `INTELLECT_HOME`。  
   - 单用户表现：切换 profile 后 Models 页列表不同（预期行为）。  
   - 缓解：Settings 页显示当前 profile 名（若已有则强化）。

10. **WebUI 与 Gateway/CLI 并行**  
    - 因素：Gateway 可能未热加载 registry 缓存。  
    - 单用户表现：WebUI 改 routing 后，已运行 gateway 仍用旧规则直到重启。  
    - 缓解：保存后提示「重启 gateway 生效」或实现 registry 版本戳 + 热刷新（Phase 3）。

#### D. 安全与隐私（单用户仍 relevant）

11. **API Key 展示面**  
    - 因素：Models 页不应重复存储 secret；只显示 masked 状态。  
    - 单用户表现：误将 key 写入 registry `metadata_json`。  
    - 缓解：schema 校验禁止 `api_key` 字段；仅存 env 名或 auth 引用。

12. **本地自定义 endpoint（LAN/Ollama）**  
    - 因素：routing 规则错误可能把 LAN URL 误判为 openrouter 上下文。  
    - 单用户表现：请求发到错误 host 或携带错误 key。  
    - 缓解：保留 `url_safety` 与 host-gated key 逻辑在 service 层单测覆盖。

### 15.3 单用户推荐发布策略（修订）

1. **W0/W1**：升级即 **自动迁移**；未迁移成功则保持 `legacy` resolver，WebUI 显示阻塞横幅「请完成模型配置迁移」。  
2. **W1**：「模型」页可编辑，但写入 **仅 DB**；YAML 镜像写入关闭。  
3. **W2 门控**：doctor 通过后才默认 `INTELLECT_PROVIDER_REGISTRY_MODE=db`。  
4. **备份清单**：升级说明要求备份 `config.yaml` + `state.db` + `auth.json`。

### 15.4 多用户模式（对比说明，非单用户阻塞）

当 `members.enabled: true` 时，还需额外考虑（**不在单用户默认路径**，但设计需预留）：

- 按 member/team/project 的 model 覆盖（`sessions.model_config`、runtime context）  
- WebUI Models 页是否仅 admin 可改全局 registry（RBAC）  
- 与 `oauth_providers`（login）和 `inference_providers`（推理）命名区分，避免运维混淆  

单用户模式下上述 RBAC **不启用**，实现时可硬编码 `actor_role=owner`。

---

## 16. 修订后的实施任务清单（双仓）

### intellect-agent

1. Schema v20：`inference_*` 全表 + `inference_migration_state`  
2. `migrate_yaml_to_db()` + rollback + `intellect model migrate`  
3. `ProviderRegistryService` / `ModelRegistryService` + resolver 读 DB  
4. `intellect doctor --model-registry` + legacy/db diff  
5. 废弃运行时对 `config.yaml` model 段的写入；`export-yaml`  

### intellect-webui

6. Settings：**Providers → 模型**（合并 `loadModelsPanel`）  
7. 新 API：`/api/models/config*`、`/api/models/providers*`、`/api/models/migrate*`  
8. 废弃 `POST /api/default-model` 写 YAML（改为 DB 封装）  
9. Onboarding / i18n / 重定向 / 迁移横幅  
10. 测试：迁移 fixture、`test_model_default_boot_precedence`、auxiliary、sidebar  

### 联调

11. W0–W2 门控与配对版本说明  
12. 用户文档：《升级指南 — 模型配置迁入数据库》

---

## 17. 关联文档

| 文档 | 路径 | 用途 |
|------|------|------|
| 迁移 Runbook | `docs/plans/2026-06-02-model-registry-migration-runbook.md` | 升级/回滚/排障检查清单 |
| Agent 实施模板 | `docs/plans/2026-06-02-model-registry-agent-implementation.md` | intellect-agent 里程碑与验收 |
| WebUI 实施模板 | `intellect-webui/docs/plans/2026-06-02-model-registry-webui-implementation.md` | intellect-webui 里程碑与验收 |

