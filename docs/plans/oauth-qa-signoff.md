# OAuth §9 QA 签字工单

**日期：** 2026-06-06  
**分支：** `v0.4.2`  
**范围：** `auth-json-deprecation` §9 + `oauth-db-only` §9

---

## 自动化覆盖（CI / 本地 pytest）

| # | 场景 | 自动化 | 测试 / 备注 |
|---|------|:------:|-------------|
| 7 | `migrate-from-auth-json` + `doctor` 无漂移 | ✅ | `tests/intellect_cli/test_doctor_oauth_drift.py` |
| 8 | `intellect logout <provider>` 清空 DB | ✅ | `tests/agent/test_oauth_model_tokens.py` |
| 0 | setup 种子 wecom/dingtalk/feishu | ✅ | `tests/intellect_cli/test_setup_oauth_seed.py` |
| 1a | 飞书 `/login` status + authorize | ✅ | `intellect-webui/tests/test_login_feishu_oauth_e2e.py` |
| 1b | 企微 `/login` status + authorize | ✅ | `intellect-webui/tests/test_login_enterprise_oauth_e2e.py` |
| 1c | 钉钉 `/login` status + authorize | ✅ | 同上 |

**企业 OAuth 约定：** Provider 定义与凭证存 `oauth_providers`（DB）；`config.yaml` 仅 `members.oauth.enabled` 总开关。Settings 填 App ID/Secret（及企微 corp_id/agent_id）后 `/login` 出现图标。

---

## 人工联调（需 QA 签字）

| # | 场景 | 签字人 | 日期 | 结果 |
|---|------|--------|------|------|
| 1 | 新装无 `auth.json`，`intellect auth add openai-codex` → DB 有 token + pool | | | ☐ |
| 2 | 重命名 `auth.json` 后 Codex 推理仍成功 | | | ☐ |
| 3 | Pool 双条目 429/DEAD 轮换 | | | ☐ |
| 4 | Profile OAuth 与 global shadow | | | ☐ |
| 5 | `intellect auth list` 与 WebUI Providers 一致 | | | ☐ |
| 6 | WebUI Codex 设备码后 `auth.json` mtime 不变 | | | ☐ |
| 9 | 删 `.anthropic_oauth.json` / `google_oauth.json` 后 status 仍 OK | | | ☐ |
| 10 | `INTELLECT_OAUTH_READ_AUTH_JSON=1` 回滚读文件 | | | ☐ |
| E1 | Settings 启用飞书 → `/login` 真机授权回调 | | | ☐ |
| E2 | Settings 启用企微 → `/login` 真机授权回调 | | | ☐ |
| E3 | Settings 启用钉钉 → `/login` 真机授权回调 | | | ☐ |

**签字栏：** __________________  **日期：** __________

---

## 执行提示

```bash
# Agent 自动化子集
scripts/run_tests.sh tests/intellect_cli/test_setup_oauth_seed.py \
  tests/intellect_cli/test_doctor_oauth_drift.py \
  tests/agent/test_oauth_model_tokens.py -q

# WebUI 企业 OAuth E2E（需 intellect-agent 同级目录）
cd intellect-webui && python -m pytest \
  tests/test_login_feishu_oauth_e2e.py \
  tests/test_login_enterprise_oauth_e2e.py -q
```

真机 E2E：在 Settings → Auth Services 启用 provider 并填 DB 凭证，打开 `/login` 完成一次完整 authorize → callback → 会话建立。

---

## 相关文档

| 文档 | 用途 |
|------|------|
| [oauth-follow-up-tasks.md](oauth-follow-up-tasks.md) | agent/webui 任务对照表 |
| [oauth-device-code-msal-p3.md](oauth-device-code-msal-p3.md) | P3 可选：设备码统一 / MSAL（**不挡本签字工单**） |
| [remaining-phases-plan.md](remaining-phases-plan.md) | Phase 总览与 backlog 优先级 |
