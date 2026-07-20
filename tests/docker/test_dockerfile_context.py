"""验证官方 Docker 构建文件已经直接包含本项目所需的容器修复。"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_dockerignore_keeps_state_python_package():
    """``state/`` 是被运行时直接导入的源码包，不能作为状态数据排除。"""
    active_patterns = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    # ``intellect_state.py`` 会导入 state.schema/state.fts/state.compression；
    # 如果构建上下文排除该目录，Gateway 会在 API Server 初始化时直接退出。
    assert "state" not in active_patterns
    assert "/state" not in active_patterns
    assert (REPO_ROOT / "state" / "__init__.py").is_file()


def test_official_dockerfile_normalizes_docker_exec_shim_line_endings():
    """最终镜像必须移除 ``intellect`` 快捷入口中的 Windows CRLF 行尾。"""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    normalization_start = dockerfile.index("RUN find /opt/intellect/docker")
    normalization_end = dockerfile.index("\n\n", normalization_start)
    normalization_step = dockerfile[normalization_start:normalization_end]

    # ``COPY`` 会原样保留 Windows 工作区中的 CRLF；如果快捷入口没有进入最终的
    # ``sed`` 规范化步骤，其 shebang 会被 Linux 解释为不存在的 ``/bin/sh\r``，
    # 最终导致 ``docker exec <container> intellect model`` 无法启动。
    assert "/opt/intellect/bin/intellect" in normalization_step


def test_official_dockerfile_installs_uv_from_domestic_python_index():
    """uv 不应再依赖会把 blob 重定向到不可达对象存储的 DaoCloud 镜像。"""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    # DaoCloud 的镜像 manifest 可以正常解析，但 uv 镜像层可能被重定向到
    # image-mirror.r2.daocloud.vip。Docker Desktop 无法访问该对象存储时，
    # ``COPY --from=uv_source`` 会在真正复制二进制前失败，因此必须彻底移除
    # 这个外部 stage，而不是只在 COPY 指令周围增加无效重试。
    assert " AS uv_source" not in dockerfile
    assert "--from=uv_source" not in dockerfile

    # Debian 运行层已经统一配置清华 PyPI；固定 uv 版本从该源安装，可以复用
    # APT/PyPI 的国内网络链路，同时确保后续 uv sync 命令仍位于全局 PATH。
    assert "python3-pip" in dockerfile
    assert "ARG UV_VERSION=0.11.6" in dockerfile
    assert "python3 -m pip install --break-system-packages" in dockerfile
    assert '--index-url "${UV_DEFAULT_INDEX}"' in dockerfile
    assert '"uv==${UV_VERSION}"' in dockerfile


def test_build_uses_only_official_docker_files():
    """仓库不得再保留与官方构建文件并行的 ``.fix`` 版本。"""
    assert not (REPO_ROOT / "Dockerfile.fix").exists()
    assert not (REPO_ROOT / "Dockerfile.fix.dockerignore").exists()
    assert not (REPO_ROOT / "docker-fix").exists()


def test_official_compose_orchestrates_complete_single_container_without_build():
    """官方根 Compose 只编排固定镜像，并同时挂载配置和受限工作区。"""
    compose = DOCKER_COMPOSE.read_text(encoding="utf-8")

    # 生产部署要求镜像由用户显式构建；Compose 不得隐式使用另一套 Dockerfile。
    assert "build:" not in compose
    assert "image: intellect-agent:0.6.7" in compose
    assert 'command: ["/opt/intellect/docker/all-in-one.sh"]' in compose

    # 两个 bind mount 都使用官方仓库根目录下被忽略的运行目录，避免访问整个宿主机。
    assert "./intellect-config:/opt/data" in compose
    assert "./workspace:/workspace" in compose


def test_official_environment_template_covers_single_container_required_values():
    """官方环境变量模板必须包含 API Server 与 WebUI 的必填部署参数。"""
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    for key in (
        "API_SERVER_KEY=",
        "INTELLECT_WEBUI_PASSWORD=",
        "API_SERVER_PORT=8642",
        "INTELLECT_WEBUI_PORT=9119",
        "INTELLECT_WEBUI_DEFAULT_WORKSPACE=/workspace",
    ):
        assert key in env_example
