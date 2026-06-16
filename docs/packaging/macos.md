# macOS 打包与安装

> 适用：macOS 11+，Intel 与 Apple Silicon。  
> **官方渠道：[Gitee Release](https://gitee.com/ontoweb/intellect-agent/releases)**

## 推荐安装方式

### 方式 A：Gitee Native 包（目标态，推荐）

```bash
TAG=v2026.6.16
VER=0.6.3
curl -LO "https://gitee.com/ontoweb/intellect-agent/releases/download/${TAG}/intellect-darwin-universal2-${VER}.tar.gz"
tar xzf "intellect-darwin-universal2-${VER}.tar.gz"
./intellect-darwin-universal2-${VER}/bin/intellect version
```

universal2 包同时支持 Intel 与 Apple Silicon。

### 方式 B：Gitee 一键 Git 安装

```bash
curl -fsSL https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
source ~/.zshrc
intellect
```

### 方式 C：Homebrew（维护者 / 高级用户）

```bash
# 使用 packaging/homebrew/intellect-agent.rb 作为 tap formula
brew install --build-from-source ./packaging/homebrew/intellect-agent.rb
```

> **当前缺口**：formula 尚未集成 Rust 构建；见 [design.md](./design.md) §4.4。

---

## 系统依赖

| 组件 | 用途 | 安装 |
|------|------|------|
| Xcode Command Line Tools | 编译 Rust 扩展 | `xcode-select --install` |
| Homebrew | 系统工具包管理 | [brew.sh](https://brew.sh) |
| ripgrep | 文件搜索 | `brew install ripgrep` |
| ffmpeg | TTS | `brew install ffmpeg` |
| Node.js ≥ 22 | TUI / browser | `brew install node@22` |
| Rust（源码编译时） | maturin 构建 | `brew install rust` 或 rustup |

---

## 开发者 / 维护者：本地打包

### 构建 universal2 wheel（推荐）

```bash
cd intellect-agent
rustup target add aarch64-apple-darwin x86_64-apple-darwin

cd rust-core
maturin build --release --target universal2-apple-darwin
# → target/wheels/intellect_community_core-*-macosx_11_0_universal2.whl
```

### 构建当前架构 wheel

```bash
./packaging/scripts/build-release-artifacts.sh
```

Apple Silicon 默认产出 `macosx_11_0_arm64`；Intel Mac 产出 `macosx_11_0_x86_64`。

### 本地开发

```bash
uv sync --frozen
cd rust-core && maturin develop --release
pip install -e .
```

---

## Wheel 规范

| 属性 | 值 |
|------|-----|
| 优先标签 | `macosx_11_0_universal2` |
| 备选 | `macosx_11_0_arm64` / `macosx_11_0_x86_64` |
| 最低 macOS | 11.0 (Big Sur) |
| Python ABI | cp312、cp313 |

### Rosetta 说明

universal2 wheel 在 Apple Silicon 上原生运行，无需 Rosetta。Intel Mac 使用 x86_64 slice。

---

## 数据与配置路径

| 路径 | 内容 |
|------|------|
| `~/.intellect/config.yaml` | 主配置 |
| `~/.intellect/.env` | API 密钥 |
| `~/.intellect/state.db` | Session 数据库 |
| `~/.intellect/logs/` | 日志 |
| `~/.intellect/profiles/<name>/` | 多 Profile 实例 |

Profile 切换：`intellect -p coder chat`

---

## Homebrew 打包要点

formula 位于 `packaging/homebrew/intellect-agent.rb`。更新发版时：

1.  bump `url` / `version` / `sha256`（指向 semver sdist）
2.  添加 `depends_on "rust" => :build`
3.  在 `install` 中执行 `maturin develop --release`
4.  `brew audit --strict intellect-agent`

环境变量（已有）：

- `INTELLECT_BUNDLED_SKILLS` → formula `pkgshare/skills`
- `intellect_MANAGED=homebrew` → 阻止 `intellect update` 走 git pull

详见 `packaging/homebrew/README.md`。

---

## 当前缺口与变通

| 问题 | 变通 |
|------|------|
| PyPI 无 Rust wheel | `brew install rust && pip install maturin && cd rust-core && maturin develop --release` |
| Gatekeeper 拦截未签名二进制 | 从 PyPI / Homebrew 安装；或 `xattr -d com.apple.quarantine`（仅信任来源） |

---

## 故障排查

**`xcrun: error: invalid active developer path`**

```bash
xcode-select --install
```

**maturin 链接失败 `ld: library not found`** — 确认 Xcode CLI 已安装且 `xcode-select -p` 指向有效路径。

**TUI 无法启动** — 确认 Node 22+ 与 `ui-tui` 已构建：

```bash
cd ui-tui && npm ci && npm run build
```
