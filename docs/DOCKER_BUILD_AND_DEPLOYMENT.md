# Intellect Agent 0.6.7 Docker 镜像构建与部署手册

本文档用于从当前源码的官方 `Dockerfile` 手动构建 `intellect-agent:0.6.7` 镜像，并通过仓库根目录的官方 `docker-compose.yml` 在 Linux 上以单容器方式部署完整的 Intellect Agent。

单个容器同时提供以下能力：

- Intellect Agent 与 Gateway
- OpenAI 兼容 API Server
- WebUI
- Skill、会话、计划任务和工作区
- 浏览器、终端、文件、Git、FFmpeg 等 Agent 工具运行环境

Compose 只负责编排现有镜像，不包含 `build`。镜像必须先使用仓库根目录的 `Dockerfile` 手动构建。

## 1. 目录结构

部署相关目录如下：

```text
intellect-agent/
├── Dockerfile
├── .dockerignore
├── docker-compose.yml
├── .env.example
├── .env                         # 本地配置，不进入 Git
├── docker/
├── plugins/
├── webui/
├── intellect-config/            # 运行后生成，不进入 Git
├── workspace/                   # 运行后生成，不进入 Git
└── 其他项目源码
```

目录用途：

| 路径 | 用途 |
|---|---|
| `Dockerfile` | 从当前源码构建完整功能镜像 |
| `.dockerignore` | 排除本地虚拟环境、缓存、编译产物、运行数据和密钥 |
| `docker-compose.yml` | 使用固定镜像启动单容器服务 |
| `.env` | 保存端口、认证和运行用户配置，不进入 Git |
| `intellect-config` | 持久化模型配置、Provider 凭据、Skill、会话、日志和 WebUI 状态 |
| `workspace` | Agent 默认工作区，可放置需要 Agent 读取或修改的业务文件 |

## 2. 部署架构

镜像和容器的职责边界如下：

```text
项目源码
   │
   │ docker build
   ▼
intellect-agent:0.6.7 镜像
   │
   │ docker compose up
   ▼
intellect-agent-0.6.7 单容器
   ├── Gateway / Agent
   ├── API Server :8642
   └── WebUI      :9119
        │
        ├── ./intellect-config  <->  /opt/data
        └── ./workspace         <->  /workspace
```

`intellect-config` 和 `workspace` 使用宿主机本地 bind mount，不使用 Docker named volume。删除或重建容器不会自动删除这里的数据。

## 3. 环境要求

Linux 部署机需要安装：

- Docker Engine
- Docker Compose v2，即使用 `docker compose` 命令
- Git，仅源码拉取方式需要
- 可访问镜像源、Python/Rust/npm 国内源以及 Dockerfile 中使用的构建资源

建议资源：

- CPU：4 核及以上
- 内存：8 GB 及以上
- 可用磁盘：20 GB 及以上

检查 Docker：

```bash
docker version
docker compose version
```

如果当前用户无权访问 Docker，请将用户加入 Docker 组并重新登录，或者根据服务器安全规范使用 `sudo`：

```bash
sudo usermod -aG docker "$USER"
```

## 4. 手动构建镜像

### 4.1 进入仓库根目录

构建命令必须在包含 `Dockerfile` 和完整项目源码的仓库根目录执行。

```bash
cd /path/to/intellect-agent
```

确认文件存在：

```bash
test -f Dockerfile
test -f .dockerignore
test -f docker-compose.yml
test -f .env.example
```

### 4.2 构建固定版本镜像

执行以下命令：

```bash
docker build -t intellect-agent:0.6.7 .
```

命令参数说明：

| 参数 | 含义 |
|---|---|
| `docker build` | 根据源码构建 Docker 镜像 |
| `-t intellect-agent:0.6.7` | 将镜像名称和版本固定为 `intellect-agent:0.6.7` |
| `.` | 使用当前仓库根目录作为构建上下文 |

构建过程会完成以下工作：

- 从国内镜像地址拉取 Debian 和 Node.js 基础镜像，并从清华 PyPI 安装固定版本 uv
- 使用清华 APT/PyPI、npmmirror 和 RSProxy 下载依赖
- 安装 Python、Node.js、Rust、Chromium、Docker CLI、Git、FFmpeg 等依赖
- 安装 Python 和 Node.js 项目依赖
- 构建终端 UI
- 使用 maturin 编译 Rust 核心扩展
- 检查 WebUI JavaScript 语法
- 检查 Rust 扩展能否在容器运行目录中正常导入
- 写入固定版本标签 `0.6.7`

需要查看完整构建日志时使用：

```bash
docker build --progress=plain -t intellect-agent:0.6.7 .
```

需要完全忽略已有构建缓存时使用：

```bash
docker build --no-cache --progress=plain -t intellect-agent:0.6.7 .
```

正常更新源码时建议保留构建缓存，只有怀疑缓存层异常时才使用 `--no-cache`。

### 4.3 指定 Linux 架构

在 x86_64 Linux 上构建和部署时，默认命令即可。如果构建机和目标机架构不同，可以明确指定平台：

```bash
docker build --platform linux/amd64 -t intellect-agent:0.6.7 .
```

ARM64 可使用：

```bash
docker build --platform linux/arm64 -t intellect-agent:0.6.7 .
```

跨架构构建依赖 Docker BuildKit/QEMU，正式部署前应在目标架构上完成实际运行验证。

### 4.4 验证镜像

查看镜像：

```bash
docker image ls intellect-agent:0.6.7
```

检查固定版本标签：

```bash
docker image inspect intellect-agent:0.6.7 \
  --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'
```

预期输出：

```text
0.6.7
```

检查 CLI：

```bash
docker run --rm intellect-agent:0.6.7 intellect --version
```

检查 Rust 扩展：

```bash
docker run --rm intellect-agent:0.6.7 \
  python -c "import intellect_community_core; print('Rust extension OK')"
```

## 5. 将镜像传输到其他 Linux 服务器

如果构建和部署发生在同一台服务器，可以跳过本节。

在构建机导出镜像：

```bash
docker save -o intellect-agent-0.6.7.tar intellect-agent:0.6.7
```

将以下内容传输到目标 Linux 服务器：

- `intellect-agent-0.6.7.tar`
- `docker-compose.yml`
- 根据 `.env.example` 准备的 `.env`

目标服务器加载镜像：

```bash
docker load -i intellect-agent-0.6.7.tar
docker image ls intellect-agent:0.6.7
```

如果从 Windows 构建并导出镜像，也建议使用 `docker save -o`，避免 PowerShell 管道对二进制数据造成影响。

## 6. 配置部署环境

### 6.1 进入部署目录

```bash
cd /path/to/intellect-agent
```

首次部署先复制官方环境变量模板，并创建本地数据目录：

```bash
test -f .env || cp .env.example .env
mkdir -p intellect-config workspace
```

### 6.2 设置 Linux 用户 UID/GID

查询部署用户的 UID 和 GID：

```bash
id -u
id -g
```

将结果写入 `.env`：

```dotenv
INTELLECT_UID=1000
INTELLECT_GID=1000
```

如果实际结果不是 `1000:1000`，请按实际值修改。这样容器在 bind mount 中创建的文件会归属于正确的 Linux 用户。

首次部署可以修正数据目录所有权：

```bash
sudo chown -R "$(id -u):$(id -g)" intellect-config workspace
```

### 6.3 配置认证密钥

`.env` 中以下两个参数必须填写：

```dotenv
API_SERVER_KEY=
INTELLECT_WEBUI_PASSWORD=
```

可以生成随机 API Server Key：

```bash
openssl rand -hex 32
```

生成 WebUI 密码：

```bash
openssl rand -base64 24
```

将两个结果分别填入 `.env`。不要把 `.env` 提交到 Git、发送到聊天记录或打包进镜像。

建议限制 `.env` 权限：

```bash
chmod 600 .env
```

### 6.4 `.env` 参数说明

| 参数 | 推荐值 | 说明 |
|---|---|---|
| `WEBUI_BIND_HOST` | `0.0.0.0` | WebUI 对外监听地址；仅本机访问时改为 `127.0.0.1` |
| `WEBUI_PORT` | `9119` | WebUI 宿主机端口 |
| `API_SERVER_BIND_HOST` | `0.0.0.0` | API Server 对外监听地址；仅本机访问时改为 `127.0.0.1` |
| `API_SERVER_ENABLED` | `true` | 启用 OpenAI 兼容 API Server |
| `API_SERVER_HOST` | `0.0.0.0` | API Server 容器内监听地址，不要改成 `127.0.0.1` |
| `API_SERVER_PORT` | `8642` | API Server 宿主机和容器端口 |
| `API_SERVER_MODEL_NAME` | `intellect-agent` | `/v1/models` 返回的模型名称 |
| `API_SERVER_KEY` | 随机强密钥 | API Bearer Token，必须填写 |
| `API_SERVER_CORS_ORIGINS` | 可信前端地址 | 允许浏览器跨域调用 API 的来源列表 |
| `INTELLECT_UID` | `id -u` 结果 | 容器内 Intellect 运行用户 UID |
| `INTELLECT_GID` | `id -g` 结果 | 容器内 Intellect 运行用户 GID |
| `INTELLECT_WEBUI_HOST` | `0.0.0.0` | WebUI 容器内监听地址 |
| `INTELLECT_WEBUI_PORT` | `9119` | WebUI 容器内端口 |
| `INTELLECT_WEBUI_DEFAULT_WORKSPACE` | `/workspace` | WebUI 默认工作区，对应本地 `workspace` |
| `INTELLECT_WEBUI_PASSWORD` | 独立强密码 | WebUI 登录密码，必须填写 |

如果外部平台通过服务端调用 API，CORS 不参与服务端到服务端请求。如果浏览器直接调用 API，需要把平台页面的完整 Origin 加入允许列表，例如：

```dotenv
API_SERVER_CORS_ORIGINS=http://127.0.0.1:9119,http://localhost:9119,https://agent.example.com
```

不要在生产环境中使用 `*`。

### 6.5 验证 Compose 配置

在仓库根目录执行：

```bash
docker compose config
```

如果 `API_SERVER_KEY` 或 `INTELLECT_WEBUI_PASSWORD` 为空，Compose 会主动报错并拒绝启动，这是预期的安全保护。

确认 Compose 不包含构建配置和 named volume：

```bash
docker compose config | grep -E 'image:|build:|type:|source:|target:'
```

预期镜像为 `intellect-agent:0.6.7`，两个数据卷均应解析为本地 `bind` 类型。

## 7. 启动容器

在仓库根目录执行：

```bash
docker compose up -d
```

查看容器状态：

```bash
docker compose ps
```

持续查看启动日志：

```bash
docker compose logs -f --tail=200
```

首次启动会执行以下初始化工作：

- 创建 `/opt/data` 下的运行目录
- 根据 `INTELLECT_UID`、`INTELLECT_GID` 修正运行用户
- 初始化配置文件
- 同步镜像内置 Skill
- 启动 Gateway/API Server
- 启动 WebUI
- 同时检查 API Server 和 WebUI 健康状态

容器名称固定为：

```text
intellect-agent-0.6.7
```

## 8. 首次配置模型 Provider

容器正常启动后，执行交互式模型配置：

```bash
docker exec -it intellect-agent-0.6.7 intellect model
```

根据提示选择 DeepSeek、OpenAI、Anthropic、OpenRouter 或其他 Provider，并填写对应 API Key 和模型。

如果需要完整首次设置，也可以执行：

```bash
docker exec -it intellect-agent-0.6.7 intellect setup
```

模型配置会持久化到：

```text
intellect-config
```

配置完成后重启容器，让 Gateway 和 WebUI 重新加载配置：

```bash
docker compose restart
docker compose logs -f --tail=100
```

检查当前模型：

```bash
docker exec -it intellect-agent-0.6.7 intellect model
```

## 9. 功能验证

### 9.1 检查容器健康状态

```bash
docker compose ps
docker inspect intellect-agent-0.6.7 \
  --format '{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{end}}'
```

正常结果应为：

```text
running / healthy
```

### 9.2 验证 WebUI

浏览器访问：

```text
http://<服务器IP>:9119
```

使用 `.env` 中的 `INTELLECT_WEBUI_PASSWORD` 登录。

如果 WebUI 只绑定到 `127.0.0.1`，只能在服务器本机访问，或者通过 SSH 端口转发访问：

```bash
ssh -L 9119:127.0.0.1:9119 <用户>@<服务器IP>
```

然后在本地浏览器打开 `http://127.0.0.1:9119`。

### 9.3 验证 API Server 健康检查

```bash
curl -fsS http://127.0.0.1:8642/health
```

预期返回：

```json
{"status":"ok"}
```

详细健康信息：

```bash
curl -fsS http://127.0.0.1:8642/health/detailed \
  -H "Authorization: Bearer <API_SERVER_KEY>"
```

### 9.4 验证模型列表

```bash
curl -fsS http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer <API_SERVER_KEY>"
```

### 9.5 验证无上下文对话调用

每次请求不提供 `session_id`、`previous_response_id` 或历史消息，即可作为独立、无上下文请求：

```bash
curl -X POST http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer <API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "intellect-agent",
    "messages": [
      {"role": "user", "content": "请介绍当前工作目录中的文件。"}
    ],
    "stream": false
  }'
```

### 9.6 验证 Skill 列表和指定 Skill 调用

查询当前 API Server 可用的 Skill：

```bash
curl -fsS http://127.0.0.1:8642/v1/skills \
  -H "Authorization: Bearer <API_SERVER_KEY>"
```

指定一个已安装 Skill 创建 run：

```bash
curl -X POST http://127.0.0.1:8642/v1/runs \
  -H "Authorization: Bearer <API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "按照该 Skill 的规范处理当前任务。",
    "skill": "pdf"
  }'
```

将返回结果中的 `run_id` 用于查询执行状态：

```bash
curl -fsS http://127.0.0.1:8642/v1/runs/<run_id> \
  -H "Authorization: Bearer <API_SERVER_KEY>"
```

`skill` 必须来自 `/v1/skills` 返回的 `data[].name`，不能传入文件路径或任意提示词。

## 10. 日常运维命令

以下命令都应在仓库根目录执行。

查看状态：

```bash
docker compose ps
```

查看最近日志：

```bash
docker compose logs --tail=200
```

持续查看日志：

```bash
docker compose logs -f
```

重启：

```bash
docker compose restart
```

停止但保留容器：

```bash
docker compose stop
```

重新启动已停止容器：

```bash
docker compose start
```

停止并删除容器和 Compose 网络：

```bash
docker compose down
```

`docker compose down` 不会删除 `intellect-config` 和 `workspace`，因为它们是宿主机本地目录。

查看容器资源使用情况：

```bash
docker stats intellect-agent-0.6.7
```

进入容器排查：

```bash
docker exec -it intellect-agent-0.6.7 bash
```

## 11. 数据备份与恢复

### 11.1 备份

为避免备份过程中会话或配置仍在写入，先停止服务：

```bash
docker compose stop
tar -czf intellect-agent-data-$(date +%Y%m%d-%H%M%S).tar.gz intellect-config workspace
docker compose start
```

备份文件包含：

- Provider 和模型配置
- API 凭据文件
- Skill
- 会话和日志
- WebUI 状态
- Agent 工作区文件

备份文件可能包含密钥和用户数据，应加密保存并限制访问权限。

### 11.2 恢复

停止并删除旧容器：

```bash
docker compose down
```

将现有数据目录移到备份位置后，再解压指定备份：

```bash
mv intellect-config intellect-config.before-restore
mv workspace workspace.before-restore
tar -xzf intellect-agent-data-YYYYMMDD-HHMMSS.tar.gz
sudo chown -R "$(id -u):$(id -g)" intellect-config workspace
docker compose up -d
```

确认新数据恢复正常后，再按组织的数据保留策略处理两个 `.before-restore` 目录。

## 12. 使用新源码重建和更新容器

Compose 中镜像版本固定为 `intellect-agent:0.6.7`。如果仍以相同标签重新构建修复源码，需要强制重新创建容器，否则现有容器仍会引用旧镜像 ID。

更新前先备份当前镜像：

```bash
docker tag intellect-agent:0.6.7 intellect-agent:0.6.7-backup
```

回到仓库根目录重新构建：

```bash
cd /path/to/intellect-agent
docker build -t intellect-agent:0.6.7 .
```

在仓库根目录重建容器：

```bash
docker compose up -d --force-recreate
docker compose ps
docker compose logs --tail=200
```

如果新镜像异常，可以回滚：

```bash
docker compose down
docker tag intellect-agent:0.6.7-backup intellect-agent:0.6.7
docker compose up -d --force-recreate
```

数据目录与镜像相互独立，但跨版本升级前仍应备份 `intellect-config` 和 `workspace`。

## 13. 安全建议

- 必须设置高强度 `API_SERVER_KEY` 和 `INTELLECT_WEBUI_PASSWORD`。
- `.env` 权限建议设置为 `600`，并且不得提交到 Git。
- API Server 拥有终端、文件和 Agent 工具能力，不应无认证暴露到公网。
- `API_SERVER_CORS_ORIGINS` 只填写可信浏览器来源，不要使用 `*`。
- 如果不需要远程访问，将 `WEBUI_BIND_HOST` 和 `API_SERVER_BIND_HOST` 改为 `127.0.0.1`。
- 对公网部署时，应在 Nginx、Caddy 或其他反向代理层配置 HTTPS、访问控制和请求限制。
- 仅把需要 Agent 操作的文件放进 `workspace`，不要直接挂载宿主机根目录、用户主目录或敏感系统目录。
- 不建议挂载 `/var/run/docker.sock`；如确有需要，必须评估其接近宿主机 root 权限的安全风险。
- 定期备份 `intellect-config` 和 `workspace`，并按密钥材料的标准保护备份文件。

## 14. 防火墙和网络

默认对外端口：

| 端口 | 服务 |
|---|---|
| `9119/tcp` | WebUI |
| `8642/tcp` | OpenAI 兼容 API Server |

如果服务器使用 UFW，并且确实需要局域网或公网直接访问，可以按实际来源网段放行。以下示例仅供参考，应优先限制来源地址：

```bash
sudo ufw allow from 192.168.50.0/24 to any port 9119 proto tcp
sudo ufw allow from 192.168.50.0/24 to any port 8642 proto tcp
```

生产环境更推荐只让服务监听 `127.0.0.1`，再通过 HTTPS 反向代理对外提供服务。

## 15. 常见问题

### 15.1 `failed to read dockerfile: Dockerfile: no such file or directory`

原因：当前目录不是包含官方 `Dockerfile` 的仓库根目录。

解决：回到仓库根目录执行：

```bash
cd /path/to/intellect-agent
docker build -t intellect-agent:0.6.7 .
```

### 15.2 `pull access denied for intellect-agent`

原因：本机不存在 `intellect-agent:0.6.7`，Compose 又不包含 `build`。

解决：先手动构建镜像，或者使用 `docker load` 导入镜像，然后再执行 `docker compose up -d`。

### 15.3 Compose 提示必须设置 `API_SERVER_KEY`

原因：`.env` 中的 API Key 为空。

解决：生成随机密钥并填写 `.env`，然后重新运行 `docker compose config`。

### 15.4 容器状态为 `unhealthy`

查看日志和健康检查详情：

```bash
docker compose logs --tail=300
docker inspect intellect-agent-0.6.7 --format '{{json .State.Health}}'
```

健康检查要求 API Server 和 WebUI 同时正常。任意一个核心服务启动失败，容器都不会被认为是完整可用状态。

### 15.5 WebUI 显示 `No LLM provider configured`

原因：本地持久化目录尚未配置模型 Provider，或者启动后修改配置但 Gateway 尚未重新加载。

解决：

```bash
docker exec -it intellect-agent-0.6.7 intellect model
docker compose restart
```

### 15.6 返回 `401 Missing Authentication header`

原因：API 请求没有携带 Bearer Token，或者平台配置的 Key 与 `.env` 中的 `API_SERVER_KEY` 不一致。

正确请求头：

```text
Authorization: Bearer <API_SERVER_KEY>
```

### 15.7 数据目录出现 `Permission denied`

确认 `.env` 中的 UID/GID 与宿主机部署用户一致：

```bash
id -u
id -g
grep -E '^INTELLECT_(UID|GID)=' .env
```

停止容器并修正目录权限：

```bash
docker compose down
sudo chown -R "$(id -u):$(id -g)" intellect-config workspace
docker compose up -d
```

### 15.8 端口被占用

检查端口：

```bash
sudo ss -lntp | grep -E ':8642|:9119'
```

修改 `.env` 中的 `WEBUI_PORT` 或 `API_SERVER_PORT` 后重新创建容器：

```bash
docker compose up -d --force-recreate
```

### 15.9 构建期间下载失败

使用完整日志重新构建：

```bash
docker build --progress=plain -t intellect-agent:0.6.7 .
```

检查失败发生在基础镜像、APT、PyPI、npm、Rust crates、Playwright 还是 s6-overlay 下载阶段。国内源已经在 `Dockerfile` 中配置，但构建机仍需要可用的 DNS、HTTPS 出站网络和足够的超时时间。

### 15.10 页面仍显示旧版本或旧脚本

先确认容器已经使用新镜像重新创建：

```bash
docker compose up -d --force-recreate
docker inspect intellect-agent-0.6.7 --format '{{.Image}}'
docker image inspect intellect-agent:0.6.7 --format '{{.Id}}'
```

如果镜像 ID 一致但浏览器仍显示旧页面，请清理该站点缓存或使用强制刷新后重新访问。

## 16. 完整部署命令汇总

以下是首次部署的最短完整流程。

在仓库根目录构建：

```bash
cd /path/to/intellect-agent
docker build -t intellect-agent:0.6.7 .
```

在仓库根目录配置：

```bash
cd /path/to/intellect-agent
mkdir -p intellect-config workspace
cp .env.example .env
chmod 600 .env
vi .env
docker compose config
```

启动和观察：

```bash
docker compose up -d
docker compose ps
docker compose logs -f --tail=200
```

配置模型并重启：

```bash
docker exec -it intellect-agent-0.6.7 intellect model
docker compose restart
```

验证服务：

```bash
curl -fsS http://127.0.0.1:8642/health
curl -fsS http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer <API_SERVER_KEY>"
```

WebUI：

```text
http://<服务器IP>:9119
```
