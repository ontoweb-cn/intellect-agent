# v0.6.5 — WebUI 配置统一 + Vault/Quartz 修复 + 技术债务清理

## Overview

v0.6.5 统一了 WebUI 配置路径、修复了 Vault/Quartz 文档站的多项构建与渲染问题，
并完成了 Rust 迁移遗留的技术债务清理（T1-T4）。

## Highlights

### WebUI 配置统一

- **配置路径跨平台统一**: WebUI 配置路径从平台相关路径统一为 `~/.intellect`，
  与 CLI/Gateway 保持一致，消除多份配置不同步的问题。
- **CSRF 豁免修复**: Onboarding 端点加入 CSRF 豁免列表，解决首次设置时的 403 错误。
- **语言选择器排序**: 英文、简体中文、繁体中文优先显示。

### Vault/Quartz 文档站修复

- **SPA 模式禁用**: 修复 SPA 模式下路由回退导致 404 的问题。
- **亮色主题统一**: 强制亮色主题，解决 Quartz 暗色/亮色模式不一致的白底问题。
- **静态文件路由**: 修复无 scope prefix 的静态文件 404 错误（`vault routing 404`）。
- **Quartz 构建修复**: 锁定 Quartz clone 至 v4 分支，解决最新 main 分支构建失败；
  使用 `git clone` 代替 npm 安装 Quartz；Windows/POSIX 构建脚本补充。
- **后处理流水线**: 构建后自动修复 `.html` 扩展名链接和主题注入。

### 技术债务清理 (T1-T4)

| 项目 | 内容 |
|------|------|
| **T1** | `rust-core/README.md`: 修正依赖引用 (`fancy-regex`→`regex`), 补充 `hex`/`pbkdf2`, 行数 ~3,170→~4,500 |
| **T2** | CI parity 测试: 新增 32 个覆盖 Crypto/Sandbox/Usage/Gateway/Stream 的 parity 测试 (17→49), CI 最低断言 ≥45 |
| **T3** | `pyproject.toml`: Rust 扩展注释从 "optional" 修正为 "required since v0.6.2" |
| **T4** | 死代码移除: `intellect_rust.py` ~65 行 fallback wrappers, `agent/agent_init.py` try/except 吞异常修复, 3 处过期注释 |

### 文档

- **域名重命名**: `intellect-agent.ontoweb.cn` → `intellect.ontoweb.cn`（URL 同步更新）
- **README 更新**: 新增安装方式说明和 Rust 扩展状态描述
- **移除过时引用**: 全局清除 "Rust optional" 和 "pure-Python fallback" 描述

### 修复

- **`agent/webui_ha.py`**: 补充缺失的 WebUI 高可用模块
- **`_platform_default_intellect_home`**: 修复意外删除的 `def` 行

## Changelog

### Added
- Vault/Quartz build scripts for Windows and POSIX
- Quartz dependency pre-install during setup
- Post-processing pipeline for static/vault bundle (links + theme)

### Changed
- WebUI config path: platform-dependent → `~/.intellect` (all platforms)
- Language selector: EN, ZH-CN, ZH-TW first
- Domain rename: `intellect-agent.ontoweb.cn` → `intellect.ontoweb.cn`
- Quartz install: npm → `git clone` (v4 branch)
- Rust extension comment: "optional" → "required since v0.6.2"

### Fixed
- Vault routing 404 for static files without scope prefix
- SPA mode disabled (route fallback causing 404s)
- Quartz light/dark mode unified to pure white (#ffffff)
- Quartz build failure on latest main (pinned to v4)
- Missing `agent/webui_ha.py` module
- Accidental deletion of `_platform_default_intellect_home` def line
- Onboarding endpoints CSRF exemption
- Links with `.html` extension in post-processing

### Removed
- `intellect_rust.py`: ~65 lines of dead Python fallback wrappers
- `agent/agent_init.py`: try/except that swallowed `RuntimeError`
- Outdated "Rust optional" / "pure-Python fallback" references across all docs

## Test Summary

| Suite | Result |
|-------|--------|
| Rust unit tests | **88/88** |
| Rust parity tests (Python) | **17→49** (+32 new: Crypto, Sandbox, Usage, Gateway, Stream) |
| CI minimum assertion | **≥45 parity tests** |
| Python collection | **26,834 tests, 0 errors** |

## Upgrade

```bash
git checkout v0.6.5
pip install maturin
cd rust-core && maturin develop --release
pip install -e ".[all,dev,acp]"
```
