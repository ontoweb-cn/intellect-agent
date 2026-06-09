# Changelog

All notable changes to Intellect Agent are documented in per-version release notes
(`RELEASE_vX.Y.Z.md`).  This file provides a high-level index and forward-looking
roadmap.

## Recent Releases

| Version | Date | Highlights |
|---------|------|------------|
| **v0.5.0** | **2026-06-10** | **Single-user refactoring + Security & Performance hardening** |
| [v0.5.1-prior](RELEASE_v0.5.1.md) | 2026-06-06 | P0-P6 Security & Platform Hardening — 18 PRs merged (pre-simplify) |
| [v0.15.1](RELEASE_v0.15.1.md) | 2026-05-29 | Bug fixes, polish |
| [v0.15.0](RELEASE_v0.15.0.md) | 2026-05-24 | Big Refactor: `run_agent.py`, Gateway platform migration, Multi-user foundation |
| [v0.14.0](RELEASE_v0.14.0.md) | 2026-05-17 | Plugin system, memory providers, context engine |
| [v0.13.0](RELEASE_v0.13.0.md) | 2026-05-10 | Multi-provider model support, streaming improvements |
| [v0.12.0](RELEASE_v0.12.0.md) | 2026-05-03 | Gateway messaging, platform adapters |
| [v0.11.0](RELEASE_v0.11.0.md) | 2026-04-26 | CLI overhaul, TUI gateway |
| [v0.10.0](RELEASE_v0.10.0.md) | 2026-04-19 | Security hardening, tool system |
| [v0.9.0](RELEASE_v0.9.0.md) | 2026-04-12 | Session management, compression |
| [v0.8.0](RELEASE_v0.8.0.md) | 2026-04-05 | Multi-modal, file operations |
| [v0.7.0](RELEASE_v0.7.0.md) | 2026-03-29 | Tool calling, function dispatch |
| [v0.6.0](RELEASE_v0.6.0.md) | 2026-03-22 | Provider adapters, model routing |
| [v0.4.0](RELEASE_v0.4.0.md) | 2026-03-08 | Storage backend, session persistence |
| [v0.3.0](RELEASE_v0.3.0.md) | 2026-03-01 | Gateway foundations, platform SDK |
| [v0.2.0](RELEASE_v0.2.0.md) | 2026-02-22 | Initial public release |

## Feature Timeline

```
v0.2.0 —— Initial release (CLI + basic agent)
v0.3.0 —— Gateway + platform SDK
v0.4.0 —— Storage backend + session persistence
v0.6.0 —— Provider adapters
v0.7.0 —— Tool calling
v0.8.0 —— Multi-modal + file ops
v0.9.0 —— Session management + compression
v0.10.0 —— Security hardening
v0.11.0 —— CLI overhaul + TUI
v0.12.0 —— Gateway messaging
v0.13.0 —— Multi-provider models
v0.14.0 —— Plugin system
v0.15.0 —— Big Refactor + Multi-user
v0.15.1 —— Bug fixes
v0.5.1-prior —— P0-P6 Security & Platform Hardening
v0.5.0 —— Single-user refactoring + Perf/Security hardening ← current
```

## Architecture

See [AGENTS.md](AGENTS.md) for the developer guide and project structure.
Detailed architecture decisions live in `docs/plans/`.
