# A3-3 keyless 端点分析（exa / parallel / firecrawl / keenable）— 实测报告

> **日期**：2026-09-02
> **方法**：对四家端点做最小化真实探测（单次请求、无凭据、短超时），以响应
> 实证代替文档推断。探测命令与响应摘录均来自当日实测。
> **结论**：**四家全部具备可实现的 keyless 形态**——与 A3-3 关账时的保守预期
> （"exa/parallel/firecrawl 待端点确认，keenable 无信息"）相比全面向好。
> tavily 已随 A3-3 实装（keyless header 实测同样通过）。

---

## 实测结论总表

| 厂商 | keyless 端点（实测） | 传输形态 | 实测结果 | 置信度 | 实装优先级 |
|---|---|---|---|---|---|
| **firecrawl** | `POST https://api.firecrawl.dev/v1/scrape`、`/v1/search` | 普通 REST JSON | 200 真实内容，无 key | ★★★ 实证 | **1（最易）** |
| **parallel** | `POST https://search.parallel.ai/mcp` | MCP JSON-RPC，**无状态**（无需 session 握手，直接 tools/list 可用） | 200，工具 `web_search`/`web_fetch` | ★★★ 实证 | **2** |
| **exa** | `POST https://mcp.exa.ai/mcp` | MCP streamable HTTP + **SSE 帧** + **session 会话制**（initialize → Mcp-Session-Id → notifications/initialized → tools/call） | 200，工具 `web_search_exa`/`web_fetch_exa`/`crawling` | ★★★ 实证 | 3（需 MCP 客户端） |
| **tavily** | `POST https://api.tavily.com/search|/extract` + header `X-Tavily-Access-Mode: keyless` | 普通 REST JSON | 200 真实结果 | ★★★ 实证 | ✅ A3-3 已实装 |
| **keenable** | 厂商真实存在：`keenable.ai`（"Independent Web Search API for AI"，2026-09-01 上线）；API 在 `api.keenable.ai`；`app.keenable.ai/llms.txt` 自述 **keyless per-IP 池（1000 次/小时）** | REST（`POST /v1/search`） | 探测 `/v1/search` 返回 401 "X-API-Key or Bearer required"——keyless 调用形态需读 `docs.keenable.ai` 确认 | ★★ 端点实证/keyless 形态待确认 | 4（文档落地后） |

---

## 逐厂商实测详情

### 1. Firecrawl — 最简单的实装点

```
POST https://api.firecrawl.dev/v1/scrape   {"url": "https://example.com"}
→ HTTP 200 {"success": true, "data": {"markdown": "# Example Domain …"}}
POST https://api.firecrawl.dev/v1/search   {"query": "…", "limit": 1}
→ HTTP 200 {"success": true, "data": [ … ]}
```

**无任何鉴权头**即返回真实抓取/搜索结果。既有 keyed 插件（`plugins/web/firecrawl`）
改造成本最低：请求体不变、去 key、加 keyless 分支即可。注意点：keyless 配额未知
（推测按 IP 限速），失败模式（402/429）归入 keyless walk 的"限流形错误前进"。

### 2. Parallel Search MCP — 无状态 JSON-RPC

```
POST https://search.parallel.ai/mcp
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
→ HTTP 200，server "Parallel Web Search MCP Server" v1.27.0
  tools = ["web_search", "web_fetch"]
```

**无需 initialize/session**——直接 `tools/list`、`tools/call` 均可用（实测 id=2
裸调用成功）。实现 = 一个 ~60 行的无状态 MCP JSON-RPC 客户端（POST + 解析
result）。既有 `plugins/web/parallel` 的 keyed REST（`api.parallel.ai`，实测
401 "FailedToResolveAPIKey"）保持不变；keyless 走 `search.parallel.ai/mcp`。

### 3. Exa MCP — 会话制 streamable HTTP（实现最重）

```
POST https://mcp.exa.ai/mcp   initialize
→ HTTP 200（SSE 帧），响应头 Mcp-Session-Id: <uuid>
POST … 同 session  notifications/initialized   （204）
POST … 同 session  tools/list
→ tools: web_search_exa(query, numResults) / web_fetch_exa / crawling
```

流程：initialize → 取 `Mcp-Session-Id` 响应头 → 发 initialized 通知 → 之后
每次调用带 session 头。响应为 **SSE 帧**（`event: message\ndata: {json}`）需解帧。
实现建议： intellect 已有完整 MCP 客户端基础设施（`tools/mcp_tool.py` 的
`_ensure_mcp_loop/_run_on_mcp_loop/_connect_server`）——**exa keyless 可作为
一个"无需配置的内置 MCP server"接入**（url=mcp.exa.ai/mcp），复用面最大。

### 4. Tavily — 已实装（A3-3），实测复核通过

`X-Tavily-Access-Mode: keyless` header 实测 200 返回真实结果，与
`plugins/web/tavily` 的 keyless 分支实现一致。

### 5. Keenable — 真实厂商，keyless 形态待文档确认

- `keenable.ai`：营销页（Framer，2026-09-01 发布），定位 "Independent Web
  Search API for AI"；
- `app.keenable.ai/llms.txt`：**"Keyless by default: no signup, no API key
  needed to start. Keyless requests share a per-IP pool capped at 1,000 an
  hour"** + 免费账号（100k 请求/月）；
- `api.keenable.ai/v1/search`（POST JSON `{"query": …}`）：401 "X-API-Key
  header or Authorization Bearer token is required"；
- `docs.keenable.ai` 存在（跳转可达），**keyless 的确切调用形态（header？专用
  路径？）需读文档确认**——llms.txt 的营销口径与探测到的 401 现状尚有出入。

---

## 实装建议（按 rolling order）

| 步 | 内容 | 规模 |
|---|---|---|
| 1 | firecrawl keyless（改既有插件，纯 REST） | S |
| 2 | parallel keyless（无状态 MCP JSON-RPC 小客户端，~60 行） | S |
| 3 | exa keyless（复用 intellect MCP 客户端基础设施接内置 server） | M |
| 4 | keenable（读 docs.keenable.ai 后实装 keyless 形态） | S |

每步独立可回退；`web.keyless_endpoint.<name>` 配置覆盖（A3-3 已预留的设计）
在端点未来变更时兜底。轮询顺序、tier 三态、rescue 语义全部复用 A3-3 框架，
无需改动框架本身。

## 隐私与配额注意

- 上述端点均为**匿名第三方服务**：keyless 开启后查询内容离开本机——
  `web.keyless_fallback` 默认 false 的裁定不变；
- firecrawl/keenable 的 keyless 配额按 IP 计（keenable 自述 1000 次/小时
  共享池）——keyless walk 的"限流形错误前进"（429 → 下一厂商）恰好适配；
- 建议在 website 隐私说明中补一句各厂商配额现状。

---

## 附：探测命令存档

```bash
# firecrawl scrape（无 key）
curl -s -X POST https://api.firecrawl.dev/v1/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
# parallel tools/list（无 session 直接可用）
curl -s -X POST https://search.parallel.ai/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
# exa initialize（SSE 帧 + Mcp-Session-Id 响应头）
curl -s -X POST https://mcp.exa.ai/mcp -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"0.1"}}}'
# keenable /v1/search（现状要求鉴权）
curl -s -X POST https://api.keenable.ai/v1/search -H "Content-Type: application/json" \
  -d '{"query":"test"}'
# tavily keyless（已实装）
curl -s -X POST https://api.tavily.com/search -H "Content-Type: application/json" \
  -H "X-Tavily-Access-Mode: keyless" -d '{"query":"test"}'
```
