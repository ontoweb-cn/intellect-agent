# Docker 发布与版本策略

> 镜像：`docker.io/ontoweb/intellect-agent`  
> 源码 tag 来源：**Gitee** `https://gitee.com/ontoweb/intellect-agent`

## 1. 版本标签体系

Docker 镜像使用**多层标签**，与 Gitee Release 的 SemVer / CalVer 对齐：

| 标签 | 示例 | 何时更新 | 用途 |
|------|------|----------|------|
| **`latest`** | `ontoweb/intellect-agent:latest` | `main` 分支每次成功构建 | 开发/尝鲜；生产不推荐 |
| **`main`** | `ontoweb/intellect-agent:main` | 同 `latest` | 明确表示跟踪 main |
| **SemVer 精确** | `ontoweb/intellect-agent:0.7.2` | Gitee Release 发版 | **生产推荐** |
| **SemVer minor** | `ontoweb/intellect-agent:0.7` | 同 Release（可选） | 自动获得 patch 更新 |
| **CalVer** | `ontoweb/intellect-agent:v2026.6.16` | Git tag 名 | 与 Release 页一一对应 |
| **Git SHA** | `ontoweb/intellect-agent:sha-abc1234` | 每次构建（可选） | 审计、回滚 |
| **Digest** | `ontoweb/intellect-agent@sha256:…` | 推送后固定 | 不可变引用（最安全） |

### 版本对应关系

```
Gitee tag v2026.6.16
    ├── pyproject.toml version → 0.7.2
    ├── Docker tags:
    │     ontoweb/intellect-agent:0.7.2
    │     ontoweb/intellect-agent:v2026.6.16
    │     ontoweb/intellect-agent:0.7      (可选)
    └── Gitee Release 附件 + Native bundles
```

### 当前 CI 行为

| 事件 | 行为（`.github/workflows/docker-publish.yml`） |
|------|-----------------------------------------------|
| push `main` | `:latest` + `:main` |
| GitHub `release` published / `v*` tag | `:{release_tag_name}`（CalVer）+ `:{semver}`（merge job 从 pyproject 读取，§6 所示目标态已实现） |

---

## 2. 多架构

| 平台 | Docker 平台 | 构建 runner |
|------|-------------|-------------|
| Linux x86_64 | `linux/amd64` | `ubuntu-latest` |
| Linux arm64 | `linux/arm64` | `ubuntu-24.04-arm` |

合并 manifest：

```bash
docker buildx imagetools create \
  -t ontoweb/intellect-agent:0.7.2 \
  -t ontoweb/intellect-agent:v2026.6.16 \
  -t ontoweb/intellect-agent:latest \
  ontoweb/intellect-agent@sha256:{amd64_digest} \
  ontoweb/intellect-agent@sha256:{arm64_digest}
```

---

## 3. 镜像内版本信息

构建时注入（Dockerfile 的 `ARG INTELLECT_VERSION` / `ARG INTELLECT_RUST_VERSION` /
`ARG INTELLECT_RELEASE_TAG`，CI 在 docker-publish.yml 中传入；ARG 默认值由
`packaging/scripts/bump-version.sh` 随发版联动，作为本地无参数构建的兜底）：

| 变量 / 文件 | 内容 |
|-------------|------|
| `INTELLECT_GIT_SHA` | 构建时 Git commit（写入 `/opt/intellect/.intellect_build_sha`） |
| `INTELLECT_VERSION` | pyproject 的 SemVer（写入 OCI label `org.opencontainers.image.version`） |
| `INTELLECT_RUST_VERSION` | rust-core/Cargo.toml 的版本（label `io.intellect.rust.version`） |
| `INTELLECT_RELEASE_TAG` | Release tag 名（label `io.intellect.release.tag`） |
| `intellect version` | 运行时查询 |

验证：

```bash
docker run --rm ontoweb/intellect-agent:0.7.2 intellect version
docker inspect ontoweb/intellect-agent:0.7.2 --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
```

**实际 OCI Labels（build-arg 注入）：**

```dockerfile
ARG INTELLECT_VERSION=0.7.2
ARG INTELLECT_RUST_VERSION=0.7.2
LABEL org.opencontainers.image.version="${INTELLECT_VERSION}"
LABEL io.intellect.release.tag="${INTELLECT_RELEASE_TAG}"
LABEL io.intellect.rust.version="${INTELLECT_RUST_VERSION}"
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
docker pull ontoweb/intellect-agent:0.7.2

# CalVer — 与 Gitee Release 页面对应
docker pull ontoweb/intellect-agent:v2026.6.16

# 不可变 digest
docker pull ontoweb/intellect-agent@sha256:...
```

### 运行

> 镜像内默认 `INTELLECT_HOME=/opt/data`，数据卷请挂载到该路径。

```bash
docker run -it --rm \
  -v ~/.intellect:/opt/data \
  ontoweb/intellect-agent:0.7.2 \
  intellect chat
```

Gateway：

```bash
docker run -d \
  --name intellect-gateway \
  -v ~/.intellect:/opt/data \
  -p 8080:8080 \
  ontoweb/intellect-agent:0.7.2 \
  intellect gateway run
```

> Windows/macOS 用户直接使用仓库根目录的 `docker-compose.yml`（Docker Desktop
> 兼容）；原 `docker-compose.windows.yml` 已删除——其存在理由（host 网络差异）
> 随根 compose 改用显式端口映射而消失。

### 升级

```bash
docker pull ontoweb/intellect-agent:0.7.3
# 更新 compose / systemd unit 中的镜像 tag
docker stop intellect-gateway && docker rm intellect-gateway
# 用新 tag 重新 run
```

**不要**在生产环境长期使用 `:latest`——main 分支可能包含未发布变更。

---

## 6. CI 变更设计（已实现）

Release 事件的 semver 标签已在 `docker-publish.yml` 的 `merge` job 落地：

```yaml
- name: Create manifest list and push
  run: |
    # SEMVER 由 build-amd64 的 meta 步骤从 pyproject 读取（outputs.version）。
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

发版触发链（已实现）：

```
v* tag push / GitHub release published
  → CI: 原生 amd64 + arm64 构建（含冒烟 + tests/docker/ 集成测试）
  → 按 digest 推送 → merge manifest
  → push :{tag} :{semver}（main push 另有 :main :latest）
```

---

## 7. 国内容器镜像（手动同步，待自动化）

国内用户可从阿里云 ACR 个人实例拉取（与 Docker Hub 相同 tag 命名）：

```
crpi-okdl7kgk1p2exqcm.cn-hangzhou.personal.cr.aliyuncs.com/ontoweb/intellect-agent:0.7.2
```

当前为**手动同步**（从 Release 构建后 push）；CI 自动同步需要 ACR 凭据接入
docker-publish 的 merge job（`docker buildx imagetools create` 多加一个 `-t`
即可，无需重推层）——待维护者提供凭据后落地。

---

## 8. docker-compose 示例（随 Release 发布）

`packaging/docker/docker-compose.example.yml`（版本钉由 bump-version.sh 随发版联动）：

```yaml
services:
  intellect-gateway:
    image: ontoweb/intellect-agent:0.7.2
    volumes:
      - intellect-data:/opt/data
    command: ["intellect", "gateway", "run"]
    restart: unless-stopped

  # Interactive CLI (one-shot)
  intellect-chat:
    image: ontoweb/intellect-agent:0.7.2
    profiles: ["cli"]
    stdin_open: true
    tty: true
    volumes:
      - intellect-data:/opt/data
    command: ["intellect", "chat"]

volumes:
  intellect-data:
```

仓库根目录的 `docker-compose.yml` 是完整的单容器部署（Gateway + API Server +
WebUI，双端口 + healthcheck），默认引用 Docker Hub 的 CI 镜像，`.env` 可用
`INTELLECT_IMAGE=` 覆盖为本地构建。

---

## 9. 故障排查

**容器内 `ImportError: intellect_community_core`** — 镜像未编译 Rust；使用 ≥ 含 Rust 构建的 semver tag，或自行 `docker build`。

**`:latest` 行为与文档不一致** — 检查 `docker inspect` 的 `Created` 时间与 Gitee main 最新 commit。

**arm64 上 pull 慢** — 确认 manifest 含 `linux/arm64`：`docker buildx imagetools inspect ontoweb/intellect-agent:0.7.2`.
