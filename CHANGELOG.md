# Changelog

All notable changes to Intellect Agent are documented in per-version release notes
(`RELEASE_vX.Y.Z.md`).  This file provides a high-level index and forward-looking
roadmap.

## Recent Releases

| Date | Highlights |
|------|------------|
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
v0.6.0 —— Rust core layer migration ← current
```

## Architecture

See [AGENTS.md](AGENTS.md) for the developer guide and project structure.
Detailed architecture decisions live in `docs/plans/`.
