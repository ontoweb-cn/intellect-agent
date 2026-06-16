# Linux 打包与安装

> 适用：Ubuntu / Debian / Fedora / Arch 等 glibc Linux，以及 WSL2。  
> **官方渠道：[Gitee Release](https://gitee.com/ontoweb/intellect-agent/releases)**

## 推荐安装方式

### 方式 A：Gitee Native 包（目标态，推荐）

无需 Rust 工具链，下载预构建自包含包：

```bash
TAG=v2026.6.16
VER=0.6.3
ARCH=$(uname -m)   # x86_64 或 aarch64
curl -LO "https://gitee.com/ontoweb/intellect-agent/releases/download/${TAG}/intellect-linux-${ARCH}-${VER}.tar.gz"
tar xzf "intellect-linux-${ARCH}-${VER}.tar.gz"
./intellect-linux-${ARCH}-${VER}/bin/intellect version
# 可选：ln -s $(pwd)/intellect-linux-${ARCH}-${VER}/bin/intellect ~/.local/bin/intellect
```

详见 [gitee-releases.md](./gitee-releases.md)。

### 方式 B：Gitee 一键 Git 安装

```bash
curl -fsSL https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
```

### 方式 C：Docker（版本化）

```bash
docker pull ontoweb/intellect-agent:0.6.3
docker run -it --rm -v ~/.intellect:/home/intellect/.intellect \
  ontoweb/intellect-agent:0.6.3 intellect chat
```

Docker 标签说明见 [docker.md](./docker.md)。

---

## 系统依赖

| 组件 | 用途 | 安装示例 (Debian/Ubuntu) |
|------|------|--------------------------|
| Python ≥ 3.12 | 运行时 | `apt install python3.12 python3.12-venv` |
| ripgrep (`rg`) | `search_files` 工具 | `apt install ripgrep` |
| ffmpeg | TTS 语音消息 | `apt install ffmpeg` |
| Node.js ≥ 22 | TUI / browser 工具 | 见 [NodeSource](https://nodejs.org/) 或 nvm |
| git | clone / 技能 | `apt install git` |

**从源码编译 Rust 扩展时额外需要：**

```bash
sudo apt install build-essential python3-dev libffi-dev pkg-config
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
pip install maturin
```

---

## 开发者 / 维护者：本地打包

### 构建当前平台产物

```bash
git clone https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
source .venv/bin/activate   # 或 uv venv .venv && source .venv/bin/activate

./packaging/scripts/build-release-artifacts.sh
ls dist/
# intellect_agent-0.6.3-py3-none-any.whl
# intellect_agent-0.6.3.tar.gz
# intellect_community_core-0.1.0-cp312-cp312-manylinux_2_28_x86_64.whl  (示例)
```

### 仅本地开发（editable）

```bash
uv sync --frozen
cd rust-core && maturin develop --release
pip install -e .
intellect version
```

### 验证 Rust 扩展

```bash
python -c "import intellect_community_core as c; print(c.detect_dangerous_command_rs('echo hi'))"
scripts/run_tests.sh tests/intellect_state/test_rust_parity.py -q
```

---

## Wheel 规范

| 属性 | 值 |
|------|-----|
| manylinux 标签 | `manylinux_2_28_x86_64` / `manylinux_2_28_aarch64` |
| 最低 glibc | 2.28（Ubuntu 20.04+、Debian 11+、RHEL 9+） |
| Python ABI | cp312、cp313 |
| 可选 musl | `musllinux_1_2_x86_64`（Alpine，P2 优先级） |

### 兼容性说明

- **CentOS 7 / RHEL 7**（glibc 2.17）：不在 P0 支持范围；需源码编译或升级 OS
- **WSL2**：与原生 Linux 相同，推荐使用 glibc manylinux wheel
- **Termux (Android)**：独立路径，见 [Termux 指南](https://intellect-agent.ontoweb.cn/docs/getting-started/termux)；Termux 安装脚本会 `pkg install rust`

---

## Root 模式 / FHS 布局

```bash
sudo curl -fsSL .../install.sh | sudo bash
```

| 路径 | 内容 |
|------|------|
| `/usr/local/lib/intellect-agent/` | 代码 |
| `/usr/local/bin/intellect` | 入口 |
| `/root/.intellect/` 或 `$INTELLECT_HOME` | 数据 |

---

## 当前缺口与变通

| 问题 | 变通 |
|------|------|
| `pip install intellect-agent` 后缺 Rust | 手动：`cd rust-core && maturin develop --release` |
| Git 安装脚本未构建 Rust | 安装后执行上述 maturin 命令 |
| Docker 镜像缺 Rust | 暂用 Git 安装或本地构建镜像 |

跟踪进度：[design.md](./design.md) 实施路线图。

---

## 故障排查

**`RuntimeError: intellect_community_core Rust extension is not installed`**

```bash
pip install maturin
cd ~/.intellect/intellect-agent/rust-core   # 或你的 clone 路径
maturin develop --release
```

**`ImportError: ... wrong ELF class`** — 架构不匹配（如在 arm64 上装了 x86_64 wheel）：

```bash
pip uninstall intellect-community-core
pip install --force-reinstall --no-cache-dir intellect-community-core
```

**SQLite WAL 在网络文件系统失败** — Intellect 会自动回退到 DELETE journal mode；见 `intellect_state.py` 注释。
