# Changelog

All notable changes to Intellect Agent are documented in per-version release notes
(`RELEASE_vX.Y.Z.md`).  This file provides a high-level index and forward-looking
roadmap.

## Recent Releases

| Date | Highlights |
|------|------------|
| **2026-06-22** | **v0.6.7 — A1 Gateway 拆分 + 发布流水线 + 版本自动化** |
|                | A1: gateway/run.py 19,808→10,098 (-49%), 5 Mixin + 4 Helper, 注册表派发 |
|                | CI: 冒烟测试, GPG 签名, changelog 生成, 产物命名统一, 国内镜像文档 |
|                | 版本: pyproject.toml 单一来源, `__init__.py` 自动解析 |
|                | 性能: P5 get_session() 跳过 system_prompt, P10 LRU eviction |
|                | 安全: WebUI 进程组终止, 多项 CVE 缓解 |
| **2026-06-19** | **v0.6.6 — Rust 模块迁移 (M5-M9) + 统一 Rust 读写 + CI/CD 发布** |
|                | Rust: prompt builder, dispatch, guardrails, caching 统一切换 |
|                | CI: Windows 安装脚本兼容, 独立 Gitee 发布流水线, 双源 wheel 下载 |
|                | 修复: 死锁消除, Windows 兼容, 多平台构建修复 |
| **2026-06-18** | **v0.6.5 — WebUI 配置统一 + Vault/Quartz 修复 + 技术债务清理** |
|                | WebUI: 配置路径统一至 `~/.intellect` (全平台), CSRF 豁免修复 |
|                | Vault/Quartz: SPA 模式修复, 亮色主题统一, 静态文件路由, Quartz v4 构建 |
|                | 清理: T1-T4 Rust 迁移技术债务 (死代码移除 ~112 行, 注释/文档修正) |
|                | 测试: Rust parity 测试 17→49, CI 最低断言保障 |
|                | 文档: README 安装方式更新, 域名重命名, 移除 "Rust optional" 引用 |
| **2026-06-17** | **v0.6.4 — Gitee 原生跨平台打包** |
|                | 打包: Gitee Releases 原生分发, 跨平台 CI 制品 (SemVer tag 触发) |
|                | Windows: pythonw.exe 守护进程, 全局子进程控制台窗口抑制 |
|                | Rust: 安全 fallback + NoneType 诊断, pip/git 自动更新时同步扩展 |
|                | 开发: dev 启动脚本, Homebrew 打包更新 |
| **2026-06-14** | **v0.6.3 — 沙箱架构升级 + AST 双层防御 + ReDoS 消除** |
|                | 架构: RegexSet O(n) DFA + Python AST 双层 (7 类检测 + auto-deny) |
|                | 安全: 31 token 独立描述, 渗透 0 bypasses, dangerous import 检测 |
|                | 性能: fancy-regex→regex (ReDoS 免疫), 匹配 O(n×m)→O(n) |
|                | 清理: 31 孤儿测试 + Python DANGEROUS_PATTERNS 死代码 (~110行) |
|                | 修复: open() 多字符模式绕过, getattr 混淆, acp 包冲突 |
|                | 测试: Rust 88/88, Python 26,834/0 errors, acp 294/0 |
| **2026-06-13** | **v0.6.2 — Rust-Only 强制 + 沙箱/Gateway 修复** |
|                | ⚠️ Breaking: 移除 Python fallback，Rust 扩展变为强制依赖 |
|                | Rust 新增: `PlatformRetryScheduler` (gateway), `check_session_expiry_batch_rs`, `backoff_delay_batch_rs`, `is_ip_blocked_rs` (SSRF), `normalize_model_name_rs` |
|                | Sandbox: `python -c` 正则收紧（要求危险函数调用才拦截）+ docker compose / sudo 组合检测 |
|                | Gateway: `test_batch_expiry_mixed` 测试数据修正（`now: 600 → 1500`） |
|                | 测试: Rust 83/83 ✅，Python 31 个孤儿测试待清理（详见 `TODO.md`） |
| **2026-06-10** | **v0.6.1 — WebUI 控制台 + Rust 完善** |
|                | WebUI: 浏览器端会话管理仪表盘 (53 API 模块 + SPA 前端) |
|                | Rust: Stage 5c JWT, StreamAccumulator JSON 修复, 编译警告清理 |
|                | 文档: WebUI 用户指南 + 架构设计, 2 份记忆方案设计文档 |
| **2026-06-10** | **v0.6.0 — Rust 核心层迁移** |
|                | 11 个 Rust 源文件 (~3,170 行，现已增长至 ~4,528 行), 20+ Python 集成点 |
|                | Stage 1: SQLiteBackend + 10 写操作 |
|                | Stage 2: 命令安全沙箱 (59 正则) |
|                | Stage 3: TokenAccumulator, StreamAccumulator, normalize_usage |
|                | Stage 4: Gateway session key + 限流 |
|                | Stage 5: PKCE + Fernet + 安全随机数 |

## Feature Timeline

```
v0.5.0 —— Single-user refactoring + Perf/Security hardening
v0.6.0 —— Rust core layer migration
v0.6.2 —— Rust-only mandatory + sandbox/gateway fixes
v0.6.4 —— Gitee-native cross-platform packaging
v0.6.5 —— WebUI config unification + vault/quartz fixes + tech-debt cleanup ← current
```

## Architecture

See [AGENTS.md](AGENTS.md) for the developer guide and project structure.
Detailed architecture decisions live in `docs/plans/`.
