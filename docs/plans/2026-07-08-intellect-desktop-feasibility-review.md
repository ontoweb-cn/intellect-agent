# Intellect Agent Desktop 可行性调查 — 技术评审版

> 文档日期：2026-07-08
> 状态：✅ 已评审（含事实更正）
> 关联：`2026-07-08-hermes-v0.16-v0.18-port-design.md`（Hermes 功能移植，不含 Desktop）
> 目的：分析 Hermes Desktop 方案，评估 intellect-agent 构建 Desktop 客户端的可能性；
> **更正** 前次调查中「Intellect Desktop Windows 已有」的错误结论。

---

## 执行摘要

1. **事实更正**：intellect-agent **本仓库内没有已发布的官方 Desktop 客户端**（无 `apps/desktop/`、无 Electron/Tauri 工程、无 Desktop 安装包构建流水线）。部分用户文档与 `docs/packaging/gitee-releases.md` 所称「Desktop `.exe` 已存在」**与仓库事实不符**——Windows 侧实际分发的是 **`scripts/install.ps1` 远程安装** 与 **Native bundle + NSIS 安装器**（`Intellect-Agent-*-Setup.exe`，安装 CLI/agent 包，非聊天 GUI）。
2. **第三方项目**：社区成员 @itsdodo21 曾展示过名为「Intellect Desktop v0.4.0」的 **macOS SwiftUI 客户端**（SSH 连远程 host），属**社区 side project**，非 ONTOWEB 官方产品，与本仓库无代码关联。
3. **Hermes Desktop** 是 ~160k LOC React + ~17k LOC Electron main 的独立产品线，依赖 `hermes serve`（WebSocket JSON-RPC + 大量 REST），**不是** TUI 套壳。
4. **Intellect 现状**：无 Desktop，但有成熟的 **WebUI**（`webui/`，300+ 文件）与 **TUI + `tui_gateway` WS**；最接近「桌面体验」的路径是 **WebView 壳包装 WebUI** 或 **长期自建 Electron 对标 Hermes**。
5. **评审结论**：构建 Intellect Desktop **技术上可行**，但 Hermes 级全栈 Desktop 为 **6–12 个月级独立产品线**；短期现实方案是 WebUI 壳 + 后端统一入口（`intellect serve`），并清理文档中的 Desktop 误导表述。

---

## 一、事实更正：「Intellect Desktop Windows 已有」

### 1.1 前次调查的错误

前次可行性分析将以下表述当作已落地事实：

- 「Windows 已有外部 Intellect Desktop（Electron 壳 spawn `intellect.exe`）」
- 「路径 B：扩展现有 Intellect Desktop」
- 「`docs/packaging/gitee-releases.md` 列出 Desktop `.exe` 已存在」

**经代码与制品清单核查，上述结论不成立。**

### 1.2 仓库内实际存在什么

| 声称 | 核查结果 | 证据 |
|------|----------|------|
| `apps/desktop/` 或 Electron 工程 | **不存在** | 全仓 `Glob **/apps/**` → 0 文件 |
| Desktop 专用 npm / electron-builder | **不存在** | 无 `package.json` desktop 脚本；无 electron-builder 配置 |
| Gitee Release 附件含 Desktop GUI `.exe` | **清单中无此项** | `packaging/manifests/artifacts.yaml` 仅列 native zip/tar.gz/wheel，**无** Desktop GUI 产物 |
| Windows NSIS 安装器 | **存在，但是 CLI/agent 包** | `packaging/installer/windows/intellect-agent.nsi` → 输出 `Intellect-Agent-{version}-Setup.exe`，安装 native bundle 到 `%LOCALAPPDATA%\IntellectAgent`，**非** Hermes 式聊天 Desktop |
| Windows 安装主路径 | **PowerShell** | `scripts/install.ps1`（`irm \| iex`） |
| WebUI | **存在** | `webui/` + `intellect webui start`（默认 `127.0.0.1:9119`） |
| TUI + WS gateway | **存在** | `ui-tui/` + `tui_gateway/ws.py` |

### 1.3 文档与代码中的「Desktop」指什么

多处出现 “Intellect Desktop” 或 “desktop GUI”，含义**不一致**，是误导来源：

| 来源 | 实际含义 | 是否 = 已发布 Desktop 客户端 |
|------|----------|------------------------------|
| `website/docs/getting-started/installation.md` L55 | 「下载 Intellect Desktop，运行 `.exe`」 | ❌ **无下载链接、无构建物**；与 NSIS/PS1 安装混淆 |
| `website/docs/user-guide/windows-native.md` §Desktop installer | 同上 | ❌ |
| `docs/packaging/gitee-releases.md` L124 | 「Desktop `.exe` GUI 安装器 \| **已存在**」 | ❌ 与 `artifacts.yaml` 矛盾；NSIS 是 **Agent Setup**，非 Desktop GUI |
| `scripts/install.ps1` 注释 | 「desktop GUI's onboarding wizard」作为 **Stage 协议的未来调用方** | ⏸️ **前瞻性设计**，非已交付产品 |
| `intellect_cli/main.py` L8873 | `intellect update` 并发检测注释中的「Intellect Desktop Electron app」 | ⏸️ **防御性注释**（预留 Windows 文件锁场景），非产品声明 |
| `webui/static/style.css` L3117 | 「Matches intellect-desktop LLM Providers panel」 | ⏸️ **UI 对齐目标**（未来 Desktop 视觉规范），非现有应用 |
| `website/src/data/userStories.json` | @itsdodo21 社区项目「Intellect Desktop v0.4.0」SwiftUI Mac | ⚠️ **第三方社区展示**，非官方发行版 |

### 1.4 更正后的 Intellect「客户端」清单

| 客户端 | 状态 | 平台 |
|--------|------|------|
| CLI（`intellect`） | ✅ 官方，本仓库 | 全平台 |
| TUI（`intellect --tui`） | ✅ 官方，本仓库 | 全平台 |
| WebUI（`intellect webui`） | ✅ 官方，本仓库 | 浏览器 / 可 WebView 嵌入 |
| Gateway 消息面 | ✅ 官方 | 各平台 adapter |
| ACP（`intellect acp`） | ✅ 官方 | 编辑器集成 |
| **Intellect Desktop（GUI）** | ❌ **未在本仓库交付** | — |
| 社区 SwiftUI「Intellect Desktop」 | ⚠️ 第三方，非本仓 | macOS（SSH 客户端） |

---

## 二、Hermes Desktop 方案（评审确认）

> 源码基线：`/Users/simon/workspace/hermes-agent/apps/desktop/`（v0.17.0 量级）
> 用户文档：`hermes-agent/website/docs/user-guide/desktop.md`

### 2.1 定位

Hermes Desktop 与 CLI/TUI/Gateway **共用同一 agent 核心**（同一 `HERMES_HOME`、会话、技能、记忆），是**独立原生客户端**，不是浏览器里打开 dashboard 的简单包装。

### 2.2 技术栈（已核实）

| 层 | 技术 | 规模（约） |
|----|------|------------|
| 壳 | Electron 40 | `electron/main.cjs` ~17k LOC |
| 渲染 | React 19 + Vite 8 + Tailwind 4 + nanostores | TS/TSX ~160k LOC |
| 共享 RPC 客户端 | `@hermes/shared` → `JsonRpcGatewayClient` | `apps/shared/` |
| 原生 | `node-pty`、git IPC、系统通知、OAuth（Electron `net`） | 打包 extraResources |
| 测试 | Vitest + electron 单测 + `scripts/test-desktop.mjs` | 覆盖广 |

### 2.3 进程模型（评审重点：与 TUI 根本不同）

```text
Electron main
  ├─ 按 profile  spawn `hermes serve --host 127.0.0.1 --port 0`
  ├─ backend pool + bootstrap + 自更新
  ├─ IPC 代理 REST → http://127.0.0.1:<port>/api/*
  └─ git / pty / FS / OAuth / 多窗口

React renderer
  ├─ WebSocket JSON-RPC → /api/ws（聊天、工具流、审批、projects.*）
  └─ REST via preload IPC（设置、技能、cron、Star Map、Command Center…）
```

**评审意见（确认有效）**：

- TUI 默认 **stdio NDJSON** 直连 `tui_gateway`；Desktop **不**走 stdio。
- Desktop 依赖 **双通道**：WS RPC + **大量 REST**（`src/hermes.ts` 中 40+ `/api/*` 路径）。
- `hermes serve` = headless **统一后端**（`tui_gateway` WS + `hermes_cli/web_server.py` REST），这是 Desktop 的硬前提。

### 2.4 功能分层（Desktop 专属 vs 共享）

| 能力 | Desktop | TUI/CLI | 评审备注 |
|------|---------|---------|----------|
| 流式聊天 + 工具 | ✅ React | ✅ Ink | 共用 `tui_gateway` 事件 |
| **Projects**（多根工作区） | ✅ | ❌ | `projects.*` RPC + `projects_db` |
| **Review 面板**（git stage/commit/PR） | ✅ | ❌ | Electron git IPC |
| **Git worktree「Start work」** | ✅ | ❌ | main 进程 |
| **终端 pane**（xterm + pty） | ✅ | ❌ | `node-pty` |
| **原生通知**（含审批动作） | ✅ | ❌ | Electron |
| **VS Code 主题导入** | ✅ | ❌ | 主题引擎 + marketplace |
| **子代理观察 OS 窗口** | ✅ | overlay only | 多窗口 |
| **Star Map** | ✅ D3 | ✅ 文本 Journey | REST + RPC |
| Command Center / Settings GUI | ✅ | CLI setup | REST 重 |
| 远程后端 | ✅ OAuth/basic | WS attach | 连远程 `hermes serve` |

v0.17–v0.18 Desktop 深化（快捷键、composer 模型选择、Projects、终端 pane 等）是 Hermes **产品差异化主战场**。

### 2.5 分发

- `electron-builder`：macOS DMG（公证 `scripts/notarize.cjs`）、Windows NSIS/MSI、Linux AppImage/deb/rpm
- `hermes desktop` CLI 命令构建并启动
- 首次启动可 bootstrap Python venv + 工具链到 `HERMES_HOME`

**评审**：Hermes Desktop 是**完整产品工程**（~830 文件目录），不是 agent 核心的附属脚本。

---

## 三、Intellect Agent 现有 UI 面（评审 + 更正）

### 3.1 WebUI — 最强、已成型（非「无 dashboard」）

| 项 | 事实 |
|----|------|
| 服务端 | Python 标准库 `ThreadingHTTPServer`（`webui/server.py`） |
| 前端 | 原生 JS SPA（**非 React**），`webui/static/` |
| 启动 | `intellect webui start` → 默认 `127.0.0.1:9119` |
| 实时 | **SSE**（`/api/chat/stream`），非 WebSocket |
| 路由 | `webui/api/routes.py` 单文件路由中枢（~650KB） |
| 能力 | 会话、流式聊天、Goals、xterm 终端、工作区/文件浏览、**git worktree API**、Kanban、Cron、Skills、Memory、Profiles、审批、PWA |

架构文档：`docs/webui/ARCHITECTURE.md`

**评审修正**：intellect **有** Web 仪表盘（WebUI），与 Hermes 的 `hermes dashboard` 同类；**缺的是 Hermes 级 Electron Desktop**，不是缺 Web 管理面。

### 3.2 TUI + `tui_gateway`

```text
intellect --tui
  Node (Ink)  ◄──stdio NDJSON──►  Python tui_gateway
```

- RPC 方法约 **70 个**（`tui_gateway/server.py` `@method` 注册）
- **`tui_gateway/ws.py`**：同一套 RPC 的 **WebSocket** 传输（注释：面向 iOS/web 客户端）
- **无** Hermes Desktop 使用的 `projects.*` RPC（全仓 `tui_gateway` grep 无匹配）

### 3.3 ACP

- `intellect acp`：VS Code / Zed / JetBrains 的 Agent Client Protocol
- **编辑器侧car**，不能替代 Desktop 的设置/多面板/项目管理

### 3.4 Members / Projects（与 Hermes Desktop Projects 不对齐）

| | Intellect WebUI「Projects」 | Hermes Desktop Projects |
|--|----------------------------|-------------------------|
| 语义 | 会话 **文件夹标签**（sidecar JSON） | **多根目录工作区** + git worktree 集成 |
| API | `webui/api/models.py` `load_projects` | `projects.*` RPC + `projects_db` |
| P6 成员项目 | `AGENTS.md` 描述完整；**本 checkout 部分 agent 模块为 stub** | N/A |

WebUI 另有 `api/worktrees.py`，但**未**形成 Hermes 式 Project 产品面。

### 3.5 分发（今日）

| 渠道 | 产物 |
|------|------|
| Gitee / git | `install.sh` / `install.ps1` |
| Native bundle | tar.gz / zip + 可选 NSIS `Intellect-Agent-*-Setup.exe` |
| Docker / Nix | 容器与 flake |
| PyPI 镜像 | `intellect-agent` + `intellect-community-core` wheel |
| **Desktop GUI 安装包** | **无** |

---

## 四、技术评审：前次调查的对错清单

### 4.1 确认正确的结论

| 结论 | 评审 |
|------|------|
| Hermes Desktop ≠ TUI 套壳 | ✅ 正确 |
| 双传输 WS RPC + REST | ✅ 正确 |
| Intellect 无 in-tree `apps/desktop/` | ✅ 正确 |
| WebUI 功能面已相当丰富 | ✅ 正确 |
| `tui_gateway/ws.py` 可复用为 native 客户端后端 | ✅ 正确（但 RPC 面少于 Hermes + 无 projects.*） |
| 直接 port Hermes Desktop UI 不现实 | ✅ 正确（API/品牌/RPC 不对齐） |
| Hermes Desktop 专属功能不应纳入 v0.16–v0.18 后端移植 P0/P1 | ✅ 与 port-design 一致 |

### 4.2 需更正的结论

| 前次结论 | 更正 |
|----------|------|
| 「Windows 已有 Intellect Desktop Electron 壳」 | ❌ **错误**。无官方 Electron 产品；文档与注释为计划/混淆/社区项目 |
| 「路径 B：扩展现有 Intellect Desktop」 | ❌ **无现成产品可扩展**；若做 Desktop 需 **从零或 WebUI 壳起步** |
| 「Gitee 已发布 Desktop `.exe`」 | ❌ **artifacts 清单无此项**；NSIS 为 Agent CLI 安装器 |
| 「外部 Desktop 与 CLI 共享目录已验证模式」 | ⚠️ **模式合理但产品未交付**；`install.ps1` Stage 协议仅为未来 GUI 预留 |
| 「P6 projects 可直接支撑 Desktop Projects」 | ⚠️ **过度乐观**；WebUI projects 语义不同，且 membership 在本树可能 stub |

### 4.3 评审新增风险项

1. **文档债务**：installation / windows-native / gitee-releases 三处 Desktop 表述需修订，避免用户寻找不存在的下载物。
2. **`intellect update` 注释**：引用不存在的 Desktop 进程，可能增加用户困惑（可改为泛化「其他 intellect.exe 子进程」）。
3. **WebUI CDN 依赖**：xterm/Prism 等来自 jsdelivr——Desktop 离线包需 vendoring。
4. **协议分裂**：WebUI 走 SSE+REST；TUI 走 JSON-RPC；Hermes Desktop 走 WS RPC+REST。Intellect 若做 Desktop 须先选 **统一后端入口**，否则长期维护三套。
5. **Rust 主线**：新 Desktop 不应倒逼 agent loop Rust 迁移；壳与 I/O 层保持 Python/Node，与 `docs/architecture/rust-python-interaction.md` 边界一致。

---

## 五、差距矩阵（评审后）

| 维度 | Hermes Desktop | Intellect 现状 | 差距 |
|------|----------------|----------------|------|
| 官方 GUI 客户端 | ✅ Electron 三平台 | ❌ **无** | 全缺 |
| Headless `serve` | ✅ `hermes serve` | ❌ 仅 `webui start` + 独立 `tui_gateway` WS | 缺统一 serve |
| 聊天协议 | WS JSON-RPC | WebUI: SSE；TUI: stdio RPC | 不统一 |
| REST 管理面 | 40+ `/api/*` 服务 Desktop | WebUI 自有 `/api/*`（体量大但路径不同） | 需映射或合并 |
| Projects（多根工作区） | ✅ | ❌（会话文件夹 only） | 产品与 API 双缺 |
| Review / git 工作流 UI | ✅ Electron IPC | WebUI 有 git API，无 Review 面板 | 中 |
| 终端 pane | node-pty | WebUI xterm SSE | WebView 可部分替代 |
| 原生通知 / 多窗口 | ✅ | PWA 有限 | 需壳 |
| Star Map / Journey | ✅ | 规划 HP-401，未实现 | 待建 |
| 打包签名 | electron-builder + 公证 | NSIS/zip only | 缺 GUI 打包链 |

---

## 六、构建 Intellect Desktop 的路径（评审修订）

### 路径 A：WebView 壳 + WebUI（推荐 MVP，2–4 周）

```text
Electron / Tauri 薄壳
  → 管理 `intellect webui` 生命周期
  → WebView → http://127.0.0.1:9119
  → 追加：托盘、单实例、原生通知、离线静态资源
```

| 优点 | 缺点 |
|------|------|
| 复用 WebUI 全部面板 | 非 Hermes 级 UX |
| 不依赖不存在的「现有 Desktop」 | SSE 在 WebView 可用但不如 WS 灵活 |
| 与 Rust/Python 边界无冲突 | 需 vendoring CDN |

**评审**：这是**当前唯一可快速交付**的「可安装桌面版」路径。

### ~~路径 B：扩展现有 Intellect Desktop~~ → **已作废**

前次推荐的「路径 B」基于错误前提。**不存在**可扩展的官方 Desktop 代码库。社区 SwiftUI 项目架构不同（SSH 客户端），**不能**作为 ONTOWEB 产品基线，最多作 UX 参考。

### 路径 C：Hermes 级全栈 Desktop（6–12 个月+）

新建 `apps/desktop/` + `intellect serve`（WS RPC + REST 合并）+ React 前端 + Electron main（git/pty/OAuth/打包）。

| 前置 | 说明 |
|------|------|
| `intellect serve` | 合并 `tui_gateway` WS 与 WebUI REST 子集 |
| `projects.*` RPC | 或启用/重做 P6 多根工作区 |
| 前端 | ~100k+ LOC 量级（可小于 Hermes 160k，但仍是产品线） |
| CI | electron-builder + 三平台签名 |

**评审**：仅在有独立前端人力与产品 commitment 时启动。

### 路径 D：Native UI on `tui_gateway` WS

SwiftUI / RN 直连 ~70 RPC 方法。

**评审**：REST 管理能力不足，需大量补 RPC 或并行 REST；适合 **macOS/iOS 一条线**，不适合短期 Windows。

### 路径 E：社区 SwiftUI 路线（非官方）

SSH 连远程 intellect host——与 Hermes「本地 spawn serve」模型不同；适合**远程开发机**场景，非本地一体化 Desktop。

---

## 七、评审建议与优先级

### 7.1 产品定位（需决策）

| 选项 | 含义 | 工期量级 |
|------|------|----------|
| **A. 安装版 WebUI** | 「Intellect 桌面版 = 带壳的 WebUI」 | 周 |
| **B. 增强 WebUI + 可选壳** | 先补 Journey/Review，再壳 | 月 |
| **C. 对标 Hermes Desktop** | 独立 Electron 产品线 | 年 |

**评审推荐**：**A → B**，除非明确资源做 C。

### 7.2 与 Hermes 移植计划（HP-*）关系

- Desktop **不是** HP 移植范围；后端能力（MoA、委派、验证、蓝图）在 WebUI/CLI/TUI 均可受益。
- 若做 Desktop 壳，**优先受益**：HP-401 `/journey`（可视化）、HP-303 验证证据（Review 工作流）、WebUI git 面板增强。

### 7.3 文档修正清单（建议单独 PR）

| 文件 | 问题 | 建议 |
|------|------|------|
| `website/docs/getting-started/installation.md` | 声称可下载 Intellect Desktop `.exe` | 改为 NSIS/PS1 安装说明；Desktop GUI 标为 **planned / not shipped** |
| `website/docs/user-guide/windows-native.md` §Desktop installer | 同上 | 区分 **Agent Setup.exe** vs **Desktop GUI** |
| `docs/packaging/gitee-releases.md` L124 | 「Desktop `.exe` 已存在」 | 改为 NSIS `Intellect-Agent-*-Setup.exe` 或删除 Desktop 行 |
| `intellect_cli/main.py` update 提示 | 引用 Intellect Desktop | 泛化为「其他 intellect 子进程」 |
| `webui/static/style.css` 注释 | 「intellect-desktop」 | 注明为 future design target |

### 7.4 技术前置（若启动路径 A/C）

1. **`intellect serve`（中期）**：统一 WS + HTTP，供壳、远程客户端、TUI attach 共用。
2. **WebUI 离线化**：静态资源与关键 JS 不依赖 CDN。
3. **单实例与端口管理**：壳与 `intellect webui` 生命周期（启动、升级、崩溃恢复）。
4. **Windows 签名**：SmartScreen 友好（NSIS 已有先例，Electron 另需配置）。

---

## 八、开放问题

| # | 问题 | 影响 |
|---|------|------|
| Q1 | 产品是否要 **Hermes  parity** 还是 **WebUI 安装版** 即可？ | 决定路径 A vs C |
| Q2 | 是否投资 **`intellect serve`** 统一后端？ | 影响 TUI attach、Desktop、远程 |
| Q3 | Projects 语义：会话文件夹 vs 多根工作区？ | 是否重做 projects.* |
| Q4 | 社区 SwiftUI 项目是否官方采纳/合作？ | 避免与路径 C 重复 |
| Q5 | 文档修正是否随 Desktop 调研一并合并？ | 用户信任 |

---

## 附录 A：关键路径索引

| 用途 | Hermes | Intellect |
|------|--------|-----------|
| Desktop 应用 | `hermes-agent/apps/desktop/` | **（无）** |
| Desktop 文档 | `website/docs/user-guide/desktop.md` | **（无等价物）** |
| Headless serve | `hermes serve` / `hermes_cli/subcommands/dashboard.py` | `intellect webui start` / `webui/server.py` |
| JSON-RPC | `tui_gateway/server.py` + WS | `tui_gateway/server.py` + `ws.py` |
| Web 管理面 | `hermes_cli/web_server.py` | `webui/api/routes.py` |
| Windows 安装 | Hermes installer + `hermes desktop` | `scripts/install.ps1` + `packaging/installer/windows/*.nsi` |
| 制品清单 | Hermes release | `packaging/manifests/artifacts.yaml` |
| Rust/Python 边界 | Python agent + 部分 Rust | `docs/architecture/rust-python-interaction.md` |

## 附录 B：评审签字

| 项 | 结论 |
|----|------|
| 前次「Intellect Desktop Windows 已有」 | **驳回** — 无官方产品证据 |
| Hermes Desktop 架构描述 | **通过** — 与源码一致 |
| Intellect WebUI/TUI 描述 | **通过** — 补充「有 WebUI、无 Desktop」 |
| 路径 B（扩展已有 Desktop） | **作废** |
| 推荐短期路径 | **路径 A（WebView + WebUI）** |
| 文档债务 | **需 follow-up PR** |
