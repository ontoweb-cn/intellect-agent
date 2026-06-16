# Docker 发布与版本策略

> 镜像：`docker.io/ontoweb/intellect-agent`  
> 源码 tag 来源：**Gitee** `https://gitee.com/ontoweb/intellect-agent`

## 1. 版本标签体系

Docker 镜像使用**多层标签**，与 Gitee Release 的 SemVer / CalVer 对齐：

| 标签 | 示例 | 何时更新 | 用途 |
|------|------|----------|------|
| **`latest`** | `ontoweb/intellect-agent:latest` | `main` 分支每次成功构建 | 开发/尝鲜；生产不推荐 |
| **`main`** | `ontoweb/intellect-agent:main` | 同 `latest` | 明确表示跟踪 main |
| **SemVer 精确** | `ontoweb/intellect-agent:0.6.3` | Gitee Release 发版 | **生产推荐** |
| **SemVer minor** | `ontoweb/intellect-agent:0.6` | 同 Release（可选） | 自动获得 patch 更新 |
| **CalVer** | `ontoweb/intellect-agent:v2026.6.16` | Git tag 名 | 与 Release 页一一对应 |
| **Git SHA** | `ontoweb/intellect-agent:sha-abc1234` | 每次构建（可选） | 审计、回滚 |
| **Digest** | `ontoweb/intellect-agent@sha256:…` | 推送后固定 | 不可变引用（最安全） |

### 版本对应关系

```
Gitee tag v2026.6.16
    ├── pyproject.toml version → 0.6.3
    ├── Docker tags:
    │     ontoweb/intellect-agent:0.6.3
    │     ontoweb/intellect-agent:v2026.6.16
    │     ontoweb/intellect-agent:0.6      (可选)
    └── Gitee Release 附件 + Native bundles
```

### 当前 CI 行为 vs 目标态

| 事件 | 当前（`.github/workflows/docker-publish.yml`） | 目标态 |
|------|-----------------------------------------------|--------|
| push `main` | `:latest`, `:main` | 不变 |
| GitHub `release` published | `:{release_tag_name}`（CalVer） | 同步到 Gitee tag 触发 |
| Release 发版 | ❌ 无 `:0.6.3` semver 标签 | ✅ 增加 `:semver` + `:v{calver}` |

---

## 2. 多架构

| 平台 | Docker 平台 | 构建 runner |
|------|-------------|-------------|
| Linux x86_64 | `linux/amd64` | `ubuntu-latest` |
| Linux arm64 | `linux/arm64` | `ubuntu-24.04-arm` |

合并 manifest：

```bash
docker buildx imagetools create \
  -t ontoweb/intellect-agent:0.6.3 \
  -t ontoweb/intellect-agent:v2026.6.16 \
  -t ontoweb/intellect-agent:latest \
  ontoweb/intellect-agent@sha256:{amd64_digest} \
  ontoweb/intellect-agent@sha256:{arm64_digest}
```

---

## 3. 镜像内版本信息

构建时注入（Dockerfile 已有 `INTELLECT_GIT_SHA` build-arg）：

| 变量 / 文件 | 内容 |
|-------------|------|
| `INTELLECT_GIT_SHA` | 构建时 Git commit |
| `INTELLECT_VERSION` | `0.6.3`（来自 pyproject，目标态） |
| `INTELLECT_RELEASE_TAG` | `v2026.6.16`（目标态） |
| `intellect version` | 运行时查询 |

验证：

```bash
docker run --rm ontoweb/intellect-agent:0.6.3 intellect version
docker inspect ontoweb/intellect-agent:0.6.3 --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
```

**目标态 OCI Labels：**

```dockerfile
LABEL org.opencontainers.image.version="0.6.3"
LABEL org.opencontainers.image.revision="${INTELLECT_GIT_SHA}"
LABEL io.intellect.release.tag="v2026.6.16"
LABEL io.intellect.rust.version="0.1.0"
```

---

## 4. Rust 扩展（容器内）

v0.6.2+ 要求 `intellect_community_core`。Dockerfile **必须**在镜像构建阶段编译 Rust：

```dockerfile
# 在 COPY 源码后、uv sync 前
RUN apt-get install -y --no-install-recommends build-essential cargo pkg-config && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN uv pip install maturin && \
    cd rust-core && maturin develop --release
```

multi-arch：各架构 runner 本地编译，无需 cross-compile。

---

## 5. 用户使用

### 拉取指定版本（推荐）

```bash
# SemVer — 生产环境
docker pull ontoweb/intellect-agent:0.6.3

# CalVer — 与 Gitee Release 页面对应
docker pull ontoweb/intellect-agent:v2026.6.16

# 不可变 digest
docker pull ontoweb/intellect-agent@sha256:...
```

### 运行

```bash
docker run -it --rm \
  -v ~/.intellect:/home/intellect/.intellect \
  -e INTELLECT_HOME=/home/intellect/.intellect \
  ontoweb/intellect-agent:0.6.3 \
  intellect chat
```

Gateway：

```bash
docker run -d \
  --name intellect-gateway \
  -v ~/.intellect:/home/intellect/.intellect \
  -p 8080:8080 \
  ontoweb/intellect-agent:0.6.3 \
  intellect gateway
```

### 升级

```bash
docker pull ontoweb/intellect-agent:0.6.4
# 更新 compose / systemd unit 中的镜像 tag
docker stop intellect-gateway && docker rm intellect-gateway
# 用新 tag 重新 run
```

**不要**在生产环境长期使用 `:latest`——main 分支可能包含未发布变更。

---

## 6. CI 变更设计（目标态）

在 `docker-publish.yml` 的 `merge` job 中，Release 事件增加 semver 标签：

```yaml
- name: Create manifest list and push
  run: |
    SEMVER=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
    if [ "${{ github.event_name }}" = "release" ]; then
      TAG="${{ github.event.release.tag_name }}"
      docker buildx imagetools create \
        -t "${IMAGE_NAME}:${TAG}" \
        -t "${IMAGE_NAME}:${SEMVER}" \
        "${args[@]}"
    else
      docker buildx imagetools create \
        -t "${IMAGE_NAME}:main" \
        -t "${IMAGE_NAME}:latest" \
        "${args[@]}"
    fi
```

发版触发链（目标态）：

```
Gitee tag push v2026.6.16
  → CI: build amd64 + arm64
  → merge manifest
  → push :0.6.3 :v2026.6.16
  → Gitee Release 附件（含 compose 示例）
```

---

## 7. Gitee 容器镜像（可选镜像）

国内用户可从 Gitee 容器镜像服务同步（P3）：

```
registry.cn-hangzhou.aliyuncs.com/ontoweb/intellect-agent:0.6.3
```

与 Docker Hub 使用相同 tag 命名，定期从 Release 构建同步。

---

## 8. docker-compose 示例（随 Release 发布）

`packaging/docker/docker-compose.yml`（目标态附件）：

```yaml
services:
  intellect:
    image: ontoweb/intellect-agent:0.6.3
    volumes:
      - intellect-data:/home/intellect/.intellect
    command: intellect gateway
    restart: unless-stopped

volumes:
  intellect-data:
```

---

## 9. 故障排查

**容器内 `ImportError: intellect_community_core`** — 镜像未编译 Rust；使用 ≥ 含 Rust 构建的 semver tag，或自行 `docker build`。

**`:latest` 行为与文档不一致** — 检查 `docker inspect` 的 `Created` 时间与 Gitee main 最新 commit。

**arm64 上 pull 慢** — 确认 manifest 含 `linux/arm64`：`docker buildx imagetools inspect ontoweb/intellect-agent:0.6.3`.
