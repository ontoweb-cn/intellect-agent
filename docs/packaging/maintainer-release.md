# 发版检查清单（维护者）

面向发布工程师。**主渠道为 Gitee**，Docker 与 PyPI 为镜像。

- Gitee Release：`https://gitee.com/ontoweb/intellect-agent/releases`
- Docker 版本策略：[docker.md](./docker.md)
- Native 包规范：[gitee-releases.md](./gitee-releases.md)

## 1. 版本 bump

| 文件 | 字段 |
|------|------|
| `pyproject.toml` | `[project].version` → Docker `:0.6.x` |
| `intellect_cli/__init__.py` | `__version__` |
| `acp_registry/agent.json` | `version` |
| `rust-core/Cargo.toml` | Rust API 变更时 bump |
| `packaging/manifests/artifacts.yaml` | `release.python_semver` |
| `packaging/docker/docker-compose.example.yml` | `image:` tag |

## 2. 本地构建

```bash
# Python + Rust wheels
./packaging/scripts/build-release-artifacts.sh

# 三平台 Native 包（在当前 OS 上构建对应平台）
./packaging/scripts/build-native-bundle.sh

# 生成校验和
cd dist && shasum -a 256 intellect_* *.whl ../dist/native/* 2>/dev/null > SHA256SUMS
```

CI 目标态应在 **Linux / macOS / Windows** 矩阵各跑 `build-native-bundle.sh`，合并上传 Gitee。

## 3. 测试

```bash
scripts/run_tests.sh -q
scripts/run_tests.sh tests/intellect_state/test_rust_parity.py -q
cd rust-core && cargo test
```

## 4. 创建 Gitee Release

### 推荐流程（CI 全平台矩阵）

```bash
# 1. 本地发版：bump 版本、打 tag、push（默认不上传 Gitee，等 CI）
python scripts/release.py --bump patch --publish

# 2. tag push 后 GitHub Actions 自动运行：
#    .github/workflows/gitee-release.yml
#    → 构建 Linux/macOS/Windows wheels + Native 包
#    → 上传到 Gitee Release
```

**GitHub Secrets 必填：**

| Secret | 说明 |
|--------|------|
| `RELEASE_TOKEN` | Gitee 私人令牌（repo 权限） |

### 可选：本地立即上传（仅当前平台，不完整）

```bash
export RELEASE_TOKEN="..."
python scripts/release.py --bump patch --publish --gitee-local
```

### CI 矩阵产物

| Job | 产出 |
|-----|------|
| `build-python` | `intellect_agent-{semver}.tar.gz`、`*-py3-none-any.whl` |
| `build-platform` ×4 | 各平台 `intellect_community_core-*.whl` + Native tar.gz/zip |
| `publish-gitee` | 合并上传 + `SHA256SUMS` |

手动重跑：`Actions → Gitee Release Artifacts → Run workflow`（输入已有 tag）。

### Gitee Release 附件清单

**Wheel / sdist：**

```
intellect_agent-0.6.3.tar.gz
intellect_agent-0.6.3-py3-none-any.whl
intellect_community_core-0.1.0-*.whl  (各平台 × cp312/cp313)
```

**Native 包：**

```
intellect-linux-x86_64-0.6.3.tar.gz
intellect-linux-aarch64-0.6.3.tar.gz
intellect-darwin-universal2-0.6.3.tar.gz
intellect-windows-amd64-0.6.3.zip
```

**校验：**

```
SHA256SUMS
```

## 5. Docker 发布

Release 发版后触发镜像构建，推送标签：

```bash
docker pull ontoweb/intellect-agent:0.6.3
docker pull ontoweb/intellect-agent:v2026.6.16
```

| 标签 | 何时打 |
|------|--------|
| `0.6.3` | Gitee Release（SemVer） |
| `v2026.6.16` | Gitee tag（CalVer） |
| `latest` | 仅 main 推送 |

验证：

```bash
docker run --rm ontoweb/intellect-agent:0.6.3 intellect version
docker buildx imagetools inspect ontoweb/intellect-agent:0.6.3
```

目标态 CI 变更见 [docker.md](./docker.md) §6。

## 6. PyPI（可选镜像）

```bash
# tag push 触发 upload_to_pypi.yml，或手动
uv build --sdist --wheel
# 先上传 intellect-community-core wheels，再 intellect-agent
```

## 7. 发布后验证

### Gitee Native 包

```bash
curl -LO https://gitee.com/ontoweb/intellect-agent/releases/download/v2026.6.16/intellect-linux-x86_64-0.6.3.tar.gz
tar xzf intellect-linux-x86_64-0.6.3.tar.gz
./intellect-linux-x86_64-0.6.3/bin/intellect version
```

### 安装脚本

```bash
curl -fsSL https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
```

```powershell
iex (irm https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.ps1)
```

### 三平台 smoke

- [ ] Linux x86_64 — Native tar.gz 或 install.sh
- [ ] Linux arm64 — Native tar.gz
- [ ] macOS — universal2 tar.gz
- [ ] Windows — zip 或 install.ps1
- [ ] Docker `:0.6.3` amd64 + arm64

## 8. Release Notes 模板

```markdown
## v0.6.3 (v2026.6.16)

### 安装

**Linux / macOS Native 包（推荐）：**
https://gitee.com/ontoweb/intellect-agent/releases/tag/v2026.6.16

**Docker：**
docker pull ontoweb/intellect-agent:0.6.3

**一键安装：**
curl -fsSL https://gitee.com/ontoweb/intellect-agent/raw/main/scripts/install.sh | bash
```

## 相关文档

- [gitee-releases.md](./gitee-releases.md)
- [docker.md](./docker.md)
- [design.md](./design.md)
