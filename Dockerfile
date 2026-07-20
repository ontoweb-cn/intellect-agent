# 直接从官方 Dockerfile 构建 Agent、Gateway、API Server 和 WebUI 共用的完整功能镜像。
# 本文件保留官方完整依赖与 Rust 扩展构建流程，并合入国内网络和跨平台容器运行修复。
# Node 22 LTS source stage. Debian trixie's bundled nodejs is pinned to 20.x
# which reached EOL in April 2026 — we copy node + npm + corepack from the
# upstream node:22 image instead so we can stay on a supported LTS without
# waiting for Debian 14 (forky, ~mid-2027).  Bookworm-based slim image used
# so the produced binary links against glibc 2.36, which runs cleanly on
# our Debian 13 (trixie, glibc 2.41) runtime.  Bumping to a new Node major
# is a one-line ARG change; see #4977.
# Node 与 Debian 使用 DaoCloud 的上游镜像前缀；Node 继续通过官方 SHA256 锁定内容。
FROM m.daocloud.io/docker.io/library/node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732 AS node_source
FROM m.daocloud.io/docker.io/library/debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1

# Store Playwright browsers outside the volume mount so the build-time
# install survives the /opt/data volume overlay at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/intellect/.playwright

# 构建期统一使用国内软件源：npm/Playwright 使用 npmmirror，Python 使用清华 PyPI，Rust 使用 RSProxy sparse 索引。
# 锁文件和固定版本仍决定最终依赖内容；这里只替换下载入口，不放宽版本约束，也不关闭完整性校验。
ENV npm_config_registry=https://registry.npmmirror.com \
    npm_config_replace_registry_host=always \
    PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    CARGO_REGISTRIES_CRATES_IO_INDEX=sparse+https://rsproxy.cn/index/

# Install system dependencies in one layer, clear APT cache.
# tini was previously PID 1 to reap orphaned zombie processes (MCP stdio
# subprocesses, git, bun, etc.) that would otherwise accumulate when intellect
# ran as PID 1. See #15012. Phase 2 of the s6-overlay supervision plan
# replaces tini with s6-overlay's /init (PID 1 = s6-svscan), which reaps
# zombies non-blockingly on SIGCHLD and additionally supervises the main
# intellect process and per-profile gateways.
# Debian 13 默认使用 deb822 格式的 debian.sources；先替换为清华镜像，再执行更新和安装。
# 基础层尚未包含 CA 根证书，因此 APT 引导阶段使用 HTTP；APT 仍会校验 Release 签名和软件包哈希。
RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|http://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        -e 's|http://deb.debian.org/debian|http://mirrors.tuna.tsinghua.edu.cn/debian|g' \
        /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "60";\nAcquire::https::Timeout "60";\n' \
        > /etc/apt/apt.conf.d/80-intellect-mirror-retries && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates curl iputils-ping python3 python3-pip python-is-python3 ripgrep ffmpeg \
    gcc python3-dev libffi-dev procps git openssh-client docker-cli xz-utils \
    cargo rustc pkg-config && \
    sed -i 's|http://mirrors.tuna.tsinghua.edu.cn|https://mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources && \
    rm -rf /var/lib/apt/lists/*

# ---------- s6-overlay install ----------
# s6-overlay provides supervision for the main intellect process
# and per-profile gateways. /init becomes PID 1 below — see ENTRYPOINT.
#
# Multi-arch: BuildKit auto-populates TARGETARCH (amd64 / arm64). s6-overlay
# uses tarball names keyed on the kernel arch string (x86_64 / aarch64), so
# we map between them inline. The noarch + symlinks tarballs are
# architecture-independent and reused as-is.
#
# We use `curl` instead of `ADD` for the per-arch tarball because `ADD`
# evaluates its URL at parse time, before any ARG / TARGETARCH substitution
# — splitting one URL per arch into two ADDs would download both on every
# build and leave dead bytes in the cache. A single curl + arch-keyed URL
# is simpler and cache-friendlier.
#
# Supply-chain integrity: every tarball is checksum-verified against the
# upstream-published SHA256. To bump S6_OVERLAY_VERSION, fetch the four
# `.sha256` files from the corresponding release and update the ARGs. The
# checksum lookup happens during build, so a compromised release artifact
# fails the build loudly instead of silently producing a tampered image.
ARG TARGETARCH
ARG S6_OVERLAY_VERSION=3.2.3.0
ARG S6_OVERLAY_NOARCH_SHA256=b720f9d9340efc8bb07528b9743813c836e4b02f8693d90241f047998b4c53cf
ARG S6_OVERLAY_X86_64_SHA256=a93f02882c6ed46b21e7adb5c0add86154f01236c93cd82c7d682722e8840563
ARG S6_OVERLAY_AARCH64_SHA256=0952056ff913482163cc30e35b2e944b507ba1025d78f5becbb89367bf344581
ARG S6_OVERLAY_SYMLINKS_SHA256=a60dc5235de3ecbcf874b9c1f18d73263ab99b289b9329aa950e8729c4789f0e
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-symlinks-noarch.tar.xz /tmp/
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
        amd64) s6_arch="x86_64"; s6_arch_sha="${S6_OVERLAY_X86_64_SHA256}" ;; \
        arm64) s6_arch="aarch64"; s6_arch_sha="${S6_OVERLAY_AARCH64_SHA256}" ;; \
        *) echo "Unsupported TARGETARCH=${TARGETARCH} for s6-overlay" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/s6-overlay-arch.tar.xz \
        "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${s6_arch}.tar.xz"; \
    { \
        printf '%s  %s\n' "${S6_OVERLAY_NOARCH_SHA256}" /tmp/s6-overlay-noarch.tar.xz; \
        printf '%s  %s\n' "${s6_arch_sha}" /tmp/s6-overlay-arch.tar.xz; \
        printf '%s  %s\n' "${S6_OVERLAY_SYMLINKS_SHA256}" /tmp/s6-overlay-symlinks-noarch.tar.xz; \
    } > /tmp/s6-overlay.sha256; \
    sha256sum -c /tmp/s6-overlay.sha256; \
    tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz; \
    tar -C / -Jxpf /tmp/s6-overlay-arch.tar.xz; \
    tar -C / -Jxpf /tmp/s6-overlay-symlinks-noarch.tar.xz; \
    rm /tmp/s6-overlay-*.tar.xz /tmp/s6-overlay.sha256

# Non-root user for runtime; UID can be overridden via INTELLECT_UID at runtime
# 修正官方路径中的 /opt/datan 拼写错误，使用户主目录与持久化目录 /opt/data 保持一致。
# 依赖 HOME 解析配置路径的组件会因此稳定读取模型、凭据和会话数据。
RUN useradd -u 10000 -m -d /opt/data intellect

# 官方方式从 ghcr.io 的独立镜像复制 uv；部分国内 Docker Desktop 环境会在
# 对象存储 Blob 跳转阶段失败。这里改为从已配置的清华 PyPI 安装固定版本，
# 并仅对容器镜像的系统 Python 使用 PEP 668 所需的 --break-system-packages。
ARG UV_VERSION=0.11.6
RUN python3 -m pip install --break-system-packages \
        --no-cache-dir \
        --index-url "${UV_DEFAULT_INDEX}" \
        "uv==${UV_VERSION}" && \
    uv --version && \
    uvx --version

# Node 22 LTS: copy the node binary plus the bundled npm + corepack JS
# installs from the upstream image.  npm and npx are recreated as symlinks
# because they're symlinks in the source image (and need to live on PATH).
# See node_source stage at the top of the file for the version-bump
# rationale (#4977).
COPY --chmod=0755 --from=node_source /usr/local/bin/node /usr/local/bin/
COPY --from=node_source /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=node_source /usr/local/lib/node_modules/corepack /usr/local/lib/node_modules/corepack
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    ln -sf /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

WORKDIR /opt/intellect

# ---------- Layer-cached dependency install ----------
# Copy only package manifests first so npm install + Playwright are cached
# unless the lockfiles themselves change.
#
# ui-tui/packages/intellect-ink/ is copied IN FULL (not just its manifests)
# because it is referenced as a `file:` workspace dependency from
# ui-tui/package.json.  Copying the tree up front lets npm resolve the
# workspace to real content instead of stopping at a bare package.json.
COPY package.json package-lock.json ./
COPY ui-tui/package.json ui-tui/package-lock.json ui-tui/
COPY ui-tui/packages/intellect-ink/ ui-tui/packages/intellect-ink/

# `npm_config_install_links=false` forces npm to install `file:` deps as
# symlinks instead of copies.  This is the default since npm 10+, which is
# what the image ships now (via the node:22 source stage).  We set it
# explicitly anyway as defense-in-depth: the previous Debian-bundled npm
# 9.x defaulted to install-as-copy, which produced a hidden
# node_modules/.package-lock.json that permanently disagreed with the root
# lock on the @intellect/ink entry, tripped the TUI launcher's
# `_tui_need_npm_install()` check on every startup, and triggered a
# runtime `npm install` that then failed with EACCES.  Keeping the env
# guards against a future regression if the source npm version changes.
ENV npm_config_install_links=false

RUN npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    (cd ui-tui && npm install --prefer-offline --no-audit) && \
    npm cache clean --force

# ---------- Layer-cached Python dependency install ----------
# Copy only pyproject.toml + uv.lock so the Python dep resolve + wheel
# download + native-extension compile layer is cached unless those inputs
# change.  Before this split the Python install sat after `COPY . .`, so
# every source-only commit re-did ~4-5 min of dep work on cold builds.
#
# README.md is referenced by pyproject.toml's `readme =` field, but it's
# excluded from the build context by .dockerignore's `*.md`.  uv's build
# frontend stats the readme path during dep resolution, so we `touch` an
# empty placeholder — the real README is restored by `COPY . .` below.
#
# `uv sync --frozen --no-install-project --extra all --extra messaging`
# installs the deps reachable through the composite `[all]` extra
# (handpicked set intended for the production image), plus gateway
# messaging adapters that should work in the published image without a
# first-boot lazy install.  We do NOT use `--all-extras`:
# that would pull in `[rl]` (atroposlib + tinker + torch + wandb from
# git), `[yc-bench]` (another git dep), and `[termux-all]` (Android
# redundancy), none of which belong in the published container.
#
# Provider packages (anthropic, bedrock, azure-identity) are included
# so Docker users can use these providers without requiring runtime
# lazy-install access to PyPI (often blocked in containerized envs).
#
# The editable link is created after the source copy below.
COPY pyproject.toml uv.lock ./
RUN touch ./README.md
# 既有锁文件记录了官方 PyPI 文件来源；先只在镜像层内重新解析为清华镜像地址，
# 再通过 frozen sync 按锁定版本安装，仓库中的 uv.lock 不会被改写。
# websockets 15.0.1 已在锁文件中，但生产 extras 不一定选入；浏览器 CDP 工具需要它。
RUN UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=8 \
    uv lock --default-index "${UV_DEFAULT_INDEX}" && \
    UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=8 \
    uv sync --frozen --no-install-project --extra all --extra messaging --extra anthropic --extra bedrock --extra azure-identity && \
    UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=8 \
    uv pip install --no-cache-dir websockets==15.0.1

# ---------- Source code ----------
# .dockerignore excludes node_modules, so the installs above survive.
COPY --chown=intellect:intellect . .

# 在构建阶段校验两个已修复的 WebUI 脚本；若后续同步官方代码时重新引入语法错误，镜像会立即构建失败。
RUN node --check webui/static/ui.js && \
    node --check webui/static/pwa-startup.js && \
    chmod 0755 docker/all-in-one.sh

# Build terminal UI assets.
RUN cd ui-tui && npm run build

# Rust extension (required since v0.6.2 — intellect_community_core).
# PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 is needed because PyO3 0.21.x
# does not support Python 3.13; the flag tells PyO3 to use the stable
# ABI (abi3) instead of version-specific internals.
# 源码中的 intellect_community_core 是源码运行占位包，镜像必须使用 maturin 生成的 Linux 扩展包。
# 显式删除占位目录可以避免它遮蔽虚拟环境中的二进制模块。
RUN rm -rf /opt/intellect/intellect_community_core && \
    mkdir -p /root/.cargo && \
    printf '[source.crates-io]\nreplace-with = "rsproxy-sparse"\n\n[source.rsproxy-sparse]\nregistry = "sparse+https://rsproxy.cn/index/"\n' \
        > /root/.cargo/config.toml && \
    . /opt/intellect/.venv/bin/activate && \
    uv pip install --no-cache-dir maturin && \
    export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 && \
    cd rust-core && maturin develop --release

# ---------- Permissions ----------
# Make install dir world-readable so any INTELLECT_UID can read it at runtime.
# The venv needs to be traversable too.
# node_modules trees additionally need to be writable by the intellect user
# so the runtime `npm install` triggered by _tui_need_npm_install() in
# intellect_cli/main.py succeeds (see #18800).
# The .venv MUST remain intellect-writable so lazy_deps.py can install
# remaining optional platform packages and future pin bumps at first use.
# Without this, `uv pip install` fails with EACCES and adapters silently
# fail to load.  See tools/lazy_deps.py.
USER root
RUN chmod -R a+rX /opt/intellect && \
    chown -R intellect:intellect /opt/intellect/.venv /opt/intellect/ui-tui /opt/intellect/node_modules
# Start as root so the s6-overlay stage2 hook can usermod/groupmod and chown
# the data volume. Each supervised service then drops to the intellect user via
# `s6-setuidgid intellect` in its run script. If INTELLECT_UID is unset, services
# run as the default intellect user (UID 10000).

# ---------- Link intellect-agent itself (editable) ----------
# Deps are already installed in the cached layer above; `--no-deps` makes
# this a fast (~1s) egg-link creation with no resolution or downloads.
RUN uv pip install --no-cache-dir --no-deps -e "."

# 从实际运行工作目录执行导入，防止“Rust 编译成功但运行期被占位包遮蔽”的问题进入交付镜像。
RUN cd /opt/data && /opt/intellect/.venv/bin/python -c "import intellect_community_core, intellect_community_core.intellect_community_core; print('Rust extension import OK')"

# ---------- Bake build-time git revision ----------
# .dockerignore excludes .git, so `git rev-parse HEAD` from inside the
# container always returns nothing — meaning `intellect dump` reports
# "(unknown)" and the startup banner drops its `· upstream <sha>` suffix.
# That makes support triage from container bug reports impossible:
# we can't tell which commit the user is actually running.
#
# Fix: write the commit SHA passed via the INTELLECT_GIT_SHA build-arg to
# /opt/intellect/.intellect_build_sha at build time, and have
# intellect_cli/build_info.py read it at runtime.  Both `intellect dump` and
# banner.get_git_banner_state() try the baked SHA first, then fall back
# to live `git rev-parse` for source installs (unchanged behaviour).
#
# The arg is optional — local `docker build` without --build-arg simply
# omits the file, and the runtime falls back to live-git lookup.  CI
# (.github/workflows/docker-publish.yml) passes ${{ github.sha }} so
# every published image has it.
ARG INTELLECT_GIT_SHA=
RUN if [ -n "${INTELLECT_GIT_SHA}" ]; then \
        printf '%s\n' "${INTELLECT_GIT_SHA}" > /opt/intellect/.intellect_build_sha && \
        chown intellect:intellect /opt/intellect/.intellect_build_sha; \
    fi
# 本地部署要求镜像版本固定为 0.6.7，使手动构建结果与 Compose 引用保持一致。
LABEL org.opencontainers.image.version="0.6.7"
LABEL io.intellect.release.tag="intellect-agent-0.6.7"
LABEL io.intellect.rust.version="0.1.0"

# ---------- s6-overlay service wiring ----------
# Static service declared at build time: main-intellect.
# Per-profile gateway services are registered dynamically at runtime by
# the profile create/delete hooks (Phase 4); they live under
# /run/service/ (tmpfs) and are reconciled on container restart by
# /etc/cont-init.d/02-reconcile-profiles (Phase 4 Task 4.0).
COPY docker/s6-rc.d/ /etc/s6-overlay/s6-rc.d/

# stage2-hook handles UID/GID remap, volume chown, config seeding,
# skills sync — all the work the old entrypoint.sh did before
# `exec intellect`. Wired in as cont-init.d/01- so it
# runs before user services start.
#
# 02-reconcile-profiles re-creates per-profile gateway s6 service
# slots from $INTELLECT_HOME/profiles/<name>/ after a container restart
# (the /run/service/ scandir is tmpfs and wiped on restart). Phase 4.
RUN mkdir -p /etc/cont-init.d && \
    printf '#!/command/with-contenv sh\nexec /opt/intellect/docker/stage2-hook.sh\n' \
        > /etc/cont-init.d/01-intellect-setup && \
    chmod +x /etc/cont-init.d/01-intellect-setup
COPY --chmod=0755 docker/cont-init.d/015-supervise-perms /etc/cont-init.d/015-supervise-perms
COPY --chmod=0755 docker/cont-init.d/02-reconcile-profiles /etc/cont-init.d/02-reconcile-profiles

# ---------- Runtime ----------
ENV INTELLECT_HOME=/opt/data

# `docker exec` privilege-drop shim. When operators run
# `docker exec <c> intellect ...` they default to root, and any file the
# command writes under $INTELLECT_HOME (auth.json, .env, config.yaml) ends
# up root-owned and unreadable to the supervised gateway (UID 10000).
# The shim lives at /opt/intellect/bin/intellect, sits earliest on PATH, and
# transparently re-exec's the real venv binary via `s6-setuidgid intellect`
# when invoked as root. Non-root callers (supervised processes,
# `--user intellect`, etc.) hit the short-circuit path with no overhead.
# Recursion is impossible because the shim exec's the venv binary by
# absolute path (/opt/intellect/.venv/bin/intellect). See the shim source for
# the opt-out env var (INTELLECT_DOCKER_EXEC_AS_ROOT=1).
COPY --chmod=0755 docker/intellect-exec-shim.sh /opt/intellect/bin/intellect

# Windows Git 工作区可能把 shell/s6 文件检出为 CRLF，造成 shebang 指向 /bin/sh\r
# 或 s6 把 longrun\r 当作非法服务类型。镜像内统一移除行尾 CR 并恢复执行权限。
RUN find /opt/intellect/docker /opt/intellect/bin/intellect /etc/s6-overlay/s6-rc.d /etc/cont-init.d -type f \
        -exec sed -i 's/\r$//' {} + && \
    chmod 0755 /opt/intellect/docker/main-wrapper.sh \
        /opt/intellect/docker/stage2-hook.sh \
        /opt/intellect/docker/all-in-one.sh \
        /etc/s6-overlay/s6-rc.d/main-intellect/run \
        /etc/cont-init.d/01-intellect-setup \
        /etc/cont-init.d/015-supervise-perms \
        /etc/cont-init.d/02-reconcile-profiles

# Pre-s6 entrypoint.sh did `source .venv/bin/activate` which exported
# the venv bin onto PATH; Architecture B's main-wrapper.sh does the
# same for the container's main process, but `docker exec` and our
# cont-init.d scripts don't pass through the wrapper. Expose the venv
# bin globally so `docker exec <container> intellect ...` and any
# subprocess that doesn't activate the venv first still find intellect.
#
# /opt/intellect/bin is prepended ahead of the venv so the privilege-drop
# shim wins PATH resolution. The shim's last act is to exec the venv
# binary by absolute path, so this PATH ordering is transparent to
# every other consumer.
ENV PATH="/opt/intellect/bin:/opt/intellect/.venv/bin:/opt/data/.local/bin:${PATH}"
RUN mkdir -p /opt/data
VOLUME [ "/opt/data" ]

# s6-overlay's /init is PID 1. It sets up the supervision tree, runs
# /etc/cont-init.d/* (our stage2 hook), starts s6-rc services
# declared in /etc/s6-overlay/s6-rc.d/, then exec's its remaining
# argv as the container's "main program" with stdin/stdout/stderr
# inherited (this is what makes interactive --tui work). When the
# main program exits, /init begins stage 3 shutdown and the container
# exits with the program's exit code. Replaces tini — see Phase 2 of
# docs/plans/2026-05-07-s6-overlay-dynamic-subagent-gateways.md.
#
# We use the ENTRYPOINT+CMD split rather than CMD alone so the
# wrapper is prepended to user-supplied args automatically:
#
#   docker run <image>                  → /init main-wrapper.sh   (CMD default)
#   docker run <image> chat -q "hi"     → /init main-wrapper.sh chat -q hi
#   docker run <image> sleep infinity   → /init main-wrapper.sh sleep infinity
#   docker run <image> --tui            → /init main-wrapper.sh --tui
#
# main-wrapper.sh handles arg routing (bare-exec vs. intellect
# subcommand vs. no-args), drops to the intellect user via s6-setuidgid,
# and exec's the final program so its exit code becomes the container
# exit code. Without the wrapper-as-ENTRYPOINT, leading-dash args
# like `--version` would be intercepted by /init's POSIX shell.
ENTRYPOINT [ "/init", "/opt/intellect/docker/main-wrapper.sh" ]
# 单镜像、单容器同时提供 Agent/Gateway/API Server 与 WebUI 两个服务端口。
EXPOSE 8642 9119

# 保持镜像为通用 Intellect 镜像；根目录 Compose 会传入单容器启动器作为前台命令。
CMD [ ]
