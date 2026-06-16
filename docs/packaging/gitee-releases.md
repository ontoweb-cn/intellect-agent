# Gitee 发布与 Native 产物

> 主仓库：`https://gitee.com/ontoweb/intellect-agent`  
> Release 页：`https://gitee.com/ontoweb/intellect-agent/releases`

## 1. 分发渠道定位

**Gitee 是 Intellect Agent 的唯一官方源码与二进制分发渠道。**

| 用途 | Gitee 路径 |
|------|------------|
| 源码 clone | `https://gitee.com/ontoweb/intellect-agent.git` |
| 分支 zip | `https://gitee.com/ontoweb/intellect-agent/archive/refs/heads/main.zip` |
| Tag zip | `https://gitee.com/ontoweb/intellect-agent/archive/refs/tags/v2026.6.16.zip` |
| Release 附件 | `https://gitee.com/ontoweb/intellect-agent/releases/download/{tag}/{filename}` |
| Issue / PR | `https://gitee.com/ontoweb/intellect-agent/issues` |

CI 可在 Gitee Go（`.gitee-ci.yml`）或外部 Runner 上执行，但**用户可见的制品必须上传到 Gitee Release**。

PyPI、Docker Hub 为**可选镜像渠道**（面向海外或 `pip install` 习惯用户），版本与 Gitee Release 对齐，不以 PyPI 为唯一来源。

### 版本号体系

| 类型 | 示例 | 用途 |
|------|------|------|
| **SemVer** | `0.6.3` | Python 包版本、`pyproject.toml`、Docker `:0.6.3` 标签 |
| **CalVer tag** | `v2026.6.16` | Git tag、Gitee Release 名称、Docker `:v2026.6.16` 标签 |
| **Rust crate** | `0.1.0` | `intellect-community-core` wheel 文件名 |

发版命令：`python scripts/release.py --bump patch --publish`（详见 [maintainer-release.md](./maintainer-release.md)）。

---

## 2. 三平台 Native 发布 — 可行性与方案

Intellect 是 **Python 进程 + PyO3 Rust 扩展**，不是单一静态二进制。  
「Native 发布」在本项目中指：**用户无需 clone 源码、无需本地 Rust 工具链，下载即用**。

### 2.1 结论摘要

| 平台 | Native 发布 | 推荐形态 | 可行性 |
|------|-------------|----------|--------|
| **Linux** | ✅ | Gitee Release tar.gz + `.deb`（P1） | 高 |
| **macOS** | ✅ | Gitee Release tar.gz / `.pkg` + Homebrew | 高 |
| **Windows** | ✅ | `install.ps1` + Gitee Release zip + Desktop `.exe` | 高（Early Beta） |
| **Docker** | ✅ | 多架构镜像（见 [docker.md](./docker.md)） | 高（Linux 容器） |

**不可行（短期）**：单个无依赖 `.exe` / AppImage 静态链接全部 Python 运行时（需 Stage 6 `intellectd` 或 PyInstaller 全量打包，维护成本极高）。

### 2.2 统一 Native 包结构（Gitee Release 附件）

每个平台发布一个 **自包含目录包**（内含 venv + 预装 wheel + 启动脚本）：

```
intellect-{platform}-{arch}-{semver}/
├── venv/                          # uv 创建的 Python 3.12 venv
│   └── .../site-packages/
│       ├── intellect_agent/...
│       └── intellect_community_core*.so|.pyd|.dylib
├── skills/                        # 符号链接或 copy bundled skills
├── bin/
│   ├── intellect                  # Linux/macOS wrapper
│   └── intellect.cmd              # Windows wrapper
├── VERSION                        # 0.6.3
└── README.txt                     # 数据目录说明 (~/.intellect)
```

**构建命令（目标态 CI）：**

```bash
./packaging/scripts/build-native-bundle.sh --platform linux --arch x86_64
./packaging/scripts/build-native-bundle.sh --platform darwin --arch universal2
./packaging/scripts/build-native-bundle.sh --platform windows --arch amd64
```

用户安装：

```bash
# Linux / macOS
curl -LO https://gitee.com/ontoweb/intellect-agent/releases/download/v2026.6.16/intellect-linux-x86_64-0.6.3.tar.gz
tar xzf intellect-linux-x86_64-0.6.3.tar.gz
./intellect-linux-x86_64-0.6.3/bin/intellect version
# 可选：ln -s .../bin/intellect ~/.local/bin/intellect
```

```powershell
# Windows
Invoke-WebRequest -Uri "https://gitee.com/ontoweb/intellect-agent/releases/download/v2026.6.16/intellect-windows-amd64-0.6.3.zip" -OutFile ia.zip
Expand-Archive ia.zip -DestinationPath $env:LOCALAPPDATA\intellect
& "$env:LOCALAPPDATA\intellect\intellect-windows-amd64-0.6.3\bin\intellect.cmd" version
```

### 2.3 各平台 Native 形态明细

#### Linux

| 形态 | 文件 | 说明 |
|------|------|------|
| **Native tar.gz** | `intellect-linux-x86_64-{semver}.tar.gz` | P0，Gitee Release |
| **Native tar.gz (arm64)** | `intellect-linux-aarch64-{semver}.tar.gz` | P0 |
| **Rust wheel 单包** | `intellect_community_core-*-manylinux_2_28_*.whl` | 供 pip / 脚本组合安装 |
| **Python wheel** | `intellect_agent-{semver}-py3-none-any.whl` | 同上 |
| **.deb** | `intellect-agent_{semver}_amd64.deb` | P1，FHS `/usr/local` 布局 |
| **AppImage** | — | P3，低优先级 |

系统依赖（仍需用户自行安装或通过 postinst 声明）：`ripgrep`、`ffmpeg`。

#### macOS

| 形态 | 文件 | 说明 |
|------|------|------|
| **Native tar.gz** | `intellect-darwin-universal2-{semver}.tar.gz` | P0，Intel + Apple Silicon |
| **Rust wheel** | `*-macosx_11_0_universal2.whl` | pip 路径 |
| **.pkg 安装器** | `intellect-agent-{semver}-universal.pkg` | P1，签名后分发 |
| **Homebrew** | `brew install intellect-agent` | 见 `packaging/homebrew/` |

#### Windows

| 形态 | 文件 | 说明 |
|------|------|------|
| **PowerShell 安装器** | `scripts/install.ps1`（远程执行） | P0，已存在 |
| **Native zip** | `intellect-windows-amd64-{semver}.zip` | P0，离线/内网 |
| **Rust wheel** | `*-win_amd64.whl` | pip 路径 |
| **Desktop .exe** | GUI 安装器 | 已存在，内部调用 `install.ps1` |

Native zip 内含 PortableGit 路径说明；或依赖安装脚本已安装的 `%LOCALAPPDATA%\intellect\git`。

---

## 3. Gitee Release 附件清单（目标态）

CalVer Release `v2026.6.16`（SemVer `0.6.3`）应包含：

### Python / Rust 包（开发者 & pip 离线安装）

```
intellect_agent-0.6.3.tar.gz
intellect_agent-0.6.3-py3-none-any.whl
intellect_community_core-0.1.0-cp312-cp312-manylinux_2_28_x86_64.whl
intellect_community_core-0.1.0-cp312-cp312-manylinux_2_28_aarch64.whl
intellect_community_core-0.1.0-cp312-cp312-macosx_11_0_universal2.whl
intellect_community_core-0.1.0-cp312-cp312-win_amd64.whl
# cp313 变体同上
```

### Native 用户包（终端用户）

```
intellect-linux-x86_64-0.6.3.tar.gz
intellect-linux-aarch64-0.6.3.tar.gz
intellect-darwin-universal2-0.6.3.tar.gz
intellect-windows-amd64-0.6.3.zip
```

### 校验

```
SHA256SUMS
SHA256SUMS.sig   # 可选 GPG 签名
```

### 下载 URL 模板

```
https://gitee.com/ontoweb/intellect-agent/releases/download/v2026.6.16/{filename}
```

`install.sh` / `install.ps1` 在检测到 `--tag v2026.6.16` 时，应优先从上述 URL 下载对应平台的 Native 包或 wheel，而非源码编译。

---

## 4. 与 PyPI / Docker 的关系

```
                    ┌─────────────────┐
                    │  Gitee Release  │  ← 权威来源（源码 tag + 全部附件）
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   pip mirror            Docker Hub           一键安装脚本
   (可选 PyPI)      ontoweb/intellect-agent    install.sh / .ps1
```

- **Gitee**：完整附件、Native 包、changelog
- **PyPI**：同步 wheel/sdist（可选，国内用户可能需镜像）
- **Docker**：从同一 Git tag 构建，打 semver + calver 标签（见 [docker.md](./docker.md)）

---

## 5. 发版流程（Gitee）

### 自动化（推荐）

1. GitHub 仓库 Secrets 配置 `GITEE_TOKEN`
2. `python scripts/release.py --bump patch --publish`（打 tag 并 push）
3. **`.github/workflows/gitee-release.yml`** 自动：
   - 构建 Python sdist/wheel
   - 4 平台矩阵：Rust wheel + Native tar.gz/zip
   - 合并 `SHA256SUMS` → 上传 Gitee Release
4. **`docker-publish.yml`** 打 `:0.6.3` 与 `:v2026.6.16` 镜像标签
5. （可选）`upload_to_pypi.yml` 同步 PyPI

本地抢先上传（仅当前平台）：`--gitee-local`

手动补救：

```bash
GITEE_TOKEN=... python packaging/scripts/publish-gitee-from-ci.py \
  --tag v2026.6.16 --dist-dir dist/combined
```

---

## 6. 实施优先级

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | CI 矩阵 + Gitee 上传 | ✅ |
| P0 | install 脚本 Gitee wheel | ✅ |
| P1 | Native 三平台 bundle | ✅ |
| P1 | Docker semver 标签 | ✅ |
| P2 | Linux `.deb`、macOS `.pkg` | 待做 |
| P3 | Gitee 容器镜像镜像 | 待做 |
