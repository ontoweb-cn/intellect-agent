#!/usr/bin/env bash
# 单容器启动器：在同一个容器内同时运行 Gateway/API Server 与 WebUI。
#
# s6-overlay 的 /init 仍然是 PID 1，负责转发终止信号和回收孤儿进程；本脚本作为容器主程序，
# 负责维护两个同等重要的业务子进程。任意一个子进程异常退出时，脚本会停止另一个子进程并
# 让容器退出，从而交给 Docker 的 restart 策略整体重启，避免出现“容器健康但只剩半套服务”的状态。
set -Eeuo pipefail

gateway_pid=''
webui_pid=''
shutdown_started=0

stop_children() {
    # trap 可能被多个信号或退出路径重复触发，使用标记保证终止流程只执行一次。
    if [[ "${shutdown_started}" -eq 1 ]]; then
        return
    fi
    shutdown_started=1

    # 只向仍然存活的业务进程发送 TERM，让 Gateway 与 WebUI 有机会保存会话并关闭监听端口。
    if [[ -n "${gateway_pid}" ]] && kill -0 "${gateway_pid}" 2>/dev/null; then
        kill -TERM "${gateway_pid}" 2>/dev/null || true
    fi
    if [[ -n "${webui_pid}" ]] && kill -0 "${webui_pid}" 2>/dev/null; then
        kill -TERM "${webui_pid}" 2>/dev/null || true
    fi

    # wait 会回收两个直接子进程；退出码在主等待逻辑中处理，这里不让二次 wait 阻断清理。
    [[ -z "${gateway_pid}" ]] || wait "${gateway_pid}" 2>/dev/null || true
    [[ -z "${webui_pid}" ]] || wait "${webui_pid}" 2>/dev/null || true
}

trap 'stop_children; exit 143' TERM
trap 'stop_children; exit 130' INT

# Gateway 是 intellect CLI 的子命令，不是独立的 gateway 可执行文件；必须通过 CLI 启动。
# 原双容器方案由 main-wrapper 自动完成该路由，单容器启动器需要显式写出完整命令。
intellect gateway run &
gateway_pid=$!

# 直接以前台模块方式启动 WebUI，避免使用会派生后台进程后立即返回的 CLI 包装命令。
python -m webui.server &
webui_pid=$!

# 任意一个核心服务退出都视为整个单容器服务失效；记录其退出码，完成成对清理后原样返回。
set +e
wait -n "${gateway_pid}" "${webui_pid}"
exit_code=$?
set -e

stop_children
exit "${exit_code}"
