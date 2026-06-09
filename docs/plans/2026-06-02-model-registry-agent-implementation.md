# [Agent] 模型/Provider Registry 数据库化 — 实施 Issue 模板

**日期：** 2026-06-02  
**设计：** `2026-06-02-provider-registry-db-unification-design.md`  
**Runbook：** `2026-06-02-model-registry-migration-runbook.md`  
**仓库：** intellect-agent

---

## 目标

将推理 Provider 与模型配置迁入 `state.db`，提供迁移器与 `ProviderRegistryService`，运行时以 DB 为真源；为 WebUI「模型」页提供 HTTP/CLI API。

---

## 里程碑

### M1 — Schema + 迁移（W0）

- [ ] `intellect_state.py`：migration v20，表 `inference_providers`、`inference_provider_aliases`、`inference_provider_env_bindings`、`inference_provider_routing_rules`、`inference_provider_hooks`、`inference_models`、`inference_runtime_profile`、`inference_fallback_chain`、`inference_auxiliary_bindings`、`inference_migration_state`
- [ ] `agent/model_registry.py`（或 `agent/provider_registry.py`）：
  - [ ] `migrate_yaml_to_db(home, *, dry_run=False, force=False) -> MigrationResult`
  - [ ] `rollback_migration(home) -> None`
  - [ ] `sync_from_code_registry() -> None`
- [ ] 迁移逻辑覆盖：`model.*`、`fallback_providers` / `fallback_model`、`auxiliary.*`、`providers`、`custom_providers`、`auth.json` active_provider
- [ ] 自动备份 `config.yaml.bak.<timestamp>`
- [ ] CLI：`intellect model migrate [--dry-run|--force|--rollback|--status]`
- [ ] 测试：`tests/agent/test_model_registry_migrate.py`（fixture YAML → DB → assert 行 + resolver diff）

### M2 — Registry Service + Resolver（W1）

- [ ] `ProviderRegistryService`：`list_providers`、`get_runtime_profile`、`set_primary`、`set_fallback_chain`、`set_auxiliary`、`update_provider`
- [ ] `resolve_runtime_provider()` / `resolve_provider()`：hybrid（DB 优先，legacy 回退）；env `INTELLECT_PROVIDER_REGISTRY_MODE`
- [ ] `intellect_cli/runtime_provider.py`：收敛特判到 DB routing 或保留 hook
- [ ] `intellect doctor`：`--model-registry`（YAML vs DB vs effective runtime）
- [ ] 启动钩子：若未迁移则自动 `migrate_yaml_to_db`（失败则 log + legacy）

### M3 — 写路径收敛 + CLI（W2）

- [ ] 禁止 `save_config_value` / loader 写入 `model`、`fallback_providers`、`auxiliary`、`providers`、`custom_providers`（doctor warning）
- [ ] `intellect model` 子命令改读/写 DB（与 today `intellect model` UX 对齐）
- [ ] `intellect model export-yaml`（只读快照）
- [ ] Gateway / `api_server`（若需）：暴露 `/v1/models/config` 或供 webui import 的 stable API
- [ ] 默认 `INTELLECT_PROVIDER_REGISTRY_MODE=db`（W2 门控后）

### M4 — 清理（W3）

- [ ] 移除 hybrid 默认；精简 `runtime_provider` 硬编码
- [ ] 可选：`intellect config migrate --strip-model-yaml`
- [ ] 文档：`AGENTS.md` 一节「模型配置在 state.db」

---

## 验收标准

1. 典型单用户 `config.yaml` fixture 迁移后，`intellect doctor --model-registry` 0 error。  
2. `migrate --dry-run` 与 `migrate` 结果一致（除 checksum 写入）。  
3. `rollback` 恢复后 legacy resolver 与迁移前行为一致。  
4. 新装无 YAML 模型段时，seed + 默认 profile 可启动。  
5. 密钥不出现在 `inference_*` 表（仅 credential_ref / env 名）。

---

## 非目标（本 Issue 不做）

- WebUI Settings UI（见 webui issue）
- TTS/STT registry 合并
- `members.enabled=true` 的 per-member 模型策略（仅预留 schema）

---

## 依赖

- WebUI W1 依赖 M1 + M2 只读/写 API 草案  
- WebUI W2 依赖 M3 写路径收敛

---

## 参考文件

| 模块 | 路径 |
|------|------|
| Provider 抽象 | `providers/base.py`, `providers/__init__.py` |
| 运行时解析 | `intellect_cli/runtime_provider.py` |
| Auth registry | `intellect_cli/auth.py` |
| 配置默认 | `intellect_cli/config.py` |
| 状态库 | `intellect_state.py` |
| 会话 model 列 | `sessions.model`, `sessions.model_config` |
