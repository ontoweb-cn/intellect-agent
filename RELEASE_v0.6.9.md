# v0.6.9 — 对外通道补齐 + MCP SDK 2.x 迁移 + 依赖清扫

## Overview

v0.6.9 是一条**对齐外部集成方与依赖健康度**的维护版本，主题词是"补齐与清理"，而非新
功能扩张。

两条对外通道（`api_server` 的 `POST /v1/runs` 与 ACP）此前的缺口被填平：run 支持逐回合
指定模型并**如实回显**实际使用的模型；`clarify` 与 `subagent_progress` 两类信息终于能
送达外部客户端，集成方不再只见静默的工具调用、也不再收不到澄清提问。

依赖侧完成了一次必须做的破坏性迁移——MCP Python SDK 从 1.x 跨到 2.x。为避免把代码锁死
在新大版本上，新增 `tools/mcp_compat.py` 适配层，使同一套代码在 2.x 与 1.x 上都能运行。
同时清扫了 13 个 dependabot 分支，并修复了两处会**静默劣化**的工程缺陷（ACP pin 漂移会
降级已安装版本、docs 构建因缺失逗号而全量失败）。

从 v0.6.8 到 v0.6.9 共 **35 个提交**，主要贡献者：simongu（17）、dependabot（13）、
github-actions（5）。

## Highlights

### 对外通道补齐

| 能力 | 变更 |
|------|------|
| 逐回合 model（#126） | `POST /v1/runs` 读取并校验 `model`，透传到 agent；run 状态与 202 响应回显**实际使用**的模型 |
| clarify（#125） | api_server: `clarify.request` 事件 + `POST /v1/runs/{run_id}/clarify`；ACP: 桥接 `elicitation/create` |
| subagent_progress（#125） | 两条通道均转发委派子代理的运行中进度摘要 |
| 工具集 | `clarify` 加入 `intellect-api-server` 与 `intellect-acp` |

**为什么重要**：`/v1/runs` 此前把请求体里的 `model` 写进 run 状态、却用 config 默认模型
跑 agent——集成方读到的是一个**从未被使用的模型名**。这不是"缺回显"，而是错误的回显。

**一处结论更正**：ACP clarify 曾在 #125 中以"pinned SDK 无 elicitation"为由暂缓。该判断
是错的——当时读的是 `.venv` 里实际安装的 0.9.0 源码，而非 `pyproject.toml` 声明的 0.12.1。
0.12.1 自带 `elicitation/create`，因此该功能无需升级 SDK 即可实现。

### MCP Python SDK 2.x 迁移

mcp 1.28.1 → 2.1.1 是破坏性大版本，涉及服务端类改名、全量字段命名、HTTP 传输库替换等。
通过新增适配层 `tools/mcp_compat.py` 而非硬改代码，保留了 1.x 兼容性：

| 破坏点 | 处理 |
|--------|------|
| `FastMCP` → `MCPServer` | `mcp_compat.server_class()` |
| 字段 camelCase → snake_case | `mcp_compat.mcp_field()`（先 snake 再回退 camel） |
| 传输改用 httpx2 | `mcp_compat.http_lib()` |
| `streamablehttp_client` 移除、`streamable_http_client` 改二元组 | 双路径 + 元组解包 |
| `OAuthClientProvider` 移除 `timeout`、回调需返回 `AuthorizationCodeResult` | `supported_kwargs()` / `authorization_code_result()` |

### 工程修复（两处静默劣化）

- **ACP pin 漂移**：`tools/lazy_deps.py` 的 `tool.acp` 停在 0.9.0，而 `pyproject.toml` 已是
  0.12.1。由于 `active_features()` 按**存在性**判断，`intellect update` 会把已装的 0.12.1
  **降级回 0.9.0**，静默摧毁 ACP 的 elicitation 能力。已同步 pin，并新增 pin 一致性契约
  测试（对 ACP 有强制约束力）。
- **docs 构建全量失败**：`website/sidebars.ts` 缺少一个逗号，导致 `npm run build` 报
  ParseError，所有站点部署失败。已修复并本地验证构建通过。

另：skills-index 探针改为仅在状态变化或上条评论超 24h 时追加评论（此前每 4 小时一条，
已累积 474 条）。

## Full Changelog

### ✨ Features

- `POST /v1/runs` 逐回合 `model` 覆盖 + 在 run 状态与 202 响应中如实回显（#126）
- api_server: `clarify.request` 事件 + `POST /v1/runs/{run_id}/clarify` 端点（#125）
- ACP: `clarify` 经 `elicitation/create` 交付——选择题映射 enum，开放式自由文本（#125）
- api_server / ACP: `subagent_progress` 转发（#125）
- `clarify` 纳入 `intellect-api-server` 与 `intellect-acp` 工具集

### 🐛 Bug Fixes

- docs: `sidebars.ts` 缺失逗号导致 Docusaurus 构建 ParseError
- ACP: `lazy_deps` 的 pin 漂移会在 `intellect update` 时把 0.12.1 降级回 0.9.0
- run 状态曾回显从未被 agent 实际使用的模型名

### ⚡ Performance / Robustness

- MCP 集成经 `tools/mcp_compat.py` 同时支持 SDK 2.x 与 1.x（2.1.1 / 1.28.1 测试面一致）

### ♻️ Refactoring / Governance

- 合并 13 个 dependabot 分支（pip / npm / GitHub Actions）
- 新增 `lazy_deps` ↔ `pyproject` pin 一致性契约测试（已知漂移登记为只收缩清单）
- watchdog 探针评论降噪（<24h 同状态不重复追加）

### 📝 Documentation

- `docs/plans/2026-09-12-acp-version-drift-fix.md`：ACP 漂移修复与迁移计划
- 更正 #125 关于 ACP elicitation 支持的结论

## Verification

```bash
# Verify version (auto-reads pyproject.toml)
python3 -c "from intellect_cli import __version__; print(__version__)"   # 0.6.9

# Rust handshake constant must match Cargo.toml
python3 -c "import intellect_community_core as c; print(c.RUST_CORE_VERSION)"

# Lockstep tests (pyproject ↔ Cargo ↔ rust handshake ↔ ACP registry)
uv run python -m pytest tests/acp/test_registry_manifest.py tests/scripts/test_release_acp_registry.py -q

# MCP migration holds on both SDK majors
.venv/bin/python -m pytest tests/tools/ tests/test_mcp_serve.py -q -k mcp
```

See [CHANGELOG.md](CHANGELOG.md) for the milestone-level index.
