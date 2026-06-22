# v0.6.7 — A1 Gateway 拆分 + 发布流水线 + 版本自动化

## Overview

v0.6.7 是架构优化的里程碑版本：Gateway 单体文件从 19,808 行拆分为 5 个 Mixin + 4 个 Helper，
减至 10,098 行（-49%）。同时重建了发布流水线（冒烟测试、GPG 签名、changelog 生成、
国内镜像文档），并实现了版本号自动解析。

## Highlights

### A1: Gateway 单体拆分 (19,808→10,098, -49%)

Gateway 的核心文件 `gateway/run.py` 通过 6 个阶段系统性拆分：

| 阶段 | 内容 | 模块 | 行数 |
|------|------|------|:--:|
| Phase 1 | 模块级函数提取 (44函数) | helpers + message_helpers + config_helpers + skill_session_helpers | ~1,125 |
| Phase 2 | 命令处理器 Mixin (57方法) | command_handlers.py | 3,705 |
| Phase 3 | Agent 生命周期 Mixin (29方法) | agent_runner.py | 3,865 |
| Phase 4 | 平台处理器 Mixin (41方法) | platform_handlers.py | 1,399 |
| Phase 5-6 | 基础设施 Mixin (90方法) | infrastructure_handlers.py | 4,363 |
| TB1-TB7 | 技术债务清理 | getter 去重/shim清理/accessor统一/函数迁出 | — |

**MRO 继承链**: `GatewayRunner → CommandHandlers → AgentRunner → PlatformHandlers → InfrastructureHandlers`

**命令派发重构**: 45-branch `if/elif` 链 → `_COMMAND_DISPATCH` 注册表 dict + `getattr` 派发

### 发布流水线重建

| 改进 | 说明 |
|------|------|
| 冒烟测试 | GitHub Actions 发布后在 3 平台下载 wheel → 安装 → 验证 import |
| GPG 签名 | `Intellect Agent CI (7770F3E587EFAA74)` 对 SHA256SUMS 签名 |
| Gitee SHA256SUMS 同步 | 同步前校验完整性 |
| Changelog 生成 | `scripts/changelog.py` — 从 git 历史按类型分组生成 |
| 产物命名统一 | `intellect-agent-{ver}-{platform}-{arch}.{ext}` |
| 国内镜像 | 清华/阿里云/中科大 pip 镜像文档 + 自动同步 |

### 版本号自动化

`__version__` 不再需要手动维护两个文件。`intellect_cli/__init__.py` 自动从 `pyproject.toml` 读取：

```
优先级: pyproject.toml → importlib.metadata → git describe → "0.0.0"
```

发布时只需 bump `pyproject.toml` 一个文件。

### 性能优化

- **P5**: `get_session()` 默认跳过 `system_prompt`（可达 100K+），仅 1 个调用方显式加载
- **P7**: `_save_session_log` 首次后内存追踪，不再读磁盘
- **P10**: endpoint 模型元数据缓存 LRU eviction

### WebUI 进程组终止

POSIX 上 `intellect webui stop` 使用 `os.killpg` 杀整个进程组，防止孤儿子进程。

### 双平台 README

所有安装方式提供 GitHub + Gitee 双版本：
- Linux/macOS: `raw.githubusercontent.com` / `raw.giteeusercontent.com`
- Windows: 同上
- pip: `pypi.org` / 清华镜像 / Gitee Release 直链
- clone: `github.com` / `gitee.com`

## Full Changelog

### ✨ Features
- **A1**: split gateway/run.py monolith into 5 mixins + 4 helpers
- **CI**: add release smoke tests (3 platforms)
- **CI**: deploy GPG signing for release artifacts
- **CI**: add automated changelog generator (`scripts/changelog.py`)
- **CI**: add domestic mirror check + install docs
- **CI**: Gitee SHA256SUMS sync with integrity verification
- auto-resolve version from pyproject.toml (no more manual `__init__.py` sync)

### 🐛 Bug Fixes
- **P5**: add `include_system_prompt=True` to api_server, export_session callers
- **TODO-003**: fix pytest collection errors (syntax error + missing shims)
- **webui**: use process-group kill on POSIX to prevent orphaned children
- **A2**: add missing uuid, `_git_repo_root`, `_path_is_within_root` to worktree_helpers
- **security**: multiple npm/Python CVE mitigations

### ⚡ Performance
- **P1-P4**: startup fast path, token cache, tc normalize, estimate optimization
- **P5**: get_session() skips system_prompt (100K+) by default
- **P10**: LRU eviction for endpoint model metadata cache

### ♻️ Refactoring
- **A1-TB1~TB7**: dedup getter, clean shims, fix Rust warning, TYPE_CHECKING, accessor unification
- **A2/A3/A4**: worktree helpers, conversation helpers, platform check_requirements
- **CI**: standardize artifact naming (`intellect-agent-{ver}-{platform}-{arch}.{ext}`)

### 📝 Documentation
- **README**: dual-platform install instructions (GitHub + Gitee)
- **README**: WebUI start/restart/stop lifecycle documentation
- **VERIFY.md**: GPG verification + domestic mirror install
- **TODO-010/011**: Rust migration analysis + release mechanism analysis

## Verification

```bash
# Verify version
python3 -c "from intellect_cli import __version__; print(__version__)"

# Verify GPG signature
curl -sL https://raw.githubusercontent.com/ontoweb-cn/intellect-agent/main/gpg-public.asc | gpg --import
gpg --verify SHA256SUMS.asc SHA256SUMS
```

See [VERIFY.md](VERIFY.md) for full instructions.
