# Windows 打包与安装

> 适用：Windows 10/11 x64。  
> **官方渠道：[Gitee Release](https://gitee.com/ontoweb/intellect-agent/releases)**

:::info 状态
原生 Windows 为 **Early Beta**；WSL2 + Linux Native 包为更稳定备选。
:::

## 推荐安装方式

### 方式 A：PowerShell 一键安装（Gitee）

```powershell
iex (irm https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.ps1)
```

### 方式 B：Gitee Native zip（目标态，离线/内网）

```powershell
$Tag = "v2026.6.16"
$Ver = "0.6.3"
$Url = "https://gitee.com/ontoweb/intellect-agent/releases/download/$Tag/intellect-windows-amd64-$Ver.zip"
Invoke-WebRequest -Uri $Url -OutFile "intellect.zip"
Expand-Archive intellect.zip -DestinationPath "$env:LOCALAPPDATA\intellect"
& "$env:LOCALAPPDATA\intellect\intellect-windows-amd64-$Ver\bin\intellect.cmd" version
```

### 方式 C：WSL2 + Linux Native 包

在 WSL2 内使用 [linux.md](./linux.md) 的 Gitee Native tar.gz 安装。

---

## 系统依赖

| 组件 | 用途 | 获取方式 |
|------|------|----------|
| Python ≥ 3.12 | 运行时 | 安装脚本通过 `uv` 自动安装 |
| MSVC Build Tools | 编译 Rust 扩展 | [Build Tools for VS 2022](https://visualstudio.microsoft.com/visual-cpp-build-tools/) |
| Rust toolchain | maturin 构建 | `winget install Rustlang.Rustup` |
| PortableGit | bash / terminal 工具 | 安装脚本自动下载 |
| Node.js 22 | TUI / browser | 安装脚本自动安装 |
| ripgrep | 文件搜索 | 安装脚本自动安装 |
| ffmpeg | TTS | 安装脚本自动安装 |

### 手动安装 Rust 工具链（开发者）

```powershell
# Rust
winget install Rustlang.Rustup
# 重启终端后
rustup default stable

# MSVC（若未安装 VS）
winget install Microsoft.VisualStudio.2022.BuildTools
# 安装时勾选「使用 C++ 的桌面开发」

pip install maturin
```

---

## 开发者 / 维护者：本地打包

### 构建 win_amd64 wheel

```powershell
cd intellect-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install uv maturin

cd rust-core
maturin build --release
# → target\wheels\intellect_community_core-*-win_amd64.whl
```

或使用 Git Bash / WSL 运行统一脚本：

```bash
./packaging/scripts/build-release-artifacts.sh
```

### 本地开发（editable）

```powershell
uv sync --frozen
cd rust-core
maturin develop --release
pip install -e .
```

### 验证

```powershell
python -c "import intellect_community_core; print('OK')"
intellect version
```

---

## Wheel 规范

| 属性 | 值 |
|------|-----|
| 标签 | `win_amd64` |
| 工具链 | MSVC（非 MinGW） |
| Python ABI | cp312、cp313 |
| 扩展模块 | `intellect_community_core.pyd` |

### 32 位 Windows

不支持。安装脚本在 32 位系统回退到 MinGit（无 bash），terminal 工具不可用。

---

## 目录布局

| 路径 | 内容 |
|------|------|
| `%LOCALAPPDATA%\intellect\intellect-agent\` | Git clone |
| `%LOCALAPPDATA%\intellect\git\` | PortableGit |
| `%LOCALAPPDATA%\intellect\config.yaml` | 配置（或通过 `INTELLECT_HOME`） |
| `%LOCALAPPDATA%\intellect\state.db` | Session 数据库 |

环境变量：

- `INTELLECT_HOME` — 覆盖数据根目录
- `intellect_GIT_BASH_PATH` — 安装脚本设置的 bash 路径
- `intellect_DISABLE_WINDOWS_UTF8=1` — 编码问题调试

---

## 打包注意事项

### PyO3 + MSVC

- maturin 在 Windows 上默认使用 MSVC；需 `VCToolsInstallDir` 在 PATH 中
- CI 使用 `windows-latest` + `actions/setup-python` + `dtolnay/rust-toolchain@stable`

### 代码签名（可选，P3）

正式发布 .exe / .pyd 可考虑 Authenticode 签名，减少 SmartScreen 警告。当前 Git 安装路径不涉及独立 .exe 分发。

### 安装脚本 Stage 协议

`install.ps1` 支持 `-Stage` / `-Manifest` 供 GUI 安装器调用；Rust 构建应作为新 stage `rust_build` 插入 `pip_install` 之前。见脚本内 Stage protocol 注释。

---

## 当前缺口与变通

| 问题 | 变通 |
|------|------|
| `install.ps1` 未构建 Rust | 安装后手动：`cd rust-core; maturin develop --release` |
| PyPI 无 win wheel | 安装 Rust + maturin 后本地编译 |
| 缺 MSVC | 安装 VS Build Tools 后重开终端 |

---

## 故障排查

**`error: linker 'link.exe' not found`**

安装 Visual Studio Build Tools，勾选 C++ 构建工具，重启 PowerShell。

**`intellect_community_core` ImportError**

```powershell
pip install maturin
cd $env:LOCALAPPDATA\intellect\intellect-agent\rust-core
maturin develop --release
```

**终端工具报 bash 找不到**

确认 `%LOCALAPPDATA%\intellect\git\usr\bin\bash.exe` 存在，或设置 `intellect_GIT_BASH_PATH`。

**Unicode 乱码**

默认已启用 UTF-8 stdio（`intellect_bootstrap`）。若异常，设置 `intellect_DISABLE_WINDOWS_UTF8=1` 对比行为。

---

## 与 Linux/macOS 差异

| 特性 | Windows 原生 | Linux / macOS |
|------|--------------|---------------|
| Shell 工具 | PortableGit bash | 系统 bash/zsh |
| 进程组信号 | 无 `os.killpg` | POSIX 进程组 |
| 时区数据 | 需 `tzdata` pip 包 | 系统 IANA 数据库 |
| 嵌入式终端 pane | 仅 WSL2 | 原生支持 |
| Gateway 后台进程 | PowerShell job | systemd / nohup |
