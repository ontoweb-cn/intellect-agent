# WebUI 崩溃事故复盘：进程级 stdout 竞态（2026-10-12）

> 供 Intellect 企业版团队参考。对应修复已随本仓库落地，企业版合并时请对照
> 「四、修复清单」逐项核对代码位置；「六、企业版适配建议」给出移植与排查要点。

---

## 一、事故概述

| 项 | 内容 |
|---|---|
| 时间 | 2026-10-12 13:35 – 13:58（本地时间） |
| 影响面 | WebUI 全部页面不可用（503 / 连接重置），访问日志断流 23 分钟；期间两次重启失败，故障窗口拉长至 ~25 分钟 |
| 触发条件 | WebUI 进程内**多个 agent 会话并发执行工具**（execute_code RPC / background review） |
| 恢复方式 | `intellect webui restart` 杀掉僵死进程后重启成功 |
| 严重级别 | 高（单进程多会话部署形态下必然可复现的并发缺陷） |

### 现象时间线

| 时间 | 事件 |
|---|---|
| 10-11 23:00 | WebUI server 启动（进程内嵌完整 agent 运行时），随后长时间承载多会话、大量工具调用 |
| 10-12 13:25 | 前兆：deepseek 凭据池写 SQLite 报 `disk I/O error`（fd/流异常的早期症状） |
| **13:35:02** | 最后一条访问日志。窗口内一个后台 review 线程（13:35:06 结束）与并发工具线程完成了一次竞态：**进程级 `sys.stdout` 被永久替换为一个已关闭的 devnull** |
| 13:49–13:51 | 用户刷新页面，每个请求都触发故障路径，errors.log 记录 56 条 `Failed to serve WebUI shell route: I/O operation on closed file`；页面拿到 503 "Intellect is restarting" 或连接重置 |
| 13:50 / 13:54 | 两次重启尝试均 `OSError: [Errno 98] Address already in use` —— 旧进程僵而不死，监听 socket 未释放，新进程 bind 失败崩溃 |
| 13:58 | `intellect webui restart` 先杀旧进程再启动，服务恢复 |

---

## 二、根因分析

### 2.1 直接根因：进程级 stdio 替换的跨线程竞态

崩溃前代码在**三个位置**对 `sys.stdout` 做进程级全局替换：

1. `tools/code_execution_tool.py` —— execute_code 沙箱的 RPC handler 线程在分发工具调用时执行 `sys.stdout = devnull`（两处：UDS 路径与远程文件 RPC 路径），**运行在主进程的监听线程里**；
2. `agent/background_review.py` —— 后台自查线程用 `contextlib.redirect_stdout(devnull)` 包裹 fork agent 的初始化与运行；
3. `agent/curator.py` —— curator review 线程同模式。

`contextlib.redirect_stdout` 与裸赋值一样是**进程级全局**操作（保存当前 `sys.stdout` → 替换 → 恢复保存值）。两个线程并发交错时：

```
线程 A（background review）          线程 B（execute_code RPC）
──────────────────────────          ──────────────────────────
A.save = sys.stdout（真 stdout）
sys.stdout = devnull_A
                                     B.save = sys.stdout（= devnull_A ❌）
                                     sys.stdout = devnull_B
A 恢复: sys.stdout = A.save（真）
  with 退出 → devnull_A.close()
                                     B 恢复: sys.stdout = B.save（= devnull_A，已关闭 ❌❌）
```

此后进程内**所有** `print()` 抛 `ValueError: I/O operation on closed file`，
且这一状态不可自愈——直到进程被杀。

### 2.2 故障放大器：响应路径依赖 stdout

WebUI 每个响应的第一步是写访问日志：`Handler.send_response()` → `log_request()` → `print(..., flush=True)`（`webui/server.py`）。stdout 死亡后：

- **所有**响应在 `send_response` 内部抛 ValueError；
- shell 页面路由的 `except` 捕获后走 503 兜底页（`_serve_shell_unavailable`，`webui/api/routes.py`），但兜底页同样要先 `send_response` → **再次抛出同一异常** → 连 503 都发不出去，浏览器直接断连；
- 访问日志 13:35 起完全断流（进程活着但一行都写不出），与错误风暴的时间窗口互为印证。

单点依赖（stdout）+ 每请求必经 = 一处流损坏 → 100% 请求失败。

### 2.3 次生灾害：僵尸进程阻塞端口

主线程的 accept 循环虽已无法正常服务，但进程未退出、监听 socket 未释放。
重启尝试 bind 失败（`Address already in use`），新进程崩溃退出，形成死锁：
**僵死进程比崩溃进程的代价更高**——它阻塞了所有自动/手动恢复手段。

### 2.4 为什么此前未暴露

- 需要**同一进程内 ≥2 个并发重定向线程**：CLI 单会话场景窗口极小；WebUI/gateway 多会话长时运行场景几乎必然命中（本次进程已运行 ~15 小时、250+ 条消息的会话、450+ 轮 API 调用）；
- 竞态命中后进程不一定立刻死：stdout 被换成 devnull（未关闭）时表现为**静默丢日志**；换成已关闭对象时才全面爆发——中间态难以告警。

---

## 三、修复设计（三层防御）

| 层 | 思路 | 效果 |
|---|---|---|
| L1 根治竞态 | 全局替换 → **线程局部重定向** | 消灭根因：并发工具分发不再污染全局状态 |
| L2 解耦日志 | 访问日志走 logging FileHandler，stdout 仅 best-effort 镜像，任何写入失败不进响应路径 | 即使流再次损坏，也不可伤及请求处理 |
| L3 僵死自愈 | liveness watchdog 真实 HTTP 自探测 `/health`，连续失败即退出释放端口 | 即使进程僵死，端口自动可回收，重启不再被阻塞 |

---

## 四、修复清单（企业版合并核对用）

### 新增文件

| 文件 | 内容 |
|---|---|
| `agent/stream_guard.py` | `GuardedStream`（stdio 稳定代理：线程局部 override、`close()` 不落到底层流、底层流死亡时写入静默）；`install_stream_guard()`（幂等安装）；`redirect_thread_output(stream)`（线程局部重定向 contextmanager）；`NULL_STREAM`（无 fd 的丢弃流，替代 `open(os.devnull)`，消除"可被竞态关闭的对象"） |
| `tests/agent/test_stream_guard.py` | 12 个用例，核心是 **8 线程 × 50 轮并发重定向的竞态回归测试**（旧代码在该测试下会把全局 stdout 变成已关闭流） |
| `tests/webui/test_server_resilience.py` | 8 个用例：closed stdout 下 `emit_access_record` 不抛且文件日志仍落盘；真实起服后 `/health` 探测与访问日志落盘的端到端验证；watchdog 探测目标解析与禁用开关 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `tools/code_execution_tool.py:553, 821` | 两处 `sys.stdout = devnull` 全局赋值 → `with redirect_thread_output(NULL_STREAM)` |
| `agent/background_review.py:367, 545` | 两处 `contextlib.redirect_stdout(_devnull)` → `redirect_thread_output(NULL_STREAM)` |
| `agent/curator.py:1739` | 同上 |
| `webui/server.py` | ① 新增 `emit_access_record()`（:173）：持久落盘 `logging` FileHandler → `<INTELLECT_HOME>/logs/webui-access.log`，stdout 镜像 try/except；② `log_request` 改走 `emit_access_record`；③ 请求路径 3 处 `print` → `_safe_print`（失败降级到 logger）；④ `main()` 早期 `install_stream_guard()`（:717）兜底任何残余全局替换；⑤ 新增 `_probe_health_once`（:637）/`start_liveness_watchdog`（:663），server bind 成功后启动 |

### 配置项（均为环境变量，非必填）

| 变量 | 默认 | 说明 |
|---|---|---|
| `INTELLECT_WEBUI_WATCHDOG` | 开 | `0`/`false`/`off`/`no` 关闭看门狗 |
| `INTELLECT_WEBUI_WATCHDOG_INTERVAL` | 30s | 探测间隔（下限 5s）；非法值回退默认并告警 |
| `INTELLECT_WEBUI_WATCHDOG_TIMEOUT` | 10s | 单次探测超时（下限 1s）；非法值回退默认并告警 |
| `INTELLECT_WEBUI_WATCHDOG_MAX_FAILURES` | 3 | 连续失败阈值，达到即退出 |

行为变化提示：
- 访问日志新增持久文件 `~/.intellect/logs/webui-access.log`（`RotatingFileHandler`，
  50MB × 5 份轮转）；`~/.intellect/webui.log`（stdout 重定向）仍保留镜像输出，
  `tail` 习惯不受影响。
- 已知取舍：工具内部再 spawn 的子线程的裸 `print` 不再被抑制（线程局部重定向
  只静默发起调用的线程；旧的全局替换连子线程一起吞）。漏显仅影响 CLI spinner
  观感，建议工具实现统一走 logger。

---

## 五、验证

- 新增 20 个测试（竞态回归 + 弹性）全部通过；
- curator / background_review 既有 142 个回归测试全部通过；
- `tests/tools/test_code_execution.py`（9 失败）与 `tests/webui/`（8 失败 + 1 收集错误）中的失败已用 `git stash` 在**未修改的 main 上复现为完全相同的失败**（AST 安全层拦截老测试 `import os` 等），属预先存在的测试债，与本修复无关；
- 复现指引：在旧代码上并发跑两个 execute_code 会话（脚本内各调用 `terminal`），数分钟内访问日志断流；命中关闭竞态时全部请求报 `I/O operation on closed file`。

---

## 六、企业版适配建议

1. **移植范围**：企业版若从本仓库合并，`agent/stream_guard.py` 与四处调用点替换为必选项（L1）；`webui/server.py` 的 L2/L3 建议同批合入。企业版若有自研的多线程部署形态（如进程池前置代理、自定义 RPC 线程），请 grep 仓库内其余 `redirect_stdout` / `sys.stdout =` 调用点逐一评估。
2. **已知未迁移点**（有意保留，避免改变行为）：
   - `plugins/platforms/google_chat/adapter.py` 的 `redirect_stdout(buf)` 包裹 `asyncio.to_thread` —— 重定向**跨线程**生效（依赖全局替换把 worker 线程的 print 也吸进来），线程局部化会改变捕获语义，需单独设计；
   - `cli.py`、`intellect_cli/oneshot.py`、`tui_gateway/slash_worker.py`、`intellect_cli/kanban.py` 的前台单线程捕获场景 —— 竞态窗口理论存在但无多线程重定向方，可后续统一迁移到 `redirect_thread_output`。
3. **同类隐患排查清单**：
   - 任何在多线程服务进程里替换 `sys.stdout`/`sys.stderr`/全局 logging handler 的代码；
   - `open(os.devnull)` + with 传递给重定向 API 的模式（对象可被并发关闭，建议换 `NULL_STREAM` 类的无状态丢弃流）；
   - sqlite `disk I/O error` 突发（本次 13:25 / 14:06 两次出现）作为 fd/流异常的前兆指标，建议纳入告警。
4. **运维建议**：
   - WebUI/gateway 建议由 supervisor（systemd 等）托管，配合本修复的 watchdog（退出即释放端口）实现秒级自愈；
   - **重负载主机调优**：默认连续 3 次失败（~90s 完全无响应）即退出。CPU 严重超载或长 GC 停顿的机器上，健康但饥饿的进程理论上可能被误杀——建议将 `INTELLECT_WEBUI_WATCHDOG_MAX_FAILURES` 调至 4–6 或加大 `INTERVAL`；
   - 访问日志断流（`webui-access.log` 无新行而进程存活）本身就是强告警信号；
   - 长会话 + 高频工具调用是本类缺陷的放大器，容量规划时注意单进程会话数上限。

---

## 七、关键结论

- **任何"临时替换进程级全局流"的代码在多线程服务进程里都是竞态炸弹**；替换必须线程局部化，或通过不可变的稳定代理分派。
- **响应路径不得依赖任何可被第三方代码破坏的共享资源**；日志类副作用必须 best-effort。
- **僵死进程比崩溃进程危害更大**；长驻服务需要真实的端到端 liveness 探测，而不是"进程还在"。
