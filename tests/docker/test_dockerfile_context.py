"""验证官方 Docker 构建文件已经直接包含本项目所需的容器修复。"""

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _project_version() -> str:
    """The single version source of truth: pyproject.toml."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


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

    # 生产部署要求镜像由用户显式提供；Compose 不得隐式使用另一套 Dockerfile。
    assert "build:" not in compose
    # 默认引用 CI 发布的多架构镜像，版本钉在与 pyproject 一致的 SemVer 上
    # （bump-version.sh 联动更新）；本地手动构建经 .env 的 INTELLECT_IMAGE 覆盖。
    version = _project_version()
    assert f"image: ${{INTELLECT_IMAGE:-ontoweb/intellect-agent:{version}}}" in compose
    assert 'command: ["/opt/intellect/docker/all-in-one.sh"]' in compose

    # 两个 bind mount 都使用官方仓库根目录下被忽略的运行目录，避免访问整个宿主机。
    assert "./intellect-config:/opt/data" in compose
    assert "./workspace:/workspace" in compose


def test_official_dockerfile_labels_are_build_arg_driven():
    """镜像版本标签必须由 build-arg 注入，禁止再次硬编码（曾漂移三个发布周期）。"""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    version = _project_version()

    # CI（docker-publish.yml）注入的三个版本 build-arg 必须被 Dockerfile 声明，
    # 否则会被静默丢弃——0.6.7 标签就是这么留在 0.7.x 源码树上的。
    for arg in ("ARG INTELLECT_VERSION=", "ARG INTELLECT_RUST_VERSION=", "ARG INTELLECT_RELEASE_TAG="):
        assert arg in dockerfile
    assert 'LABEL org.opencontainers.image.version="${INTELLECT_VERSION}"' in dockerfile
    assert 'LABEL io.intellect.release.tag="${INTELLECT_RELEASE_TAG}"' in dockerfile
    assert 'LABEL io.intellect.rust.version="${INTELLECT_RUST_VERSION}"' in dockerfile

    # ARG 默认值必须与版本树一致（本地无参数构建的兜底值）。
    assert f"ARG INTELLECT_VERSION={version}" in dockerfile
    assert f"ARG INTELLECT_RUST_VERSION={version}" in dockerfile
    assert 'LABEL org.opencontainers.image.version="0.' not in dockerfile


def test_docker_ci_trigger_paths_cover_image_content_carriers():
    """docker-publish 的触发面必须覆盖所有镜像内容载体（镜像以 COPY . . 收录全树）。

    缺口后果：rust-only / 前端-only 的提交合入 main 后 :main/:latest 静默过期
    （Rust 扩展仍是旧构建）。同时 tests/** 纯测试改动必须被负模式排除（镜像
    不运行测试，避免无意义的 5GB 构建），但 tests/docker 自身的集成测试改动
    必须重新纳入。GitHub paths 规则是"同一文件按最后匹配的模式生效"，故
    顺序契约固定为：广匹配 → '!tests/**' → 'tests/docker/**'。
    """
    import yaml

    wf_path = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    # YAML 1.1 把裸 `on:` 解析成布尔 True。
    on_block = wf.get(True, wf.get("on"))

    carriers = {
        "rust-core/**",
        "webui/**",
        "ui-tui/**",
        "skills/**",
        "optional-skills/**",
        ".env.example",
    }
    for event in ("push", "pull_request"):
        paths = on_block[event]["paths"]
        missing = carriers - set(paths)
        assert not missing, f"{event} paths missing carriers: {missing}"
        assert paths.index("!tests/**") > paths.index("**/*.py")
        assert paths.index("tests/docker/**") > paths.index("!tests/**")


def test_smoke_action_probes_rust_extension():
    """冒烟 action 必须包含 Rust 扩展导入探针。

    `intellect --help` 走 CLI 快速路径，打印 stub 即返回、不导入 agent 栈——
    缺失或架构不符的 intellect_community_core（v0.6.2 起强制依赖）在 --help
    上拦不住。导入探针是 arm64 镜像在推送前唯一的架构健全性门（完整集成
    测试仅 amd64 运行）。
    """
    action = (REPO_ROOT / ".github/actions/intellect-smoke-test/action.yml").read_text(
        encoding="utf-8"
    )
    assert "import intellect_community_core" in action
    assert "rust_core_version" in action
    # 探针是独立的 docker run 步骤（真实 ENTRYPOINT 路径），不是 --help 的替换。
    assert "Probe: Rust extension imports in-image" in action


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
