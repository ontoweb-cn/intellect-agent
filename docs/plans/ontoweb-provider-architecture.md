# ONTOWEB Provider 架构分析

**日期：** 2026-06-01
**代码库：** intellect-agent `v0.4.1`

---

## 一、总览

ONTOWEB Provider（原名 OntoWeb Provider）是 intellect-agent 的**核心订阅与推理后端**。它不仅是一个 LLM 推理 provider，还承载了：

| 角色 | 说明 |
|------|------|
| **推理后端** | 通过 `https://inference.ontoweb.cn/v1` 提供 300+ 模型 |
| **OAuth 认证** | device-code 流程登录，跨 profile 共享凭证 |
| **订阅管理** | 免费/付费 tier 检测，模型分区，授权检查 |
| **Tool Gateway 编排** | 6 个托管工具后端（web、TTS、image、video、browser、modal） |
| **速率限制** | 跨会话 429 断路保护 |

### 代码分布

```
plugins/model-providers/ontoweb/       — Provider 插件（注册 + 请求构建）
intellect_cli/ontoweb_subscription.py  — 订阅功能状态 + Tool Gateway 编排 (884 行)
intellect_cli/ontoweb_account.py       — Portal 账户授权检查 (683 行)
intellect_cli/auth.py                  — OAuth 登录 + 凭证解析 (~200 行相关)
agent/ontoweb_rate_guard.py            — 跨会话速率限制 (326 行)
intellect_cli/tools_config.py          — Tool Gateway 配置 UI (~200 行相关)
tools/managed_tool_gateway.py          — 托管网关 URL/Token 解析
tools/tool_backend_helpers.py          — 工具后端门控函数
agent/prompt_builder.py                — 系统提示注入
agent/conversation_loop.py             — 速率限制检查 + 429 处理
intellect_cli/proxy/adapters/ontoweb_portal.py  — 代理上游适配器
intellect_cli/portal_cli.py            — `intellect portal` CLI 命令
intellect_cli/models.py                — 精选模型列表
```

---

## 二、核心模块详解

### 2.1 Provider 插件

**文件：** `plugins/model-providers/ontoweb/__init__.py`

```
class OntowebProfile(ProviderProfile):
    aliases = ("ontoweb-portal", "ontoweb")
    env_vars = ("ONTOWEB_API_KEY",)
    base_url = "https://inference.ontoweb.cn/v1"
    auth_type = "oauth_device_code"
```

职责：
- 向 provider 注册表注册 `ontoweb` profile
- `build_extra_body()`：每次 API 请求附加产品标签
- `build_api_kwargs_extras()`：处理 reasoning 配置（ONTWEB 特有：reasoning 显式关闭时从请求体省略）

### 2.2 订阅功能状态

**文件：** `intellect_cli/ontoweb_subscription.py`

核心入口函数：

```
get_ontoweb_subscription_features(config, force_fresh=False) → OntowebSubscriptionFeatures
```

返回的 `OntowebSubscriptionFeatures` 包含 6 个托管工具的特性状态：

| Feature Key | 工具 | 网关 Vendor |
|-------------|------|-------------|
| `web` | 网页搜索/提取 | `firecrawl` |
| `image_gen` | 图像生成 | `fal-queue` |
| `tts` | 文本转语音 | `openai-audio` |
| `browser` | 浏览器自动化 | `browser-use` |
| `video_gen` | 视频生成 | `fal-queue` |
| `modal` | 容器执行 | `modal` |

每个 feature 的状态计算逻辑：
1. 检查 toolset 是否启用
2. 检查是否有直接 API key（优先级高于托管）
3. 检查 Portal 付费授权
4. 检查 `use_gateway: true` 配置

### 2.3 Portal 账户授权

**文件：** `intellect_cli/ontoweb_account.py`

```
get_ontoweb_portal_account_info(force_fresh=False) → OntowebPortalAccountInfo
```

解析链路（优先级递减）：
1. JWT 解码（快速，无网络请求）→ 检查 TTL
2. `/api/oauth/account` API 调用（force_fresh 或 JWT 过期时）
3. 凭证池回退（OAuth pool entries → inference key pool entries）

结果缓存 60 秒。

关键属性：
- `logged_in` — 是否登录
- `paid_service_access` — 是否付费（`True`/`False`/`None`）
- `is_paid` / `is_free_tier` / `tool_gateway_entitled` — 便利属性

### 2.4 OAuth 登录

**文件：** `intellect_cli/auth.py`（行 7307+）

```
_ontoweb_device_code_login(portal_url, inference_url, client_id, scope)
```

流程：
1. POST 到 Portal 的 device-code 端点
2. 显示 `verification_uri_complete` URL + `user_code`
3. 自动打开浏览器（远程会话跳过）
4. 轮询 token 完成（间隔 1 秒）
5. 交换 OAuth token → inference-scoped JWT
6. 保存到 `auth.json`，镜像到共享存储，同步凭证池

### 2.5 速率限制

**文件：** `agent/ontoweb_rate_guard.py`

```
record_ontoweb_rate_limit(headers, error_context)  # 429 到达时
ontoweb_rate_limit_remaining() → Optional[int]      # API 调用前检查
clear_ontoweb_rate_limit()                          # 成功响应后清除
is_genuine_ontoweb_rate_limit(headers) → bool       # 区分真假 429
```

关键设计：`is_genuine_ontoweb_rate_limit()` 区分**账户级别耗尽**（剩余=0，重置≥60s）和**单模型上游容量不足**（不放断路，否则会阻止所有其他模型）。

状态文件：`~/.intellect/rate_limits/ontoweb.json`

---

## 三、依赖关系图

### 3.1 谁依赖 ONTOWEB Provider

```
                    ┌──────────────────────────────────┐
                    │     intellect_cli/auth.py        │
                    │  OAuth 登录 · 凭证解析 · 模型选择 │
                    └──────────┬───────────────────────┘
                               │ provider="ontoweb"
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  setup.py     │   │  main.py         │   │  models.py       │
│  快速设置向导  │   │  _model_flow_ontoweb│   │  精选模型列表     │
└───────────────┘   └──────────────────┘   └──────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ ontoweb_      │   │ ontoweb_         │   │ tools_config.py  │
│ subscription │   │ account.py       │   │ Tool Gateway UI  │
│ 订阅功能状态  │   │ Portal 账户信息   │   │ 托管后端配置     │
└───────┬───────┘   └──────────────────┘   └────────┬─────────┘
        │                                           │
        ▼                                           ▼
┌──────────────────────────────────────────────────────────┐
│                 工具层 (6 个工具)                         │
│                                                          │
│  web_tools.py        → managed_ontoweb_tools_enabled()   │
│  tts_tool.py         → managed_ontoweb_tools_enabled()   │
│  image_generation.py → managed_ontoweb_tools_enabled()   │
│  terminal_tool.py    → managed_ontoweb_tools_enabled()   │
│  transcription.py    → managed_ontoweb_tools_enabled()   │
│  firecrawl plugin    → managed_ontoweb_tools_enabled()   │
│  browser_use plugin  → managed_ontoweb_tools_enabled()   │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│           managed_tool_gateway.py                        │
│                                                          │
│  resolve_managed_tool_gateway(vendor)                    │
│    → https://{vendor}-gateway.ontoweb.cn                 │
│    → read_ontoweb_access_token()                            │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Agent 运行时依赖

```
┌──────────────────────────────────────────────────────────┐
│                  agent/conversation_loop.py               │
│                                                          │
│  API 调用前: ontoweb_rate_limit_remaining() 检查             │
│  API 成功后: clear_ontoweb_rate_limit()     清除             │
│  API 429:    is_genuine_ontoweb_rate_limit() 判断            │
│              record_ontoweb_rate_limit()     记录            │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                  agent/prompt_builder.py                  │
│                                                          │
│  build_ontoweb_subscription_prompt()                      │
│    → 系统提示中注入托管工具能力说明                        │
│    → 指导 LLM 不要为 OntoWeb 托管功能索要 API key             │
└──────────────────────────────────────────────────────────┘
```

### 3.3 CLI 命令依赖

| 命令 | 模块 | 功能 |
|------|------|------|
| `intellect model` | `main.py:_model_flow_ontoweb()` | 选择 ONTOWEB 作为 provider，触发 OAuth + 模型选择 |
| `intellect setup` | `setup.py` | 快速设置路由到 ONTOWEB Portal |
| `intellect tools` | `tools_config.py` | 显示托管后端，内联登录 |
| `intellect portal status` | `portal_cli.py` | Portal 状态 + Tool Gateway 路由 |
| `intellect portal open` | `portal_cli.py` | 打开订阅管理页面 |
| `intellect status` | `status.py` | Tool Gateway 状态段 |

---

## 四、Tool Gateway 配置流程

用户选择 ONTOWEB 托管后端的完整流程：

```
1. intellect tools → 选择类别 (e.g. "Web Search & Extract")
                    │
2. _visible_providers() 
   → 始终显示 "OntoWeb Subscription" 行
   → 已登录: "★ Included with your OntoWeb subscription"
   → 未登录: "★ via OntoWeb Portal (login on select)"
                    │
3. 用户选择托管行
   → _configure_provider()
   → managed_feature 检测
                    │
4. ensure_ontoweb_portal_access()  ← ⚠ 函数缺失!
   → 应执行: Portal 登录 + 授权检查
   → 仅 auth + entitlement，不切换 provider
                    │
5. 授权确认后:
   → 设置 use_gateway: true
   → 设置对应 provider (firecrawl/fal-queue/等)
                    │
6. 工具调用时:
   → managed_ontoweb_tools_enabled() 检查
   → resolve_managed_tool_gateway(vendor) 获取 URL + token
   → 请求通过 https://{vendor}-gateway.ontoweb.cn 代理
```

---

## 五、模型选择流程

```
intellect model → 选择 "ONTOWEB Portal"
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   未登录                    已登录
        │                       │
  1. 检查共享凭证           1. resolve_ontoweb_runtime_credentials()
  2. device-code OAuth      2. JWT 有效性检查
  3. 保存 auth.json         3. 过期→重新认证
  4. 镜像共享存储            4. 获取精选模型列表
  5. 同步凭证池                     │
        │                   5. 获取实时定价
        └───────────┬───────────┘
                    │
            6. 检查 free tier
            7. 获取 Portal 推荐模型
            8. 分区 free/paid 模型
            9. curses_radiolist 选择
           10. 保存模型选择
           11. prompt_enable_tool_gateway()
```

**模型目录来源（优先级）：**
1. 远程 `model-catalog.json`（每小时刷新）
2. 内置 `_PROVIDER_MODELS["ontoweb"]`（17 个精选模型）
3. Portal `/api/ontoweb/recommended-models`（增强推荐）

---

## 六、命名不一致问题

代码库处于 `ontoweb` → `ontoweb` 迁移的过渡状态：

### 已迁移

| 类型 | 示例 |
|------|------|
| 文件名 | `ontoweb_subscription.py`, `ontoweb_account.py`, `ontoweb_rate_guard.py` |
| 类名 | `OntowebProfile`, `OntowebSubscriptionFeatures`, `OntowebPortalAccountInfo` |
| 函数名 | `get_ontoweb_subscription_features`, `format_ontoweb_portal_entitlement_message` |

### 未迁移

| 类型 | 示例 | 原因 |
|------|------|------|
| Provider ID | `"ontoweb"` (config.yaml 中的 model.provider) | 配置键，修改会破坏兼容性 |
| 内部函数名 | `_login_nous`, `resolve_ontoweb_access_token` | 内部 API，不对外 |
| 数据类名 | `NousFeatureState`, `NousPaidServiceAccessInfo` | 内部类型 |
| 代理适配器 | `NousPortalAdapter`, name=`"ontoweb"` | 代理内部标识 |
| 速率文件 | `ontoweb.json` | 文件路径 |
| JSON 字段 | `managed_nous_feature` | Provider catalog 字段名 |

---

## 七、已知问题

### 7.1 ⚠ 关键：`ensure_ontoweb_portal_access` 函数缺失

**位置：** `intellect_cli/tools_config.py` 行 2572, 2955

```python
from intellect_cli.ontoweb_subscription import ensure_ontoweb_portal_access
```

该函数在 `_configure_provider()` 和 `_reconfigure_provider()` 中被调用，用于处理用户选择 ONTOWEB 托管后端时的内联登录。但此函数**在当前代码库中不存在**。

**测试引用：** `tests/intellect_cli/test_tools_config.py` 的 mock 指向 `intellect_cli.nous_subscription.ensure_ontoweb_portal_access`（同样不存在）。

**影响：** 用户在 TUI 中选择 "OntoWeb Subscription" 行时将遇到 `ImportError`，Tool Gateway 内联登录完全不可用。

**修复方案：** 需要从 hermes-agent 的 `hermes_cli/nous_subscription.py` 移植 `ensure_ontoweb_portal_access()` 和 `_run_nous_portal_login_only()` 两个函数。这些函数属于 hermes-agent 每日更新的一部分（commit `1fc7bdc5e`），在本次移植中未成功应用（tools_config.py 冲突）。

### 7.2 Portal URL 不一致

- `ontoweb_account.py` 默认 Portal URL：`https://portal.ontoweb.cn`
- `setup.py` 中注册 URL 引用：`https://portal.nousresearch.com/manage-subscription`

### 7.3 Provider ID 与类名不一致

- Provider 插件注册的 ID 是 `"ontoweb"`
- `auth.py` 中 `PROVIDER_REGISTRY["ontoweb"]` 使用 `"ontoweb"` 作为 key
- `models.py` 中 `_PROVIDER_MODELS["ontoweb"]` 使用 `"ontoweb"` 作为 key
- 两套 key 共存导致某些查询路径需要同时检查两个值

---

*本文档基于代码探索自动生成。*
