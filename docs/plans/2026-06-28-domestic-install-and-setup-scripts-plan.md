# 国内安装文档与源码 Setup 脚本改进方案

> **状态**: ✅ Phase 1 + Phase 2 已完成  
> **日期**: 2026-06-28  
> **关联**: `README.md`, `README.zh-CN.md`, `setup-intellect.sh`, `setup-intellect.ps1`, `scripts/install.sh`, `scripts/install.ps1`, `VERIFY.md`, `website/docs/getting-started/installation.md`, `website/docs/developer-guide/contributing.md`

---

## 背景与目标

项目需要在以下两方面改进：

1. **README 快速安装**：Linux、macOS、WSL2、Windows（原生 PowerShell）需同时提供**全球**与**国内**安装方式。
2. **源码安装**：手动 clone 完成后，需提示或自动运行 `setup-intellect.sh`；Windows 需提供原生 PowerShell 版 `setup-intellect.ps1`。

本文档记录现状诊断、分阶段方案、验收标准与待决策项，供评审与实施。

---

## 现状诊断

### 需求 1：README 国内安装方式

#### 文档不一致（主要问题）

| 文档 | Linux/macOS/WSL2 | Windows PowerShell | 说明 |
|------|------------------|-------------------|------|
| `README.md`（英文） | GitHub (`ontoweb-cn`) + **Gitee** | GitHub (`ontoweb-cn`) + **Gitee** | 已双轨 |
| `README.zh-CN.md` | **仅 GitHub**（org 为 `ontoweb`，**疑似错误**） | **仅 GitHub**（同上） | 缺国内路径 + GitHub org 错误 |
| `website/docs/getting-started/installation.md` | **仅 Gitee** | **仅 Gitee** | 与 README 策略相反 |

**关键 bug**：`README.zh-CN.md` 的 GitHub URL 使用 `raw.githubusercontent.com/ontoweb/intellect-agent/...`（org 为 `ontoweb`），但英文 README 使用 `raw.githubusercontent.com/ontoweb-cn/intellect-agent/...`（org 为 `ontoweb-cn`）。中文 README 的 GitHub 命令指向了错误的仓库地址，Phase 1 需一并修正。

英文 README 已包含 Gitee 脚本地址，但中文 README 的快速安装仍指向 `raw.githubusercontent.com`（且 org 有误），国内用户最容易踩坑。

#### 更深一层：依赖下载镜像

即便安装脚本从 Gitee 下载，安装过程仍可能访问海外源：

| 依赖 | 当前行为 | 国内优化状态 |
|------|----------|--------------|
| 安装脚本 / 源码 | Gitee raw / clone | 部分已做；中文 README 未同步 |
| `uv` 安装器 | `astral.sh` | 文档未说明 |
| PyPI / pip | 默认 `pypi.org` | `AGENTS.md`、`VERIFY.md` 有镜像说明，README 未写 |
| Node.js / PortableGit | Windows 安装器可能走 GitHub | 未文档化 |
| Rust wheel | Gitee Releases 预编译 | `install.sh` / `install.ps1` 已自动尝试 |
| Rust toolchain | 官方 rustup | `install.ps1` 有清华镜像回退 |
| npm registry | 默认 registry | 未文档化 |
| Docker | Docker Hub + 阿里云镜像 | 中文 README 已有阿里云 |

**结论**：「国内安装」应分两层——**脚本/源码来源**（Gitee vs GitHub）与**依赖下载镜像**（PyPI / uv / npm / Node 等）。Phase 1 以文档同步为主；Phase 3 可选做安装器全自动镜像。

---

### 需求 2：源码安装后运行 setup 脚本

#### 当前三条路径，体验不统一

```
用户安装方式
├── 一行命令 install.sh / install.ps1
│   └── clone → 脚本内联 venv/依赖/setup wizard
│       └── 不调用 setup-intellect.sh
├── git clone 手动源码（README Contributing）
│   └── 提到 setup-intellect.sh
│   └── 无 Windows 脚本；clone 后无强制提示
└── website contributing 手动 uv 步骤
    └── 未提及 setup-intellect.sh
```

#### 关键事实

- `setup-intellect.sh` 存在且功能完整（uv / venv / 依赖 / PATH / 可选 setup wizard）。
- **`setup-intellect.ps1` 不存在**。
- `install.sh` / `install.ps1` 与 `setup-intellect.sh` **逻辑大量重复**，未复用。
- README 手动路径仍写 **Python 3.11**，与 `pyproject.toml` 的 `>=3.12` 及脚本中的 `3.12` 不一致。

#### install.sh 与 setup-intellect.sh 职责差异

| 能力 | `install.sh` | `setup-intellect.sh` |
|------|--------------|----------------------|
| clone 仓库到 `~/.intellect/intellect-agent` | ✓ | ✗（假定已在仓库内） |
| 系统依赖（Node、ffmpeg、browser 等） | ✓ | 部分（ripgrep 可选） |
| uv / venv / Python 依赖 | ✓ | ✓ |
| PATH / symlink | ✓ | ✓ |
| setup wizard | ✓（`--skip-setup` 可跳过） | ✓（交互确认） |
| Termux 分支 | ✓ | ✓ |

---

## 推荐实施路径：Phase 1 + Phase 2

| 阶段 | 范围 | 工期（估） | 风险 |
|------|------|-----------|------|
| **Phase 1** | 文档同步 + 国内镜像说明 | 0.5–1 人日 | 低 |
| **Phase 2** | `setup-intellect.ps1` + 源码安装文档 + Python 3.12 修正 | 2–4 人日 | 中 |
| Phase 3（可选） | `INTELLECT_CN=1` + install 复用 setup | 5–8 人日 | 中高 |

---

## Phase 1：文档同步

### 1.1 同步 `README.zh-CN.md` 快速安装

**首要：修正 GitHub org 错误。** 当前中文 README 的 GitHub URL 指向 `ontoweb/intellect-agent`，英文版指向 `ontoweb-cn/intellect-agent`。修正为 `ontoweb-cn`（与英文版一致）。

修正后与英文版对齐，补充 Gitee 命令与说明块：

```bash
# GitHub
curl -fsSL https://raw.githubusercontent.com/ontoweb-cn/intellect-agent/main/scripts/install.sh | bash

# Gitee（国内）
curl -fsSL https://raw.giteeusercontent.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
```

```powershell
# GitHub
iex (irm https://raw.githubusercontent.com/ontoweb-cn/intellect-agent/main/scripts/install.ps1)

# Gitee（国内）
iex (irm https://raw.giteeusercontent.com/ontoweb/intellect-agent/raw/main/scripts/install.ps1)
```

说明文案（与英文 README 第 20 行一致）：

> GitHub 用户用第一条；国内用户用 Gitee 第二条。脚本相同，仅下载源不同。

### 1.2 新增「国内用户」小节

在 `README.md`、`README.zh-CN.md` 中增加表格（可交叉引用 `VERIFY.md`）：

| 资源 | 国内推荐 | 备注 |
|------|----------|------|
| 安装脚本 | Gitee raw | 已有 |
| 源码 clone | `gitee.com/ontoweb/intellect-agent` | 已有 |
| pip / uv | `UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` 或清华 | 见 `AGENTS.md`（开发者文档）与 `VERIFY.md` |
| Docker | 阿里云容器镜像 | 中文 README 已有 |
| pip 包 | 清华 / 阿里 / 中科大 | 见 `VERIFY.md` |
| Rust wheel | Gitee Releases | 安装器已自动尝试 |
| npm（仅开发环境） | `registry.npmmirror.com` | 仅开发/contributing 场景；终端用户安装不涉及 npm |

**国内加速安装示例**（写入 README）：

```bash
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
curl -fsSL https://raw.giteeusercontent.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
```

```powershell
$env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
iex (irm https://raw.giteeusercontent.com/ontoweb/intellect-agent/raw/main/scripts/install.ps1)
```

### 1.3 统一文档策略

**决策：全部文档统一为 GitHub + Gitee 双轨**（与英文 README 一致）。理由：
- 中文用户可从 GitHub URL 下载（慢但通常可访问），Gitee 作为加速备选
- 海外用户（包括海外中文用户）只需 GitHub
- 避免出现"中文 README 只有 GitHub、官网只有 Gitee"的割裂体验

- **README**：GitHub + Gitee 并列（海外 + 国内双轨）。
- **官网 `installation.md`**：改为 GitHub + Gitee 双轨；Gitee 保留为国内推荐但非唯一选项。
- **contributing 文档**：Phase 2 改为推荐 `setup-intellect.sh` / `setup-intellect.ps1`。

### Phase 1 改动文件清单

- [ ] `README.zh-CN.md` — **修正 GitHub org**（`ontoweb`→`ontoweb-cn`）+ 快速安装双轨 + 国内用户小节 + pip 小节（对齐英文）
- [ ] `README.md` — 补充国内镜像环境变量示例（若尚未有）
- [ ] `website/docs/getting-started/installation.md` — 增加 GitHub 备选链接
- [ ] `website/i18n/zh-Hans/.../getting-started/installation.md` — 同上

---

## Phase 2：源码 Setup 脚本与文档

### 2.1 新增 `setup-intellect.ps1`

与 `setup-intellect.sh` 功能对齐：

| 步骤 | bash 版 | PowerShell 版 |
|------|---------|---------------|
| 检测 / 安装 uv | ✓ | ✓ |
| Python 3.12 venv | ✓ | ✓ |
| `uv sync --extra all --locked` 或 pip fallback | ✓ | ✓ |
| 创建 `.env` | ✓ | ✓ |
| 链接 `%USERPROFILE%\.local\bin\intellect` | ✓ | ✓ |
| 写入 User PATH | ✓ | ✓ |
| 同步 bundled skills | ✓ | ✓ |
| 可选运行 `intellect setup` | ✓ | ✓ |

**参数设计**（与 install 脚本命名一致）：

```powershell
.\setup-intellect.ps1                  # 交互式（默认）
.\setup-intellect.ps1 -NonInteractive  # CI / 自动化
.\setup-intellect.ps1 -SkipSetup       # 跳过 setup wizard 交互
```

**实现建议**：

- 短期：从 `setup-intellect.sh` 逐段移植，复用 `scripts/install.ps1` 中的 uv 检测、PATH 写入、控制台输出风格。
- 长期（Phase 3 可选）：抽取共享逻辑到 `tools/setup_intellect.py`，由 sh/ps1 薄包装调用。

**环境变量**（与 bash 对齐）：

- `UV_NO_CONFIG=1` — 防止 sudo / 多用户场景下 uv 读错配置。
- `UV_INDEX_URL` — 国内 PyPI 镜像（可选，由用户或 `INTELLECT_CN` 设置）。

### 2.2 扩展 README「从源码安装」专节

在 Contributing 之前或之内，增加独立小节 **Install from Source** / **从源码安装**：

**Linux / macOS / WSL2：**

```bash
# GitHub
git clone --recurse-submodules https://github.com/ontoweb-cn/intellect-agent.git
# Gitee（国内）
git clone --recurse-submodules https://gitee.com/ontoweb/intellect-agent.git

cd intellect-agent
./setup-intellect.sh     # 必须运行：创建 venv、安装依赖、配置 PATH
```

**Windows（原生 PowerShell）：**

```powershell
git clone --recurse-submodules https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
.\setup-intellect.ps1    # 必须运行
```

**国内源码 + 镜像：**

```bash
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
git clone --recurse-submodules https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
./setup-intellect.sh
```

> **注意**：手动 `git clone` 后必须运行 setup 脚本。一行安装命令（`install.sh` / `install.ps1`）会在内部完成等价步骤，无需再运行 setup 脚本。

### 2.3 自动运行 vs 仅提示

| 方案 | 说明 | 推荐 |
|------|------|------|
| A. 仅文档提示 | 零侵入，易漏步骤 | 作补充 |
| B. clone 后 banner | 实现简单 | 作补充 |
| **C. setup 脚本默认执行，末尾交互 wizard** | 与现 bash 行为一致 | **默认** |
| D. install.sh 调用 setup-intellect.sh | DRY，行为统一 | Phase 3 |

**推荐组合**：

- `setup-intellect.sh` / `.ps1`：clone 后由用户显式运行；脚本内**自动完成** venv/依赖/PATH；末尾询问是否运行 `intellect setup`（可用 `-SkipSetup` / `INTELLECT_SKIP_SETUP=1` 跳过）。
- 为 bash 脚本增加 `--skip-setup` / `--non-interactive` 参数（与 ps1 对齐），便于 CI。

### 2.4 修正 Python 版本文档

将所有 **3.11** 改为 **3.12**（当前所有文档与 `pyproject.toml` `requires-python = ">=3.12"` 不一致）：

**文档文件**（所有位置已确认含 3.11）：

- [ ] `README.md` — Contributing 手动路径（第 235 行 `uv venv .venv --python 3.11`）
- [ ] `README.zh-CN.md` — Contributing 手动路径（第 159 行 `uv venv venv --python 3.11`）
- [ ] `website/docs/getting-started/installation.md` — 三处：第 33 行、第 126 行、第 131 行
- [ ] `website/i18n/zh-Hans/.../getting-started/installation.md` — 同上三处
- [ ] `website/docs/developer-guide/contributing.md` — 第 37 行（"3.11+"）、第 47-48 行（`--python 3.11`）
- [ ] `website/i18n/zh-Hans/.../developer-guide/contributing.md` — 同上

**代码文件**（错误提示中硬编码了 3.11）：

- [ ] `scripts/install.ps1` — 第 474-476 行 fallback 错误消息硬编码 `Python 3.11` 和 `Python.Python.3.11`，应引用 `$PythonVersion`（即 3.12）

### 2.5 更新 Contributing 文档

将 website contributing 的「Clone and Install」改为推荐 setup 脚本：

```bash
git clone --recurse-submodules https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
./setup-intellect.sh
```

Windows 段落指向 `setup-intellect.ps1`。保留「Manual path」作为高级备选，并修正 Python 版本。

### Phase 2 改动文件清单

- [ ] **新增** `setup-intellect.ps1`
- [ ] **可选增强** `setup-intellect.sh` — 增加 `--skip-setup`、`--non-interactive` 参数
- [ ] **修复** `scripts/install.ps1` — 第 474-476 行硬编码 `Python 3.11` 改为引用 `$PythonVersion`
- [ ] `README.md` — 源码安装专节、Contributing 更新、Python 3.12
- [ ] `README.zh-CN.md` — 同上（中文）
- [ ] `website/docs/getting-started/installation.md` — Python 3.12 修正
- [ ] `website/i18n/zh-Hans/.../getting-started/installation.md` — Python 3.12 修正
- [ ] `website/docs/developer-guide/contributing.md` — 推荐 setup 脚本 + Python 3.12
- [ ] `website/i18n/zh-Hans/.../developer-guide/contributing.md` — 同上

---

## Phase 3（可选）：安装器深度国内优化 + 代码复用

### 3.1 国内模式开关 `INTELLECT_CN=1`

当设置 `INTELLECT_CN=1` 时，安装脚本可：

- **依赖下载阶段**自动使用国内镜像（与脚本来源无关）：
  - 若未设置则自动写入 `UV_INDEX_URL`（阿里或清华）
  - 提示 npm 使用 `registry.npmmirror.com`（仅开发场景）
  - uv 安装失败时输出国内手动安装指引
- **clone 阶段**：若脚本从 GitHub raw 下载但用户已设 `INTELLECT_CN=1`，clone 仍优先 Gitee（GitHub raw 可访问不代表 clone 速度快）
- Windows：Node / PortableGit 优先国内镜像或 Gitee Release 资产

> **设计说明**：`INTELLECT_CN=1` 主要控制的是脚本**执行阶段**的依赖下载行为，而非脚本**下载阶段**（用户已通过选择 GitHub/Gitee raw URL 完成了脚本下载）。两个维度独立，不应混淆。

### 3.2 install.sh 复用 setup-intellect.sh

```
install.sh 主流程:
  clone_repo
  install_system_deps (node, ffmpeg, browser...)   # install 独有
  ./setup-intellect.sh --skip-wizard --non-interactive
  run_setup_wizard                                 # install 层负责
```

**收益**：减少双份维护，源码安装与一键安装行为一致。  
**成本**：需处理路径差异（`~/.intellect/intellect-agent` vs 任意 clone 目录）、Termux、root FHS 布局。

---

## 方案对比

| 维度 | Phase 1 仅文档 | Phase 1+2（推荐） | Phase 1+2+3 完整 |
|------|----------------|-------------------|------------------|
| 中文 README 国内命令 | ✓ | ✓ | ✓ |
| 源码安装 setup 指引 | 部分 | ✓ | ✓ |
| Windows PowerShell setup | ✗ | ✓ | ✓ |
| 依赖级国内镜像 | 文档级 | 文档 + setup 可选 env | 安装器全自动 |
| 实现风险 | 低 | 中 | 中高 |
| 工期 | 0.5–1 天 | 2–4 天 | 5–8 天 |

---

## 验收标准

### 需求 1：国内安装文档

- [ ] `README.md` 与 `README.zh-CN.md` 在 Linux/macOS/WSL2、Windows 均含 GitHub + Gitee 命令
- [ ] 国内 PyPI/uv 镜像有可复制示例（含 `UV_INDEX_URL`）
- [ ] 官网 installation 与 README 策略一致或可互相跳转
- [ ] Docker / pip 国内方式在中文 README 中与英文版对齐

### 需求 2：源码 Setup

- [ ] 存在 `setup-intellect.ps1`，在 Windows 原生 PowerShell 可完成与 bash 等价的 dev setup
- [ ] README 有明确的「从源码安装」：clone → 运行 setup 脚本 → `intellect`
- [ ] setup 脚本支持 `-SkipSetup` / `-NonInteractive`（CI 友好）
- [ ] 文档 Python 版本统一为 **3.12**
- [ ] `install.ps1` 错误提示中的 Python 版本引用 `$PythonVersion` 而非硬编码 `3.11`
- [ ] Contributing 文档推荐 setup 脚本而非仅手动 uv 命令

---

## 待决策项

1. **setup 脚本参数命名**：`-SkipSetup` 是否与 `install.ps1` 完全一致？bash 是否同步增加 `--skip-setup`？
2. **Phase 3 是否纳入本迭代**：是否在本轮实现 `INTELLECT_CN=1`？
3. **PowerShell 实现方式**：逐段移植 vs 共享 Python 模块（影响 Phase 2 工期）。
4. **`setup-intellect.sh` 国内 uv 安装**：是否在 `INTELLECT_CN=1` 时提示用户手动安装 uv（astral.sh 在国内可能不稳定）？

---

## 参考链接

- 英文 README 快速安装（已含 Gitee）：`README.md` § Quick Install
- 国内 pip 镜像：`VERIFY.md` § Installing from China
- uv / PyPI 镜像（开发）：`AGENTS.md` § Network / PyPI mirror
- Windows 安装详情：`docs/packaging/windows.md`
- 现有开发者 setup 脚本：`setup-intellect.sh`

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-06-28 | 初稿：需求分析 + Phase 1/2/3 方案与验收标准 |
| 2026-06-28 | 初稿：需求分析 + Phase 1/2/3 方案与验收标准 |
| 2026-06-28 | 评审修订：新增 GitHub org 错误修正、扩展 Python 版本修正范围（+4 文件）、补充 `install.ps1` 代码修复、统一双轨策略决策、细化 `INTELLECT_CN=1` 设计说明 |
| 2026-06-28 | Phase 1+2 实施完成：README.zh-CN.md 双轨+国内小节、website 双轨+Python 3.12、新增 setup-intellect.ps1、setup-intellect.sh 增加参数、install.ps1 修复、所有文档 Python 3.12 统一、Contributing 推荐 setup 脚本 |
| 2026-06-28 | 补充：`CONTRIBUTING.md` 修正 Python 3.12、双轨 clone、推荐 setup 脚本；新增 CI Secrets 章节（Docker Hub + Nix Lockfile 必需 Token，可选 Token 说明） |
