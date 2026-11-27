# 低优先级质量项 — 测试大纲（步骤 4）

- 截图目录：`tests/e2e-tests/results/low-priority-quality/`
- 前置依赖：`.venv`、`scripts/run_tests.sh`
- 隔离声明（R3/R5）：所有临时数据置于 `tmp_path` / `mktemp -d`，命名含 `e2e-low-priority-`；用后删除。
- 原生弹窗处理：本模块无浏览器点击/上传，无原生弹窗（R6）。
- R6 口径：SessionStore/Skill 缓存无浏览器 UI；CLI/单元探针作为诊断证据，报告注明不等价于浏览器 UI。

## 用例矩阵

| 用例 | 场景 | 类型 | 优先级 | 关联手册 |
|---|---|---|---|---|
| TC-LPQ-01 | 单次 `mark_resume_pending` 立即持久化 | 正常 | P1 | 1 |
| TC-LPQ-02 | 批量标记 flush=False，显式 flush 后持久化；未 flush 不落盘 | 边界 | P1 | 1 |
| TC-LPQ-03 | 原地编辑 SKILL.md 内容后缓存失效 | 正常/边界 | P1 | 2 |
| TC-LPQ-04 | platform 切换仍使 skills 缓存失效 | 边界 | P2 | 2 |
| TC-LPQ-05 | 多行命令审批默认 deny、view 后才可批准 | 错误/安全 | P1 | 3 |
| TC-LPQ-06 | native Anthropic tool marker / OpenRouter tool 无 marker | 正常/边界 | P2 | 3 |
| TC-LPQ-07 | coverage workflow 有 manual + schedule 触发，六 shard 权威 | 配置 | P3 | 3 |
| TC-LPQ-08 | 空 skills 目录、无效 SKILL.md 不崩溃 | 错误 | P2 | 2 |

## 手册↔代码差异清单（步骤 3）

- 手册 1 原描述“每个标记立即写”与目标批量优化冲突；已修订为“单次立即、shutdown 批量 flush”。
- 手册 2 明确平台与外部目录变化；代码 `_skills_cache_signature` 覆盖。
- 手册 3 将 coverage 定义为 advisory，不声称强 gate；避免旧 `continue-on-error` 口径误导。
- 未补：`test_execute_code_approval_cluster` 的 AST payload、`test_run_agent` 截断宽断言；列入 R6 遗留，不阻塞。
