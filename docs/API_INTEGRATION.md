# Intellect Agent 外部平台接口接入文档

本文档面向需要接入 Intellect Agent 的外部业务平台，说明以下接口：

1. 获取用户自定义 Skill：`GET /v1/skills/custom`
2. 使用标准 OpenAI Chat Completions 接口调用 Skill：`POST /v1/chat/completions`
3. API Server 心跳检测：`GET /health`（8642端口）
4. WebUI 心跳检测：`GET /health`（9119端口）

本文档对应当前 Docker 单容器部署，镜像版本为 `intellect-agent:0.6.7`。

接口汇总：

| 接口 | 方法 | 是否需要API Key | 主要用途 |
|---|---|---|---|
| `/v1/skills/custom` | GET | 是 | 获取用户自定义且当前可调用的Skill |
| `/v1/chat/completions` | POST | 是 | 通过 `/<skill-name>` 调用Skill |
| `:8642/health` | GET | 否 | 检测API Server是否存活 |
| `:9119/health` | GET | 否 | 检测WebUI是否存活 |

## 1. 接口地址

当前示例部署地址：

| 服务 | 基础地址 | 说明 |
|---|---|---|
| API Server | `http://192.168.50.129:8642` | Skill 查询、Chat Completions 和 API 心跳 |
| WebUI | `http://192.168.50.129:9119` | WebUI 页面和 WebUI 心跳 |

生产环境中应将示例地址替换为实际域名或主机地址，例如：

```text
https://intellect-api.example.com
https://intellect-web.example.com
```

如果外部平台无法访问以上端口，请检查：

- 仓库根目录 `.env` 中 `API_SERVER_BIND_HOST` 和 `WEBUI_BIND_HOST` 是否为 `0.0.0.0`。
- Linux 防火墙是否允许访问8642和9119端口。
- 云主机安全组是否开放对应端口。
- 反向代理是否正确转发请求和流式响应。

## 2. 认证方式

Skill 查询和 Chat Completions 接口使用 Bearer Token 认证：

```http
Authorization: Bearer <API_SERVER_KEY>
```

`API_SERVER_KEY` 来自仓库根目录 `.env`：

```dotenv
API_SERVER_KEY=请替换为实际密钥
```

调用 JSON 接口时还应发送：

```http
Content-Type: application/json
```

两个基础心跳接口不需要 Bearer Token。

### 2.1 通用认证失败响应

未携带密钥、密钥格式错误或密钥不匹配时返回：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
```

```json
{
  "error": {
    "message": "Invalid API key",
    "type": "invalid_request_error",
    "code": "invalid_api_key"
  }
}
```

外部平台不得把 `API_SERVER_KEY` 写入浏览器前端代码、公开日志或错误提示。推荐由平台后端调用 Intellect API。

## 3. 获取用户自定义 Skill

### 3.1 接口定义

```http
GET /v1/skills/custom
```

完整地址：

```text
http://192.168.50.129:8642/v1/skills/custom
```

用途：列出当前 Profile 中已启用、兼容 `api_server` 平台、且来源属于本地自定义的 Skill。

### 3.2 “用户自定义 Skill”的判定规则

接口会排除：

- `.bundled_manifest` 中记录的系统内置 Skill。
- `.hub/lock.json` 中记录的 Skills Hub 安装 Skill。
- 已全局禁用的 Skill。
- 针对 `api_server` 平台禁用的 Skill。
- 与当前运行平台不兼容的 Skill。

接口可以返回：

- 用户手工创建的 Skill。
- Agent 创建的 Skill。
- 团队目录中的本地 Skill。
- 项目目录中的本地 Skill。
- 成员目录中的本地 Skill。
- 配置在外部 Skill 目录中的本地 Skill。

即使用户修改了系统内置 Skill 的内容，只要名称仍存在于 bundled manifest 中，它仍然按系统内置来源处理，不会出现在该接口中。

### 3.3 请求示例

Linux curl：

```bash
curl -fsS "http://192.168.50.129:8642/v1/skills/custom" \
  -H "Authorization: Bearer ${API_SERVER_KEY}"
```

PowerShell：

```powershell
$headers = @{
    Authorization = "Bearer $env:API_SERVER_KEY"
}

Invoke-RestMethod `
    -Method Get `
    -Uri "http://192.168.50.129:8642/v1/skills/custom" `
    -Headers $headers
```

### 3.4 成功响应

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "object": "list",
  "data": [
    {
      "name": "contract-review",
      "description": "检查合同文本中的风险条款并生成审阅意见。",
      "category": "legal"
    },
    {
      "name": "monthly-report",
      "description": "根据业务数据生成月度报告。",
      "category": null
    }
  ]
}
```

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `object` | string | 是 | 固定为 `list` |
| `data` | array | 是 | 自定义 Skill 数组 |
| `data[].name` | string | 是 | Skill 名称 |
| `data[].description` | string | 是 | Skill 描述，可能为空字符串 |
| `data[].category` | string/null | 是 | Skill 所在目录分类；未分组时为 `null` |

注意：`category` 是组织分类，不是来源字段。`category: null` 不代表该 Skill 不是自定义 Skill。

接口不会返回：

- `SKILL.md` 文件内容。
- Skill 的绝对路径。
- Provider API Key。
- 其他凭据或环境变量。

结果按照 `category + name` 排序，不分页。

### 3.5 没有自定义 Skill

没有符合条件的 Skill 时仍返回 `200 OK`：

```json
{
  "object": "list",
  "data": []
}
```

### 3.6 枚举失败

```http
HTTP/1.1 500 Internal Server Error
```

```json
{
  "error": {
    "message": "Failed to enumerate custom skills",
    "type": "server_error",
    "param": null,
    "code": null
  }
}
```

### 3.7 Docker目录中的自定义 Skill

当前 Compose 使用本地目录挂载：

```text
宿主机：intellect-config
容器内：/opt/data
```

自定义 Skill 示例位置：

```text
intellect-config/skills/monthly-report/SKILL.md
```

最小 `SKILL.md` 示例：

```markdown
---
name: monthly-report
description: 根据业务数据生成月度报告。
---

# 月度报告

按照用户提供的数据生成结构化月度报告。
```

为了让外部平台可以直接使用返回的 `name` 构造斜杠命令，建议自定义 Skill 名称只使用：

```text
小写英文字母、数字和连字符
```

例如：

```text
monthly-report
contract-review
data-quality-check
```

不要在名称中使用空格、斜杠、中文标点或其他特殊字符。

当前斜杠命令会把Skill名称转换为命令标识：名称转为小写，空格和下划线转换为连字符，其他非英文字母、数字和连字符的字符会被移除，连续连字符会被合并。例如：

| Skill名称 | 斜杠命令 |
|---|---|
| `monthly-report` | `/monthly-report` |
| `Monthly Report` | `/monthly-report` |
| `monthly_report` | `/monthly-report` |

为了避免外部平台重复实现规范化规则，生产环境仍建议直接使用规范的 `kebab-case` 名称，使接口返回的 `name` 加上 `/` 后就是最终调用命令。

新增或删除 Skill 后，建议重新加载 Skill 或重启容器。Docker部署中可以执行：

```bash
cd ~/intellect-agent
docker compose restart
```

## 4. 使用 Chat Completions 调用 Skill

### 4.1 接口定义

```http
POST /v1/chat/completions
```

完整地址：

```text
http://192.168.50.129:8642/v1/chat/completions
```

该接口兼容 OpenAI Chat Completions 请求和响应格式。

### 4.2 Skill调用语法

在最后一条用户消息的 `content` 开头写入：

```text
/<skill-name> [用户任务]
```

例如：

```text
/monthly-report 请根据以下销售数据生成本月报告：……
```

正确写法：

```json
{
  "role": "user",
  "content": "/monthly-report 请根据以下数据生成报告"
}
```

以下写法不属于当前 Chat Completions 的明确 Skill 调用协议：

```json
{
  "skill": "monthly-report",
  "messages": [
    {
      "role": "user",
      "content": "请根据以下数据生成报告"
    }
  ]
}
```

`/v1/chat/completions` 当前不会读取顶层 `skill` 字段。即使请求返回 `200`，也不能据此判断顶层 `skill` 已生效。

### 4.3 推荐调用流程

外部平台应按以下顺序调用：

1. 调用 `GET /v1/skills/custom` 获取可用 Skill。
2. 在平台页面展示 `name` 和 `description`。
3. 用户选择一个 Skill。
4. 平台校验所选名称仍存在于列表中。
5. 将用户消息构造为 `/<skill-name> <用户任务>`。
6. 调用 `POST /v1/chat/completions`。
7. 读取 `choices[0].message.content`。

推荐在平台后端保存已获取的 Skill 列表，并设置较短缓存，例如30至60秒。实际调用前仍应校验用户提交的 Skill 名称，避免把任意文本直接拼接为斜杠命令。

### 4.4 非流式请求

请求：

```json
{
  "model": "intellect-agent",
  "messages": [
    {
      "role": "user",
      "content": "/monthly-report 请根据以下销售数据生成本月报告：产品A销售100件，产品B销售80件。"
    }
  ],
  "stream": false
}
```

Linux curl：

```bash
curl -fsS "http://192.168.50.129:8642/v1/chat/completions" \
  -H "Authorization: Bearer ${API_SERVER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "intellect-agent",
    "messages": [
      {
        "role": "user",
        "content": "/monthly-report 请根据以下销售数据生成本月报告：产品A销售100件，产品B销售80件。"
      }
    ],
    "stream": false
  }'
```

PowerShell：

```powershell
$headers = @{
    Authorization = "Bearer $env:API_SERVER_KEY"
    "Content-Type" = "application/json"
}

$body = @{
    model = "intellect-agent"
    messages = @(
        @{
            role = "user"
            content = "/monthly-report 请根据以下销售数据生成本月报告：产品A销售100件，产品B销售80件。"
        }
    )
    stream = $false
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Method Post `
    -Uri "http://192.168.50.129:8642/v1/chat/completions" `
    -Headers $headers `
    -Body $body
```

成功响应：

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Intellect-Session-Id: api-chat-xxxxxxxx
```

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1784286202,
  "model": "intellect-agent",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "# 本月销售报告\n\n……"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 500,
    "total_tokens": 1700
  }
}
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 本次 Chat Completion 标识 |
| `object` | string | 固定为 `chat.completion` |
| `created` | integer | 创建时间，Unix秒级时间戳 |
| `model` | string | 请求或服务端配置的模型名称 |
| `choices[0].message.role` | string | 通常为 `assistant` |
| `choices[0].message.content` | string | Agent最终输出 |
| `choices[0].finish_reason` | string | `stop`、`length` 或 `error` |
| `usage.prompt_tokens` | integer | 输入Token数量 |
| `usage.completion_tokens` | integer | 输出Token数量 |
| `usage.total_tokens` | integer | 总Token数量 |

响应头 `X-Intellect-Session-Id` 是本次会话标识。只有需要继续已有会话时才应在后续请求中主动传回该值。

### 4.5 不带上下文调用

如果每次调用都要求互不继承上下文：

- 不要发送 `X-Intellect-Session-Id` 请求头。
- `messages` 中只发送本次任务需要的消息。
- 不要把上一次 assistant 响应重新放入 `messages`。

示例：

```json
{
  "model": "intellect-agent",
  "messages": [
    {
      "role": "user",
      "content": "/monthly-report 生成一份独立的新报告"
    }
  ],
  "stream": false
}
```

### 4.6 OpenAI Python SDK示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.50.129:8642/v1",
    api_key="替换为API_SERVER_KEY",
)

response = client.chat.completions.create(
    model="intellect-agent",
    messages=[
        {
            "role": "user",
            "content": "/monthly-report 请根据以下数据生成报告",
        }
    ],
    stream=False,
)

print(response.choices[0].message.content)
```

### 4.7 流式请求

将 `stream` 设置为 `true`：

```bash
curl -N "http://192.168.50.129:8642/v1/chat/completions" \
  -H "Authorization: Bearer ${API_SERVER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "intellect-agent",
    "messages": [
      {
        "role": "user",
        "content": "/monthly-report 请生成本月报告"
      }
    ],
    "stream": true
  }'
```

普通文本分片示例：

```text
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1784286202,"model":"intellect-agent","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1784286202,"model":"intellect-agent","choices":[{"index":0,"delta":{"content":"# 本月"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1784286202,"model":"intellect-agent","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1200,"completion_tokens":500,"total_tokens":1700}}

data: [DONE]
```

Agent执行工具时还可能返回 Intellect 自定义 SSE 事件：

```text
event: intellect.tool.progress
data: {"tool":"skill_view","toolCallId":"call_xxx","status":"running"}
```

外部平台如果只兼容标准 OpenAI 文本流，可以忽略无法识别的自定义事件，但必须继续读取后续 `data:` 分片，直到收到 `data: [DONE]`。

流空闲时服务端约每30秒发送一次注释心跳：

```text
: keepalive
```

SSE客户端应忽略以冒号开头的注释行。

### 4.8 Chat Completions常见错误

#### JSON格式错误

```http
HTTP/1.1 400 Bad Request
```

```json
{
  "error": {
    "message": "Invalid JSON in request body",
    "type": "invalid_request_error",
    "param": null,
    "code": null
  }
}
```

#### 缺少messages

```http
HTTP/1.1 400 Bad Request
```

```json
{
  "error": {
    "message": "Missing or invalid 'messages' field",
    "type": "invalid_request_error"
  }
}
```

#### 没有有效的用户消息

```http
HTTP/1.1 400 Bad Request
```

```json
{
  "error": {
    "message": "No user message found in messages",
    "type": "invalid_request_error"
  }
}
```

#### Agent没有生成可用结果

```http
HTTP/1.1 502 Bad Gateway
```

```json
{
  "error": {
    "message": "Agent run did not produce a response.",
    "type": "server_error",
    "param": null,
    "code": "agent_incomplete",
    "intellect": {
      "completed": false,
      "partial": false,
      "failed": true
    }
  }
}
```

#### Skill名称不存在

`/v1/chat/completions` 的斜杠调用由 Agent 的 Skill加载流程处理，不会在HTTP入口处把未知斜杠命令转换成固定的 `404 skill_not_found`。

未知 Skill 可能产生：

- `200 OK`，但回答内容提示未找到或无法加载该 Skill。
- Agent根据任务选择其他处理方式。
- Agent执行失败时返回相应的5xx错误。

因此外部平台必须先调用 `GET /v1/skills/custom` 校验 Skill 名称，不能仅依赖 Chat Completions 的HTTP状态码判断 Skill 是否存在。

## 5. API Server心跳检测

### 5.1 接口定义

```http
GET http://192.168.50.129:8642/health
```

认证：不需要。

用途：确认 API Server 进程、监听端口和HTTP请求处理器可以响应。

请求：

```bash
curl -i "http://192.168.50.129:8642/health"
```

正常响应：

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "status": "ok",
  "platform": "intellect-agent"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | 正常时固定为 `ok` |
| `platform` | string | 固定为 `intellect-agent` |

API Server 的普通心跳没有原生 `degraded` 响应。如果服务不可用，监控平台一般会遇到：

- TCP连接被拒绝。
- 请求超时。
- 反向代理返回502或503。

该接口不会检查：

- Provider API Key是否有效。
- DeepSeek、OpenRouter等上游是否可访问。
- 模型余额或权限是否正常。
- 模型是否能生成响应。
- Skill是否能够完成任务。

API Server还提供相同内容的别名：

```http
GET /v1/health
```

以及详细状态接口：

```http
GET /health/detailed
Authorization: Bearer <API_SERVER_KEY>
```

详细接口认证后可返回 Gateway状态、平台状态、活跃Agent数量、退出原因、更新时间和PID，但仍不会发起真实模型调用。

## 6. WebUI心跳检测

### 6.1 接口定义

```http
GET http://192.168.50.129:9119/health
```

认证：不需要。

用途：确认 WebUI HTTP服务以及基础流状态可以响应。

请求：

```bash
curl -i "http://192.168.50.129:9119/health"
```

### 6.2 正常空闲响应

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "status": "ok",
  "sessions": 1,
  "active_streams": 0,
  "active_runs": 0,
  "runs": [],
  "last_run_finished_at": 1784286202.307647,
  "uptime_seconds": 456.9,
  "accept_loop": {
    "requests_total": 64,
    "last_request_at": 1784286635.903
  },
  "idle_seconds_since_last_run": 433.6
}
```

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `status` | string | 是 | `ok` 或 `degraded` |
| `sessions` | integer | 是 | 当前WebUI进程中的会话数量 |
| `active_streams` | integer | 是 | 活跃流式连接数量 |
| `active_runs` | integer | 是 | 当前运行中的Agent任务数量 |
| `runs` | array | 是 | 活跃任务概要列表 |
| `last_run_finished_at` | number/null | 是 | 最近任务完成的Unix秒级时间戳 |
| `uptime_seconds` | number | 是 | WebUI进程运行秒数 |
| `accept_loop` | object | 是 | HTTP接收循环诊断信息 |
| `accept_loop.requests_total` | integer | 通常 | 当前进程累计处理请求数 |
| `accept_loop.last_request_at` | number/null | 通常 | 最近请求的Unix秒级时间戳 |
| `idle_seconds_since_last_run` | number | 条件 | 没有活跃任务且存在完成记录时返回 |
| `oldest_run_age_seconds` | number | 条件 | 存在活跃任务时返回 |

监控平台不得要求动态字段与示例值完全相同。

### 6.3 存在活跃任务

响应可能类似：

```json
{
  "status": "ok",
  "sessions": 2,
  "active_streams": 1,
  "active_runs": 1,
  "runs": [
    {
      "stream_id": "stream_xxxxx",
      "age_seconds": 8.4
    }
  ],
  "last_run_finished_at": 1784286202.307647,
  "uptime_seconds": 820.5,
  "accept_loop": {
    "requests_total": 102,
    "last_request_at": 1784287010.123
  },
  "oldest_run_age_seconds": 8.4
}
```

`runs[]` 中除 `stream_id` 和 `age_seconds` 外，还可能带有内部运行时登记的安全概要字段。外部平台不应依赖未在本文档中明确承诺的附加字段。

### 6.4 WebUI降级响应

基础流状态异常时返回：

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
```

```json
{
  "status": "degraded",
  "sessions": 1,
  "active_streams": 0,
  "active_runs": 0,
  "runs": [],
  "last_run_finished_at": null,
  "uptime_seconds": 900.2,
  "accept_loop": {
    "requests_total": 120,
    "last_request_at": 1784287100.123
  }
}
```

### 6.5 WebUI深度健康检查

如果监控平台还需要检查会话、项目和状态存储，可以调用：

```http
GET http://192.168.50.129:9119/health?deep=1
```

深度检查包括：

- Stream共享锁。
- Stream运行状态。
- 会话数据读取。
- 项目数据读取。
- Session Store。
- SQLite `state.db` 或PostgreSQL连接。

任一必要检查失败时返回 `503` 和 `status: degraded`。

## 7. 外部监控平台配置建议

建议分别建立两个HTTP监控项。

### 7.1 API Server监控项

```text
名称：Intellect API Server
URL：http://192.168.50.129:8642/health
方法：GET
期望HTTP状态：200
期望JSON字段：status == "ok"
检测间隔：30秒
请求超时：5秒
连续失败次数：3次
```

### 7.2 WebUI监控项

```text
名称：Intellect WebUI
URL：http://192.168.50.129:9119/health
方法：GET
期望HTTP状态：200
期望JSON字段：status == "ok"
检测间隔：30秒
请求超时：5秒
连续失败次数：3次
```

建议只有在两个监控项都正常时，才将整个 Intellect Agent 服务判定为可用。

监控逻辑伪代码：

```text
api_ok = API_HTTP_STATUS == 200 and API_JSON.status == "ok"
webui_ok = WEBUI_HTTP_STATUS == 200 and WEBUI_JSON.status == "ok"

service_ok = api_ok and webui_ok
```

注意：Docker自身把容器标记为 `unhealthy` 并不会自动重启容器。当前自动重启主要发生在 Gateway或WebUI核心进程退出、导致整个容器退出之后。外部监控平台如果发现持续 `unhealthy`，应告警或通过经过授权的运维流程执行重启。

## 8. 完整平台接入示例

以下示例展示“获取Skill—用户选择—调用Skill”的完整流程。

```python
import requests

API_BASE_URL = "http://192.168.50.129:8642"
API_SERVER_KEY = "替换为API_SERVER_KEY"

headers = {
    "Authorization": f"Bearer {API_SERVER_KEY}",
}

# 第一步：获取当前可调用的用户自定义Skill。
skills_response = requests.get(
    f"{API_BASE_URL}/v1/skills/custom",
    headers=headers,
    timeout=10,
)
skills_response.raise_for_status()
skills = skills_response.json()["data"]

# 第二步：示例中选择monthly-report；实际平台应由用户从skills列表选择。
selected_skill = "monthly-report"
available_names = {item["name"] for item in skills}
if selected_skill not in available_names:
    raise ValueError("所选Skill不存在、已禁用或当前不可调用")

# 第三步：把Skill名称放在最后一条用户消息开头。
user_task = "请根据以下销售数据生成本月报告：产品A销售100件。"
payload = {
    "model": "intellect-agent",
    "messages": [
        {
            "role": "user",
            "content": f"/{selected_skill} {user_task}",
        }
    ],
    "stream": False,
}

chat_response = requests.post(
    f"{API_BASE_URL}/v1/chat/completions",
    headers={**headers, "Content-Type": "application/json"},
    json=payload,
    timeout=300,
)
chat_response.raise_for_status()

result = chat_response.json()
print(result["choices"][0]["message"]["content"])
```

## 9. HTTP状态码汇总

| 状态码 | 可能出现的接口 | 含义 |
|---|---|---|
| `200` | 所有接口 | 请求成功；Chat接口仍需检查响应内容和 `finish_reason` |
| `400` | Chat Completions | JSON、messages、用户消息或内容格式无效 |
| `401` | Skill列表、Chat Completions | Bearer Token缺失或无效；`/health/detailed` 未认证时只返回精简信息 |
| `403` | API Server浏览器请求 | CORS来源不允许，或当前身份无权限 |
| `413` | Chat Completions | 请求体超过服务端限制 |
| `500` | Skill列表、Chat Completions | 服务端内部异常 |
| `502` | Chat Completions | Agent未生成可用响应或执行不完整 |
| `503` | WebUI健康检查 | WebUI基础或深度检查处于降级状态 |

## 10. 安全与生产建议

- 生产环境优先使用HTTPS，避免API Key和业务内容以明文传输。
- 不要把 `API_SERVER_KEY` 放入浏览器JavaScript、移动端安装包或公开仓库。
- 将8642端口限制为业务平台后端或监控平台可访问。
- 如果必须从浏览器直接访问API，使用 `API_SERVER_CORS_ORIGINS` 明确列出允许来源，不要在生产环境长期配置为 `*`。
- 日志中不得记录完整Authorization请求头。
- 外部平台应对用户选择的Skill名称进行白名单校验。
- 生成式模型输出应视为不可信数据；展示为HTML前必须转义或清洗。
- 普通心跳不代表模型可用。需要端到端可用性监控时，可以低频执行受控的轻量模型探测，但这会产生Provider调用和费用。

## 11. 快速验收命令

设置密钥：

```bash
export API_SERVER_KEY='替换为实际密钥'
```

检查API Server：

```bash
curl -fsS "http://192.168.50.129:8642/health"
```

检查WebUI：

```bash
curl -fsS "http://192.168.50.129:9119/health"
```

获取自定义Skill：

```bash
curl -fsS "http://192.168.50.129:8642/v1/skills/custom" \
  -H "Authorization: Bearer ${API_SERVER_KEY}"
```

调用自定义Skill：

```bash
curl -fsS "http://192.168.50.129:8642/v1/chat/completions" \
  -H "Authorization: Bearer ${API_SERVER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "intellect-agent",
    "messages": [
      {
        "role": "user",
        "content": "/monthly-report 请生成一份测试报告"
      }
    ],
    "stream": false
  }'
```

验收标准：

- 两个心跳接口均返回HTTP 200且 `status` 为 `ok`。
- `/v1/skills/custom` 返回 `object: list`。
- 目标Skill出现在 `data` 数组中。
- Chat Completions返回 `object: chat.completion`。
- `choices[0].message.content` 包含目标Skill按其说明生成的结果。
