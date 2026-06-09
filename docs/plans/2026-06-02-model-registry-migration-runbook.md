# 模型配置迁入数据库 — 迁移 Runbook

**日期：** 2026-06-02  
**关联设计：** `docs/plans/2026-06-02-provider-registry-db-unification-design.md`  
**受众：** 运维、发布负责人、支持单用户升级问题的开发者

---

## 1. 升级前检查清单

在升级 intellect-agent / intellect-webui **之前** 完成：

- [ ] 确认 `INTELLECT_HOME` 路径（含 profile：`~/.intellect/profiles/<name>`）
- [ ] 备份以下文件（建议打包为 `intellect-backup-<date>.tar.gz`）：
  - [ ] `{INTELLECT_HOME}/config.yaml`
  - [ ] `{INTELLECT_HOME}/state.db`
  - [ ] `{INTELLECT_HOME}/auth.json`
  - [ ] `{INTELLECT_HOME}/.env`（若存在）
- [ ] 记录当前有效配置（便于对比）：
  ```bash
  intellect model show          # 或等价命令（迁移实现后）
  grep -E '^(model|fallback|auxiliary|providers|custom_providers):' -A2 \
    ~/.intellect/config.yaml
  ```
- [ ] 停止正在运行的 **gateway** 与 **webui**（避免迁移中并发写）
- [ ] 确认磁盘空间 ≥ 2× `state.db` 大小（迁移会写新表 + 备份）

---

## 2. 标准升级流程（单用户 / 单 Profile）

### 2.1 安装新版本

1. 升级 intellect-agent 与 intellect-webui 到 **配对版本**（见发布说明中的最低兼容版本）。
2. 激活 venv：`source .venv/bin/activate`（或项目约定路径）。

### 2.2 自动迁移（首次启动）

1. 执行任意会打开 `state.db` 的命令，例如：
   ```bash
   intellect doctor
   ```
2. 检查迁移状态：
   ```bash
   intellect model migrate --status
   intellect doctor --model-registry
   ```
3. 预期：
   - `inference_migration_state` 含 `yaml_to_db_v1`，`status=ok`
   - 同目录存在 `config.yaml.bak.<timestamp>`
   - doctor **0 error**（warning 可接受，需阅读说明）

### 2.3 启动 WebUI 并验证

1. 启动 webui，打开 **Settings → 模型**（原 Providers 应重定向至此）。
2. 确认页面显示：
   - 默认主模型与升级前一致
   - Fallback 链条目数一致
   - Auxiliary 各任务绑定一致
   - Provider 凭证状态为「已配置」或「需登录」
3. 发起一次测试对话，确认能正常收到模型回复。

### 2.4 启动 Gateway（若使用）

```bash
# 按项目惯例启动 gateway
intellect gateway …
```

保存模型配置后若 routing 未生效，**重启 gateway**（registry 缓存，见设计文档 Phase 3）。

---

## 3. 手动迁移与演练

### 3.1 预演（不写入）

```bash
intellect model migrate --dry-run
```

检查输出中的：

- 将导入的 provider 数量
- `primary_model` / fallback / auxiliary 映射
- **warnings**（同名不同 base_url 等）

### 3.2 强制重新迁移（慎用）

仅在 doctor 建议或支持指导下使用：

```bash
intellect model migrate --force
```

`--force` 会按设计文档冲突策略：**DB 已有 user 行时以 DB 为准**，YAML 差异记入 `migration_warnings.log`。

---

## 4. 回滚流程

**触发条件：** 迁移后无法对话、doctor 报 error、或 resolver diff 未通过。

1. 停止 gateway / webui / 所有 agent 进程。
2. 回滚软件版本到升级前 tag（agent + webui 同步回滚）。
3. 恢复文件：
   ```bash
   cp ~/.intellect/config.yaml.bak.<timestamp> ~/.intellect/config.yaml
   # 若已替换 state.db，从备份 tar 中恢复整个 state.db
   cp ~/intellect-backup-*/state.db ~/.intellect/state.db
   ```
4. 或在新版本下执行（若已实现）：
   ```bash
   intellect model migrate --rollback
   export INTELLECT_PROVIDER_REGISTRY_MODE=legacy
   ```
5. 验证：
   ```bash
   intellect doctor
   # 测试 CLI 对话或 webui 发一条消息
   ```

---

## 5. 常见失败场景与处理

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| doctor：`migration incomplete` | 首次启动迁移中断 | 停进程后 `intellect model migrate`；检查 `state.db` 可写 |
| 默认模型为空 | `model.default` 未导入或 model id 不在 catalog | `migrate --dry-run` 查看映射；在「模型」页手动选默认模型 |
| custom provider 404 | `base_url` / `api_mode` 迁移错误 | 对比 `config.yaml.bak` 与 DB `inference_providers`；修正后保存 |
| WebUI 改模型不生效 | 仍写 YAML 或 gateway 未重启 | 确认 webui 版本 ≥ W2；重启 gateway；`doctor --model-registry` |
| CLI 与 WebUI 显示不一致 | hybrid 模式或迁移未完成的 profile | 对当前 `INTELLECT_HOME` 再跑 migrate；检查是否用错 profile |
| OAuth 可用但推理失败 | 凭证未过期但 routing 错 | 「模型」页查看运行时预览；检查 `inference_provider_routing_rules` |
| 仅复制 config 到新机器无效 | 未复制 `state.db` | 新机器需 **config + state.db + auth.json** 或在该机重新 migrate |
| `fallback` 丢失 | legacy `fallback_model` 字符串未解析 | 在「模型」页重建 fallback 链；参考 bak YAML |

---

## 6. 多 Profile 说明

每个 profile 有独立 `INTELLECT_HOME` 与 `state.db`：

```bash
intellect -p <profile> doctor --model-registry
intellect -p <profile> model migrate --status
```

**不要** 假设迁移一个 profile 会影响其他 profile。

---

## 7. 发布门控（维护者）

W2 对外发布前，CI / 发布 checklist：

- [ ] `scripts/run_tests.sh tests/.../test_model_registry_migrate*.py` 通过
- [ ] fixture：含 `custom_providers` + `auxiliary` + `fallback_providers` 的 config 迁移后 diff 通过
- [ ] webui：`test_model_default_boot_precedence` 通过
- [ ] 文档：`CHANGELOG` + 用户升级指南链接本 Runbook
- [ ] 配对版本号写入 agent / webui release notes

---

## 8. 支持收集信息模板

用户报障时请收集：

1. intellect-agent / webui 版本号  
2. `INTELLECT_HOME` 路径、是否 profile  
3. `intellect model migrate --status` 输出（可打码）  
4. `intellect doctor --model-registry` 输出  
5. `config.yaml.bak.*` 是否存在（无需提供内容，仅确认）  
6. 是否仅升级 agent 未升级 webui（或反之）
