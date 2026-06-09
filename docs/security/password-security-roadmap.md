# Intellect Agent 密码安全改进路线图

**日期：** 2026-06-01
**基于：** 密码处理全量安全分析

---

## 总览

| 指标 | 当前值 |
|------|--------|
| 凭证存储位置 | 6 个（auth.json / .env / config.yaml / OAuth 文件 / Bitwarden / env vars） |
| 静态加密 | 0 个 |
| 高危发现 | 3 |
| 中危发现 | 4 |
| 低危发现 | 4 |
| 安全亮点 | 9 |

---

## P0 — 高危（建议立即修复）

### 1.1 BlueBubbles 密码 URL Query 传输

**现状：**

```python
# gateway/platforms/bluebubbles.py:140
def _api_url(self, path: str) -> str:
    return f"{self.server_url}{path}{sep}password={quote(self.password, safe='')}"
```

每次 API 调用将密码作为 URL query 参数明文传输，暴露面：
- 网络中间设备（HTTP 明文传输）
- 代理日志
- BlueBubbles 服务端日志
- `access_log=None` 仅关闭本地 aiohttp 日志，无法控制远端

**改进方案：**

1. **优先级 A — Header 传递**：将 password 从 URL query 移至 HTTP Header（如 `X-BlueBubbles-Password` 或 `Authorization: Bearer`，取决于 BlueBubbles 服务端是否支持）
2. **优先级 B — HTTPS 强制**：检测 `server_url` 是否以 `https://` 开头，HTTP 时打印显式安全警告并询问确认
3. **优先级 C — 审计日志**：在连接成功时记录 `server_url` 的 scheme，警告非 HTTPS 连接

**涉及文件：** `gateway/platforms/bluebubbles.py`

**风险：** 如果 BlueBubbles 服务端不支持 Header 认证，需要先与服务端协调 API 变更。

---

### 1.2 凭证静态加密

**现状：** 6 个凭证存储位置全部明文。

**改进方案：**

分阶段实施：

**Phase 1 — 利用操作系统密钥链（低摩擦）**

| 平台 | 方案 |
|------|------|
| macOS | Keychain Services (`security` CLI 或 `keyring` 库) |
| Linux | Secret Service API (D-Bus) 或文件加密回退 |
| Windows | Credential Manager (wincred) |

实现 `intellect_cli/secret_store.py`：
```python
def get_secret(key: str) -> Optional[str]: ...
def set_secret(key: str, value: str) -> None: ...
def delete_secret(key: str) -> None: ...
```

**Phase 2 — 文件级加密回退**

当密钥链不可用时：
- 使用 `cryptography.fernet` 对 `auth.json` 全量加密
- 加密密钥存储在操作系统密钥链中（仅一个密钥需要保护）
- 首次启动时生成随机加密密钥并存入密钥链
- 加密文件格式：`{ "version": 1, "encrypted": true, "ciphertext": "<base64>", "salt": "<base64>" }`

**Phase 3 — 零信任模式（可选）**

- 凭证永不落盘
- 启动时从 Bitwarden / 1Password CLI / env 获取
- `INTELLECT_SECRET_BACKEND=bitwarden` 配置开关

**涉及文件：** 新建 `intellect_cli/secret_store.py`，修改 `intellect_cli/auth.py`、`intellect_cli/config.py`、`agent/credential_pool.py`

---

### 1.3 敏感命令日志脱敏

**现状：** `agent/redact.py` 覆盖 31 种 API key 模式，但 URL query 参数不脱敏。

**改进方案：**

在 `redact.py` 中新增 `_redact_url_query_secrets()` 函数，对已知敏感 query key 进行脱敏：

```python
_SENSITIVE_QUERY_KEYS = {"password", "token", "api_key", "apikey", "secret", "key", "auth"}
```

在保持 OAuth callback query 参数可用的前提下，仅脱敏已知的凭证型参数。

**涉及文件：** `agent/redact.py`

---

## P1 — 中危（建议近期修复）

### 2.1 config.yaml 权限加固

**现状：** `~/.intellect/config.yaml` 创建时权限为 0644（全局可读），其中 `custom_providers[].api_key` 可被同机其他用户读取。

**改进方案：**

1. `save_config()` 写入后调用 `os.chmod(path, 0o600)`
2. 添加 `intellect config check` 检测项：扫描 config.yaml 中是否有 `api_key` / `api_secret` 等敏感字段，有则警告
3. 提供迁移命令：`intellect config migrate-sensitive` 将 config.yaml 中的 API key 迁至 `.env` 或密钥链

**涉及文件：** `intellect_cli/config.py`、`intellect_cli/doctor.py`

---

### 2.2 SUDO_PASSWORD 加强

**现状：** 明文存储在 `.env`，经 `sudo -S` 管道明文传输。

**改进方案：**

1. **临时 sudo ticket**：使用 `sudo -v` 预先刷新 sudo ticket，避免每次传密码
2. **提示优化**：首次配置时显示更醒目的安全警告（当前仅在 `.env.example` 注释中）
3. **超时清除**：内存中持有的 `SUDO_PASSWORD` 在每次使用后立即 `del`，减少驻留时间

**涉及文件：** `tools/approval.py`、`intellect_cli/secret_prompt.py`

---

### 2.3 WeCom/Feishu/DingTalk 凭证迁移

**现状：** 这些平台的 App Secret 和 Encrypt Key 存储在 `config.yaml` 的 `extra` 字典中（0644 全局可读）。

**改进方案：**

1. 将 `app_secret`、`encrypt_key`、`client_secret` 等敏感字段从 `config.yaml` 迁至 `.env` 或密钥链
2. 保留 `config.yaml` 中的非敏感配置（`app_id`、`corp_id`、`webhook_path` 等）
3. 向后兼容：如果 `config.yaml` 中仍有旧字段，打印迁移提示

**涉及文件：** `gateway/platforms/wecom.py`、`gateway/platforms/feishu.py`、`gateway/platforms/dingtalk.py`、`gateway/config.py`

---

### 2.4 EMAIL_PASSWORD 日志保护

**现状：** IMAP/SMTP 登录失败时可能将 `EMAIL_PASSWORD` 写入错误日志。

**改进方案：**

1. 在 email adapter 的登录调用处包裹 try/except，捕获异常后替换密码再记录
2. 使用 `redact.py` 的 `RedactingFormatter` 确保日志层面兜底

**涉及文件：** `gateway/platforms/email.py`

---

## P2 — 低危（建议排期修复）

### 3.1 进程环境变量可见性

**现状：** 所有平台 bot token 和 API key 存储在 `os.environ` 中，子进程可读取。

**改进方案：**

- 评估使用 `ContextVar` 替代 `os.environ` 传递凭证的可行性
- 在子进程 spawn 前显式清理不需要的 env vars

**涉及文件：** `tools/process_registry.py`、各 gateway platform adapter

---

### 3.2 OAuth Token 落盘数量减少

**现状：** OAuth token 存储在 4 个独立 JSON 文件中（`auth.json`、`.anthropic_oauth.json`、`.claude/.credentials.json`、`.qwen/oauth_creds.json`）。

**改进方案：**

- 将外部工具的 OAuth token 统一管理到 `auth.json` credential_pool 中
- 外部文件保留（不破坏其他工具），但 intellect-agent 优先从 `auth.json` 读取

**涉及文件：** `agent/credential_sources.py`、`agent/credential_pool.py`

---

### 3.3 凭证过期主动提醒

**现状：** 凭证仅在 API 调用失败时才发现过期，用户到那时才知道。

**改进方案：**

- `intellect status` 增加 OAuth token 过期检查（JWT `exp` 字段解码）
- Gateway 启动时主动检查即将过期的 token（24h 内），打印提醒
- 可选的 cron job：`intellect cron create --check-credentials`

**涉及文件：** `intellect_cli/status.py`、`intellect_cli/doctor.py`

---

### 3.4 凭证审计日志

**现状：** 无凭证访问审计。

**改进方案：**

- 在 `auth.json` 加载和凭证池读取时记录审计事件到 `~/.intellect/logs/credential_audit.log`
- 记录：时间、凭证来源、操作（load/refresh/use/remove）、指纹（SHA-256，非原始值）
- `intellect security audit` 命令展示最近的凭证访问记录

**涉及文件：** 新建 `intellect_cli/credential_audit.py`

---

## 实施优先级时间线

```
Week 1-2 (P0):
  └─ 1.2 Phase 1: 操作系统密钥链集成
  └─ 1.3: URL query 敏感参数脱敏

Week 2-3 (P0):
  └─ 1.1: BlueBubbles 密码传输方式改造
  └─ 2.1: config.yaml 权限 0644 → 0600

Week 3-4 (P1):
  └─ 1.2 Phase 2: 文件级加密回退
  └─ 2.3: WeCom/Feishu/DingTalk 凭证迁移
  └─ 2.2: SUDO_PASSWORD 加强

Week 5-6 (P2):
  └─ 2.4: EMAIL_PASSWORD 日志保护
  └─ 3.1-3.4: 进程隔离、Token 统一、过期提醒、审计日志
```

---

## 兼容性注意事项

1. **密钥链集成**：需保留 `.env` 文件和 `auth.json` 作为回退（CI/Docker/headless 环境无密钥链）
2. **config.yaml 权限**：修改默认权限后需在 `intellect config migrate` 中更新已有文件
3. **BlueBubbles**：需确认 BlueBubbles 服务端支持 Header 认证；如不支持需先推动服务端变更
4. **平台凭证迁移**：WeCom/Feishu/DingTalk 用户需有明确的迁移路径和回退方案

---

*本文档基于 2026-06-01 密码处理安全性全量分析生成。*
