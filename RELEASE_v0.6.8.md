# v0.6.8 — Multiplex Supervisor + Bot Mode + Pets + 协议加固

## Overview

v0.6.8 是 **phase0 foundations** 的集大成版本，将分散在多个分支上的里程碑（Gateway
Multiplex B1、Bot Mode B2、跨机 DM relay B2-4、协议加固 A3/M5、历史清理 G-07 闭环、
Tool Search W15、单用户卫生 W10/W11、Pets）统一合入 `main`。

架构上的主线：**单个 supervisor 进程按 profile 派生相互隔离的 gateway 子进程**（复用唯一
外部监听器 + `/p/<name>/` 前缀路由），每个 profile 可进一步成为**可被 DM 的 bot** 并跨机
中继消息。同期还交付了 deadline 系统、per-model 用量、错误分类器加固与 web-search /
project-skills / pets 生态扩展。版本号单一来源与 ACP registry 锁步测试首次随发布流程生效。

从 v0.6.7 到 v0.6.8 共 **305 个提交**，主要贡献者：simongu（203）。

## Highlights

### M3 — Gateway Multiplex（B1）：一个 supervisor，多个隔离 profile

| 能力 | 说明 |
|------|------|
| 多路复用 | `intellect gateway run --multiplex` 从单个 supervisor 派生 per-profile gateway 子进程，各自独立的 `INTELLECT_HOME`；死子进程指数退避重启，互不牵连 |
| 单监听前端 | supervisor 独占唯一外部 HTTP 监听：默认路径进默认 profile，`/p/<name>/...` 路由到对应子进程（前缀剥离）；子进程仅绑定 loopback 临时端口 |
| WS 鉴权路由 | WebSocket upgrade 按同前缀路由，路由失败 fail-closed（4404）；per-profile token（`TUI_AUTH_TOKEN_<PROFILE>`）保证 A profile 令牌打不开 B 的端点 |
| 可观测性 | `gateway status` 渲染多路拓扑；控制面 socket 上报 `role: supervisor` + 存活 `served_profiles`；`intellect doctor` 校验 serve-set pinning 与跨 profile 凭据冲突 |
| Watchdog | 子进程崩溃自动重启；`gateway.watchdog` 配置接线（TODO-013） |

### M4 + B2/B2-4 — Bot Mode：profile 即 DM-able bot

- **常开可选**（`bot_mode.enabled`，默认关）：serve set 中每个 profile 都可被 DM；roster
  由 supervisor 依据控制面存活态物化到 `bot_mode/roster.json`，`intellect bots` 可查。
- **防伪造注入**：`message_agent` 只注入标题为 "Bot Chat" 的会话，派发时二次校验会话标题 +
  归属 profile（伪造调用拿到结构化错误而非投递）；DM 正文走 0o600/0o700 临时文件而非 argv。
- **预算**：`bot_mode.max_dm_depth`（默认 3）封顶 bot→bot 链式 DM；Bot Chat 会话获得字节
  稳定的协议段（roster + capability epoch）。
- **B2-4 跨机中继**：`bot_mode.peers` 让 Bot Chat 会话 DM 到其它机器的 bot（HTTP 投递到对端
  `/p/<profile>/`，回写进发送方会话并署名）；鉴权以对端 profile API key 作共享密钥
  （`api_key_env` 间接寻址）。已证实的局限：跨机回复链受 owner 拓扑约束（见用户文档）。

### M5 — 协议加固 + 生态长尾（A3）+ Pets（PT）

- **错误分类器（A3-1）**：坏图归类为可剥离重试；Kimi/Moonshot tool-replay 400 进入可重试格
  式错误；Anthropic "out of extra usage" 转 cooldown 而非终止计费；确定性空响应不再重复计费。
- **Per-model 用量（A3-2）**：TokenAccumulator 增加 per-model 维度，`/usage` 展示分模型视图。
- **更新加固（A3-7）**：`intellect update` 优先 `uv sync --frozen --inexact --all-extras`；
  产物随附 SHA256SUMS。
- **外部会话导入（A3-5）**：`intellect sessions import` 严格转换 Claude Code / Codex JSONL
  （纯文本、不伪造 tool_calls、无 system 载荷）。
- **MCP 治理（A3-6）**：`mcp_` 前缀工具 50K 结果分级、超大重复字节折叠为引用桩、`intellect
  mcp doctor` 健康巡检。
- **Project skills（A3-4）**：`.intellect/skills` 在信任后被发现（`intellect skills trust`），
  同提交内置 fail-closed 内容隔离门。
- **Keyless 搜索池（A3-3）**：默认**关闭**的匿名搜索层（`web.keyless_fallback`），per-provider
  分层覆盖 + 轮转保底 + 一次性救援；新增 provider：parallel / tavily / exa / firecrawl / keenable。
- **Pets（PT V1）**：`agent/pet/` store/manifest/state/render 包、`intellect pets`
  CLI（list/install/select/doctor）、确定性 unicode 渲染。
- **G-21 迁移关闭**：stream_consumer 热路径实测 ~0.036% CPU，Python 保留（基准见
  bench-baseline.json）。

### M2 closeout — G-07 历史清理闭环 + deadline 系统

- 预调用清洗器内重复 tool-result 去重，关闭 G-07 验收清单最后一项；`test_history_sanitization.py`
  锁定 6 种脏历史 + 幂等 + provider pairing 不变式。
- 新增 **deadline 系统**（`agent/deadline.py` + wiring 测试）：会话/请求级截止时间守卫、
  停滞看门狗（stall guards）、流式电路熔断（stream stale circuit breaker）。

### W15 — Tool Search L2（渐进披露对齐 Hermes）

- 激活**常开**（`enabled: auto` = `on`）；`threshold_pct` 语义改为**列表预算百分比**（10→5）。
- 目录列表 `full → names → mixed → groups → none` 随预算退化；新增 `listing` /
  `listing_max_tokens` 键。
- 多查询/批描述：`tool_search queries: []`（≤10）、`tool_describe names: []`（≤10）；
  **model-facing schema 变更**（`query`→`queries`、`name`→`names`）。
- 盲调探针、bridge-aware 并行准入、检索加固（exact-name inf 计分、source-label 索引、
  Snowball stemming 可选）。
- 回滚：`enabled: off` 恢复全量 eager 暴露。

### W10/W11 — 配置默认值与单用户卫生

- `profiles.management_enabled` **默认改为 `false`**（`DEFAULT_CONFIG`），显式值不受影响。
- 永久单用户：移除幽灵 member slash CommandDefs、wiki 贡献 HTTP 路由、member
  oauth/register 公开豁免；`GET /api/members/status` 保持公开。

### 发布与工程

- 版本号 lockstep 收窄并**由测试强制**：pyproject / rust-core Cargo / `_EXPECTED_RUST_CORE_VERSION`
  握手常量 / ACP registry manifest + uvx pin 四者原子更新
  （`tests/acp/test_registry_manifest.py`、`tests/scripts/test_release_acp_registry.py`）。
- Docker 发布流水线支持 **tag 触发**（`docker-publish.yml`），release 事件按
  `:<tag>` + `:<semver>` 打 multi-arch manifest。
- 中国区镜像文档、CI 冒烟与 keyless 测试引入随依赖更新落地。

## Full Changelog

### ✨ Features
- **B1/M3**: multiplex supervisor — per-profile isolated gateway children + watchdog restart
- **B1-4/B1-5/B1-6**: single-listener front with `/p/<name>/` routing; WS auth-isolated upgrade; topology observability
- **B2/M4**: bot mode — profiles as DM-able bots (roster/liveness, budget depth cap, anti-forgery dispatch)
- **B2-4**: cross-gateway DM relay over `bot_mode.peers`
- **A3-3/M5**: keyless anonymous web-search pool + parallel/tavily/exa/firecrawl/keenable providers
- **A3-4/M5**: project skills (`.intellect/skills`) with fail-closed quarantine gate
- **PT**: pets package + `intellect pets` CLI
- **A3-5/M5**: `intellect sessions import` (Claude Code / Codex JSONL, strict contract)
- **A3-6/M5**: MCP result governance + `intellect mcp doctor`
- **M5**: deadline engine, stall guards, stream stale circuit breaker
- **W15**: progressive-disclosure tool catalog + multi-query search / batch describe

### 🐛 Bug Fixes
- **A3-1**: image-payload & tool-replay classification, cooldown routing, dedup double-billing
- **G-07 closeout**: duplicate tool-result dedup in pre-call sanitizer
- **A3-7**: locked `uv sync --frozen` update path
- **misc**: relay base-URL fix for standalone peers; hardened control-socket/status paths

### ⚡ Performance / Robustness
- **G-21**: hot-path Rust migration CLOSED — stream_consumer stays Python (0.036% CPU, benchmarked)
- per-profile isolation & backoff restart reduce blast radius of a single child crash

### ♻️ Refactoring / Governance
- **R6**: fail-closed content quarantine for trusted skills
- **A3-2**: per-model TokenAccumulator dimension + `/usage` breakdown
- version-lock now test-enforced across pyproject / Rust / ACP registry

### 📝 Documentation
- `website/docs/user-guide/`: bot-mode, multiplex-gateways, pets, web-search guides
- domestic mirror + tag-triggered docker publish notes

## Verification

```bash
# Verify version (auto-reads pyproject.toml)
python3 -c "from intellect_cli import __version__; print(__version__)"   # 0.6.8

# Rust handshake constant must match Cargo.toml
python3 -c "import intellect_community_core as c; print(c.RUST_CORE_VERSION)"

# Lockstep tests (pyproject ↔ Cargo ↔ rust handshake ↔ ACP registry)
uv run python -m pytest tests/acp/test_registry_manifest.py tests/scripts/test_release_acp_registry.py -q
```

See [CHANGELOG.md](CHANGELOG.md) for the milestone-level index.
