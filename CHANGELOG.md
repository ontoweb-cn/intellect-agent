# Changelog

All notable changes to Intellect Agent are documented in per-version release notes
(`RELEASE_vX.Y.Z.md`).  This file provides a high-level index and forward-looking
roadmap.

## Recent Releases

| Date | Highlights |
|------|------------|
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
|                | 11 个 Rust 源文件 (~3,170 行), 20+ Python 集成点 |
|                | Stage 1: SQLiteBackend + 10 写操作 |
|                | Stage 2: 命令安全沙箱 (59 正则) |
|                | Stage 3: TokenAccumulator, StreamAccumulator, normalize_usage |
|                | Stage 4: Gateway session key + 限流 |
|                | Stage 5: PKCE + Fernet + 安全随机数 |

## Feature Timeline

```
v0.5.0 —— Single-user refactoring + Perf/Security hardening
v0.6.0 —— Rust core layer migration
v0.6.2 —— Rust-only mandatory + sandbox/gateway fixes ← current
```

## Architecture

See [AGENTS.md](AGENTS.md) for the developer guide and project structure.
Detailed architecture decisions live in `docs/plans/`.
