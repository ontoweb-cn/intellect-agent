# OAuth 设备码与 MSAL 原生（P3 可选）

**日期：** 2026-06-05  
**状态：** 📋 规划参考 — 不挡 `v0.4.2` → `main` 合并  
**关联：** [remaining-phases-plan.md](remaining-phases-plan.md)、[oauth-unified-platform.md](oauth-unified-platform.md)、[oauth-follow-up-tasks.md](oauth-follow-up-tasks.md)

---

## 定位

| 维度 | 说明 |
|------|------|
| 优先级 | **P3** — 在 §9 人工 QA、`v0.4.2` 合并、i18n 全量 parity 之后 |
| 与 DB-only 迁移关系 | 解决 **token 存哪**；本项解决 **用户怎么完成授权**（流程形态） |
| 仓 | 主要 **intellect-agent + CLI**；WebUI 已有部分设备码 UI（Codex onboarding、Settings `auth_flow`） |

---

## 一、OAuth 设备码（Device Code Flow）

### 1.1 是什么

[RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628) **设备授权流**，适用于：

- SSH 远程机（服务器无浏览器）
- 无头环境、容器
- 用户在**另一台设备**完成授权

```
CLI/服务器                         用户浏览器
    │ POST device_code 端点              │
    │← user_code + verification_uri    │
    │ 显示 URL + 代码 ─────────────────→ 打开页面、输入代码
    │ 轮询 token 端点                    │
    │← access_token                    │
```

与 **PKCE + loopback**（`http://127.0.0.1:18923/callback`）对比：

| | PKCE loopback | Device code |
|--|---------------|-------------|
| 场景 | 本机有浏览器 | 远程 / 无浏览器 |
| 回调 | 本地 HTTP 收 `code` | 无回调，轮询 token |
| 体验 | 自动跳转 | 手动打开 URL + 输入代码 |

### 1.2 Intellect 已实现（分散）

**模型 OAuth（API 凭证）**

| Provider | 位置 | 说明 |
|----------|------|------|
| OpenAI Codex | WebUI `api/oauth.py` + onboarding | 服务端持有 `flow_id`；token 写 `oauth_tokens`（默认不写 `auth.json`） |
| ONTOWEB Portal | `intellect_cli/auth.py` → `_ontoweb_device_code_login` | Portal device code → inference JWT → credential pool |
| GitHub Copilot | `intellect_cli/copilot_auth.py` | 独立 device code 实现 |

**成员登录**

```bash
intellect members login --oauth github --device
```

实现：`intellect_cli/main.py` → `_oauth_device_flow`（GitHub 硬编码端点；其他 provider 可走 OIDC `device_authorization_endpoint`）。

**统一引擎（薄封装）**

- `agent/oauth/__init__.py` → `OAuthEngine._start_device_code()`
- `agent/oauth/login_flow.py`：`auth_flow == "device_code"` 时走设备码
- 内置 catalog：**ONTOWEB** = `device_code`；**Azure AD 成员登录** = `oidc_discovery`（非 device code）

**WebUI 配置**

- Settings → Auth Services：`auth_flow: device_code`，字段 `device_code_url`、`token_url` 等
- i18n：`oauth_device_step1`、`oauth_device_step2`、`oauth_device_polling`

### 1.3 P3 待统一（规划）

[oauth-unified-platform.md](oauth-unified-platform.md) 目标结构：

```
agent/oauth/flows/device_code.py   # 从 Codex / ONTOWEB / Copilot / members --device 抽离
```

建议交付：

1. 统一轮询、超时、`authorization_pending` / `slow_down` 处理  
2. WebUI 成员登录支持 device code（不仅 CLI `--device`）  
3. 任意 DB `oauth_providers` 行可经同一引擎完成：发起 → 展示 code → 轮询 → 存 token  
4. 与 `oauth_pending_states`（schema v23）存 device session 状态对齐  

**CLI 参考**

```bash
# 远程 SSH（成员登录）
intellect members login --oauth github --device

# 模型 OAuth（本机 / WebUI）
intellect auth add openai-codex          # WebUI onboarding 设备码
intellect auth add ontoweb               # Portal device code
```

---

## 二、MSAL 原生（Microsoft Authentication Library）

### 2.1 是什么

**MSAL**（`msal-python` 等）是微软官方 OAuth/OIDC 客户端，面向 **Azure AD / Entra ID** 交互式登录：

- Windows **WAM broker**（系统账户选择器、与 Office/Teams SSO）
- macOS Keychain 等原生凭据缓存
- Refresh、tenant、条件访问（MFA）由库处理
- `acquire_token_interactive()`、`acquire_token_by_device_flow()` 等 API

### 2.2 与现有 Azure 能力区分

| 能力 | 模块 | 用途 |
|------|------|------|
| **Azure AD 成员 SSO** | `members_oauth` preset `azure_ad`，`auth_flow=oidc_discovery` | 身份证明 → `identities` 表 |
| **Entra ID 推理鉴权** | `agent/azure_identity_adapter.py` + `azure-identity` | Foundry/API：`DefaultAzureCredential`（SP、MI、Azure CLI…） |

`azure_identity_adapter` 文档中的 **Broker (WAM)** 属于**推理侧**凭据链，**不是**成员 OAuth 登录的 MSAL 集成。

当前 Azure AD 成员路径：手工拼 authorize URL → 浏览器 → callback → urllib 换 token → 写 `identities`（未使用 `msal.PublicClientApplication`）。

### 2.3 P3 可选交付

1. `intellect members login --oauth azure_ad` 在 Windows/macOS 优先走 MSAL interactive  
2. `--device` 回退 MSAL device flow（远程 SSH + 企业 Azure）  
3. 与现有 `oidc_discovery` 并存，由 config 或 feature flag 选择  
4. 新依赖 `msal`（须符合 `pyproject.toml` 上界策略）  

**概念示例（非现有代码）：**

```python
app = msal.PublicClientApplication(client_id, authority=tenant)
result = app.acquire_token_interactive(scopes)
# 或远程：
flow = app.initiate_device_flow(scopes)
result = app.acquire_token_by_device_flow(flow)
```

---

## 三、实施顺序建议

```text
§9 人工 QA 签字 → v0.4.2 合并 main
        ↓
（可选）设备码 flows 模块统一
        ↓
（可选）Azure AD MSAL 分支
```

两条线可**独立 PR**：设备码泛化所有 provider；MSAL 仅 Azure AD 成员登录。

---

## 四、测试与验证

**现有回归（设备码相关）**

```bash
# Agent
scripts/run_tests.sh tests/intellect_cli/test_auth_ontoweb_provider.py -q

# WebUI Codex onboarding（设备码，服务端 flow_id）
cd intellect-webui && python -m pytest tests/test_issue1362_codex_oauth_onboarding.py -q
```

**MSAL 落地后建议新增**

- Mock `msal.PublicClientApplication` 的 interactive / device flow  
- Azure AD 成员登录 E2E（可选真机 tenant）  

---

## 五、不在本项范围

- OAuth token 迁 DB（`oauth_tokens`、`oauth_providers`）— 已在 `v0.4.2` 完成  
- 飞书 / 企微 / 钉钉 企业 IdP — PKCE loopback + DB 凭证，见 [oauth-qa-signoff.md](oauth-qa-signoff.md)  
- Foundry `model.auth_mode: entra_id` — 已见 `website/docs/guides/azure-foundry.md`
