# 低优先级质量项 — 九步交付报告

- 日期：2026-11-27
- 模块：low-priority-quality
- 代码提交：`028fced`（实现）、`93cc0d8`（lint 修复）
- 范围来源：`bd54f40` 评审报告「仍未处理（低优先级）」
- 执行方式：九步交付法（用户明确要求继续处理；作为范围授权，Spec 已记录）

## 1. 结论摘要

| 缺陷/改进 | 修复 | 验收证据 |
|---|---|---|
| F-LPQ-01 shutdown 每 session 全量写 session_index | `mark_resume_pending(..., flush=False)` + 两次统一 `SessionStore.flush()`；单次调用仍立即持久化 | TC-LPQ-01/02；restart 99 tests |
| F-LPQ-02 skills cache 只依赖目录 mtime | `_skills_cache_signature()`：platform + 每个 SKILL.md path/mtime_ns/size（local/team/project/member/external） | TC-LPQ-03；skills 87 tests |
| F-LPQ-03 prompt caching 公共 API 覆盖不足 | 增加 native Anthropic tool marker / OpenRouter tool 无 marker 测试 | TC-LPQ-06；prompt caching 9 tests |
| F-LPQ-04 coverage 仅手动 | `workflow_dispatch` + weekly `schedule`；advisory，六 shard 仍为 gate | TC-LPQ-07 静态检查 |
| D-LPQ-01 AST approval/truncation 弱断言 | 本模块不扩大改造，R6 记录 | 见 §4 |

最终模块回归：**15 files / 917 tests passed，0 failed**。  
CI `93cc0d8`：**Tests 6/6 success，Lint success，Security success，Nix success，CodeQL success**；`028fced` Docker success。

## 2. 用例执行结果

| 用例 | 结果 | 证据 |
|---|---|---|
| TC-LPQ-01 单次 marker 立即持久化 | PASS | `results/low-priority-quality/TC-LPQ-01.txt` |
| TC-LPQ-02 批量 flush=False + flush | PASS | `TC-LPQ-02.txt` |
| TC-LPQ-03 原地编辑 SKILL.md 缓存失效 | PASS | `TC-LPQ-03.txt`；CLI before/after 仅名字可见（R6） |
| TC-LPQ-04 platform 切换缓存失效 | PASS | `TC-LPQ-04-06-08.txt` |
| TC-LPQ-05 多行审批默认 deny + view 解锁 | PASS | `TC-LPQ-05.txt` |
| TC-LPQ-06 native/OpenRouter tool marker | PASS | `TC-LPQ-06.txt` |
| TC-LPQ-07 coverage 触发配置 | PASS | `TC-LPQ-07.txt` |
| TC-LPQ-08 空目录/无效技能错误路径 | PASS | `TC-LPQ-04-06-08.txt` |

回归日志：`results/low-priority-quality/final-regression.log`（15 files / 917 passed）。

## 3. 交付物

### 代码

- `gateway/session.py`：`flush` 参数 + public `flush()`。
- `gateway/run.py`：shutdown 两处批量标记 + 单次 flush。
- `tools/skills_tool.py`：`_skills_cache_signature()`。
- `.github/workflows/tests.yml`：weekly schedule + coverage 条件。
- `tests/gateway/test_restart_resume_pending.py`、`tests/tools/test_skills_tool.py`、`tests/agent/test_prompt_caching.py`。

### 文档/测试资产

- 研究：`tests/e2e-tests/outline/low-priority-quality-research.md`
- 用户手册：`docs/low-priority-quality-manual.md`
- 大纲：`tests/e2e-tests/outline/low-priority-quality.md`
- 用例：`tests/e2e-tests/cases/low-priority-quality/`
- 夹具/探针：`tests/e2e-tests/fixtures/low-priority-quality/`
- Spec：`tests/e2e-tests/spec/low-priority-quality-spec.md`
- 结果：`tests/e2e-tests/results/low-priority-quality/`
- 项目记忆：`tests/e2e-tests/outline/low-priority-quality-memory.md`、`TODO.md`

### 构建/健康

- `py_compile`：通过。
- `ruff`：Lint ratchet success（CI）。
- 真实运行库 CLI：`intellect version`、`intellect skills list` 成功（`health-*.txt`）。
- 部署：本模块为 Python 源码内嵌逻辑，无独立构建产物；运行 `.venv` 即真实运行库，探针确认新代码生效。

## 4. 受限注记（R6）

1. **无浏览器 UI 可观测面**：`SessionStore`/`_SKILLS_CACHE` 是内部模块，本仓库无对应浏览器页面；使用真实 CLI + 单元探针作为诊断证据，不伪造截图。
2. **CLI skills list 不显示 description**：before/after 表只显示 name；缓存内容刷新由 `TC-LPQ-03.txt` 的 `_find_all_skills()` 探针证明。
3. **本地全量套件**存在已知网络/超时噪声（历史 flaky）；本次以 CI 六个隔离 shard 为权威回归门。
4. **coverage schedule 尚未实际到点执行**：仅静态验证 trigger/job 配置；首次运行需观察是否超时，advisory 不阻塞合并。
5. **AST approval payload、run_agent 截断宽断言**未在本次范围改造；保留为后续测试质量任务，避免扩大改动。

## 5. 遗留建议

- 观察首次 weekly coverage 运行；若 60m 仍超时，缩小 coverage 范围或改为 shard 上传。
- Session flush 失败仅 debug 日志；生产可考虑升级为 warning/指标。
- Skills signature 每次列表 O(SKILL.md) stat；若外部技能目录非常大，可增加短 TTL 缓存。
- 为 approval AST 测试设计独立的安全检查/审批上下文用例，替代 `print('ok')` 弱 payload。

## 6. 九步闭环声明

调研、手册、手册↔代码检视、大纲/用例/夹具、执行、Spec（执行范围记录）、修复、构建/lint/健康、回归、报告/归档/记忆均已完成；受限项已按 R6 如实记录，未伪造通过。
