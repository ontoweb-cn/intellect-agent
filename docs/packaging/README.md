# Intellect Agent — 跨平台打包文档

本目录描述 Intellect Agent 在 **Windows / Linux / macOS** 上的打包策略、产物清单与安装说明。

**官方分发渠道：[Gitee](https://gitee.com/ontoweb/intellect-agent)**（源码、Release 附件、Issue）。PyPI 与 Docker Hub 为可选镜像。

## 文档索引

| 文档 | 读者 | 内容 |
|------|------|------|
| [design.md](./design.md) | 维护者 / 架构师 | 打包总体设计、产物矩阵、CI 规划 |
| [gitee-releases.md](./gitee-releases.md) | 维护者 & 用户 | **Gitee 主渠道**、三平台 Native 发布方案 |
| [docker.md](./docker.md) | 用户 & 维护者 | **Docker 版本标签**、多架构、升级策略 |
| [linux.md](./linux.md) | 用户 & 维护者 | Linux 安装与 Native 包 |
| [macos.md](./macos.md) | 用户 & 维护者 | macOS 安装与 Native 包 |
| [windows.md](./windows.md) | 用户 & 维护者 | Windows 安装与 Native 包 |
| [maintainer-release.md](./maintainer-release.md) | 发布工程师 | Gitee 发版检查清单 |

## 相关资源

| 路径 | 说明 |
|------|------|
| `.github/workflows/gitee-release.yml` | **CI 全平台构建 + Gitee 上传** |
| `packaging/scripts/ci-build-platform.sh` | CI 单平台构建 |
| `packaging/scripts/publish-gitee-from-ci.py` | CI 合并上传 Gitee |
| `packaging/scripts/build-release-artifacts.sh` | Python + Rust wheel 构建 |
| `packaging/scripts/build-native-bundle.sh` | 三平台 Native 目录包构建（设计脚本） |
| `packaging/docker/docker-compose.example.yml` | Docker Compose 示例 |
| `docs/architecture/rust-python-interaction.md` | Rust ↔ Python 架构 |

## 三平台 Native 发布 — 快速结论

| 平台 | 能否 Native 发布 | 推荐方式 |
|------|------------------|----------|
| **Linux** | ✅ | Gitee Release `intellect-linux-{arch}-{semver}.tar.gz` 或 `.deb` |
| **macOS** | ✅ | Gitee Release `intellect-darwin-universal2-{semver}.tar.gz` 或 Homebrew |
| **Windows** | ✅ | `install.ps1` 或 Gitee Release `intellect-windows-amd64-{semver}.zip` |
| **Docker** | ✅（Linux 容器） | `ontoweb/intellect-agent:0.6.3` / `:v2026.6.16` |

Native 包 = 预装 venv + Rust 扩展 + 启动脚本，**无需本地 Rust 工具链**。详见 [gitee-releases.md](./gitee-releases.md)。

## Docker 版本标签

| 标签 | 含义 |
|------|------|
| `:0.6.3` | SemVer，**生产推荐** |
| `:v2026.6.16` | CalVer，对应 Gitee Release |
| `:latest` | 跟踪 main，不推荐生产 |

详见 [docker.md](./docker.md)。

## 当前状态（v0.6.3）

| 渠道 | Python | Rust | Native 包 | 说明 |
|------|--------|------|-----------|------|
| **Gitee Release CI** | ✅ | ✅ 矩阵 | ✅ 矩阵 | `gitee-release.yml` + `RELEASE_TOKEN` |
| **release.py --publish** | ✅ 本地 | ✅ 本地 | ✅ 当前平台 | CI 补全全平台；`--gitee-local` 可抢先上传 |
| Git 安装脚本 | ✅ | ✅ Gitee wheel | — | `install.sh` / `install.ps1` |
| Docker Hub | ✅ | ✅ Dockerfile | — | `:semver` + `:calver` 双标签 |
| PyPI | ✅（镜像） | 部分 | — | `upload_to_pypi.yml` |

## 快速命令（开发者）

```bash
# 构建 wheel → dist/
./packaging/scripts/build-release-artifacts.sh

# 构建当前平台 Native 目录包 → dist/native/
./packaging/scripts/build-native-bundle.sh

# 本地开发
cd rust-core && maturin develop --release && pip install -e .
```
