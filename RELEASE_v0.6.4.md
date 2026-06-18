# v0.6.4 — Gitee 原生跨平台打包

## Overview

v0.6.4 将发布流程迁移至 Gitee Releases 原生分发，实现了跨平台 CI 制品自动构建，
同时解决了 Windows 平台 WebUI 的一系列控制台窗口问题。

## Highlights

### 打包 & CI

- **Gitee Releases 原生分发**: SemVer tag (`v*`) 触发自动构建，三平台（Linux/macOS/Windows）
  CI 制品自动上传至 Gitee Releases。
- **跨平台 CI 制品**: 每个 tag 推送后自动生成对应平台的安装包，无需手动构建。
- **Homebrew 打包更新**: Rust 扩展设为可选依赖，安装器适配。

### Windows WebUI 修复

- **pythonw.exe 守护进程**: WebUI 后端改用 GUI 子系统启动（`pythonw.exe`），
  彻底消除空白控制台窗口。
- **全局子进程窗口抑制**: 所有 WebUI 子进程探测统一使用 `pythonw.exe` + `PYTHONPATH`，
  包括健康检查、重启自检等场景，不再闪现 cmd 窗口。
- **守护进程自重启**: `webui_ha.py` 看门狗在 Windows 上使用 `pythonw.exe` 实现无窗口重启。

### Rust 扩展

- **安全 fallback + NoneType 诊断**: `intellect_rust.py` 新增安全的 Rust 扩展缺失时的
  fallback 逻辑和 NoneType 返回值诊断，避免 `AttributeError: 'NoneType' object has no attribute` 崩溃。
- **自动更新同步**: `pip install --upgrade` 和 `git pull` 自动更新时同步编译 Rust 扩展。

### 开发体验

- **dev 启动脚本**: 新增开发模式快速启动脚本。
- **`.gitignore` 更新**: 补充 Rust 构建产物忽略规则。

## Changelog

### Added
- Gitee-native release workflow (SemVer tag trigger)
- Cross-platform CI artifacts (Linux, macOS, Windows)
- Dev start script
- Homebrew formula updates (Rust optional)

### Changed
- Windows WebUI: `python.exe` → `pythonw.exe` for daemon and all subprocess probes
- `intellect_rust.py`: safe fallback wrappers with NoneType diagnostics
- Rust extension: sync during `pip`/`git` auto-update
- Installers: Rust extension marked optional for packaging flexibility

### Fixed
- Windows: blank console window on WebUI launch (daemon)
- Windows: blank console window on WebUI self-restart
- Windows: console window flash on WebUI subprocess health checks
- `intellect_rust.py`: NoneType crash when Rust extension not loaded

## Test Summary

| Suite | Result |
|-------|--------|
| Rust unit tests | **88/88** |
| Python collection | **26,834 tests, 0 errors** |
| CI artifacts | **3 platforms** (Linux, macOS, Windows) |

## Upgrade

```bash
git checkout v0.6.4
pip install maturin
cd rust-core && maturin develop --release
pip install -e ".[all,dev,acp]"
```
