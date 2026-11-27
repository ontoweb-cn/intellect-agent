# 低优先级质量项 — 用户手册（步骤 2）

## 1. 会话恢复标记持久化

入口：gateway 收到重启/关闭信号，或进程被 service manager 终止。

操作：gateway 在 shutdown 阶段为每个运行中 session 标记 `resume_pending`，然后 drain agent。

预期：
- 单次标记（例如代码直接调用）在返回后立即落盘；新进程启动能看到标记。
- 批量 shutdown 场景在所有 session 标记完成后统一 flush；不会每个 session 都重写整个 `session_index`。
- 如果 flush 前进程被强杀，可能丢失尚未 flush 的标记；这是批量优化的已知取舍，必须有统一 flush 点。

错误路径：
- session 不存在：返回 False，不写。
- session 已 suspended：不覆盖，返回 False。
- DB 写失败：调用方记录日志，不阻塞其他 session。

## 2. 技能列表缓存

入口：`skills_list` / agent 的技能发现流程；用户也可运行 `intellect skills list` 查看已安装技能。

操作：创建/编辑 `~/.intellect/skills/<category>/<name>/SKILL.md`。

预期：
- 新增技能：下一次发现能看到。
- 原地编辑 `SKILL.md` 的 description/name：下一次发现能看到新值，即使父目录 mtime 未变化。
- 平台变化：缓存失效，按新 platform 重新过滤。
- 外部 skills 目录变化：同样失效。

错误路径：
- 空目录 / 不存在目录：返回空列表，不崩溃。
- 无法读取/解析的 SKILL.md：跳过该 skill，记录 debug 日志。

## 3. 测试与 CI 质量

入口：开发者运行 `scripts/run_tests.sh`；CI 的 Tests workflow。

预期：
- 审批安全门禁有回归测试：多行命令默认 deny、必须先 view、完整命令可显示。
- prompt caching 公共 API 对 native Anthropic tool 消息有 marker、对 OpenRouter tool 消息无 marker。
- coverage 为 advisory（非合并 gate），手动 `workflow_dispatch` 和每周 schedule 可触发；六 shard 仍是权威 gate。
- 测试弱断言、已删除的私有测试保留为 R6 现状注记，不在本模块扩大改造。
