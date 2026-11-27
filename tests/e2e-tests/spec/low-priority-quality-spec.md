# 低优先级质量项 — 修复 Spec（步骤 5/6 门）

## 背景

来源：`bd54f40` 评审报告「仍未处理（低优先级）」三项。用户在 2026-11-27 明确要求调用九步交付继续处理，故本次以其指令作为执行范围授权；本 spec 用于记录缺陷、取舍与验收，不阻塞执行。

## 缺陷 / 改进项

| ID | 级别 | 描述 | 位置 |
|---|---|---|---|
| F-LPQ-01 | P2 | shutdown 每个 session 触发一次全量 session_index 写 | `gateway/session.py`、`gateway/run.py` |
| F-LPQ-02 | P2 | skills cache 仅依赖目录 mtime，原地编辑 SKILL.md 不失效 | `tools/skills_tool.py` |
| F-LPQ-03 | P3 | prompt caching 公共 API 缺 tool/native 覆盖 | `tests/agent/test_prompt_caching.py` |
| F-LPQ-04 | P3 | coverage 只有手动入口；无定期触发 | `.github/workflows/tests.yml` |
| D-LPQ-01 | R6 | AST approval payload / truncation 宽断言未在本模块改造 | 测试资产 |

## 方案

### F-LPQ-01

- `mark_resume_pending(..., flush=True)`：单次调用立即持久化（保持兼容）。
- shutdown 两处循环改为 `flush=False`，循环后 `session_store.flush()` 一次。
- 保留 `flush()` public；异常逐 key 记录，最终 flush 记录日志但不阻塞中断。
- 取舍：若进程在批量标记后、flush 前被强杀，会丢失尚未 flush 的标记；两处 flush 紧跟标记循环，在 drain/中断前完成，窗口可接受。

### F-LPQ-02

- 新增 `_skills_cache_signature()`：platform + 每个 SKILL.md 的 `(path, mtime_ns, size)`，覆盖 local/team/project/member/external dirs。
- 缓存命中要求 signature 完全一致；扫描完成后重算并保存。
- 取舍：每次列表多 stat 每个 SKILL.md，不解析 YAML；相对解析成本可接受。

### F-LPQ-03

- 增加 native Anthropic tool 消息有 top-level `cache_control`、OpenRouter tool 消息无 marker 的公共 API 测试。

### F-LPQ-04

- `on:` 增加 weekly `schedule`；coverage job 条件包含 `schedule`。
- 保持 `continue-on-error: true` 和六 shard 权威 gate；不把 20+ 分钟 coverage 变成合并阻塞。
- 取舍：coverage 仍非强 gate，但自动报告可持续观察。

### D-LPQ-01

- 不在本次扩大修改：AST 测试需要重新设计安全检查与审批上下文；记录 R6 遗留建议。
- truncation 宽断言保留，避免误伤未截断路径。

## 验收

1. `test_flush_false_defers_durability_until_explicit_flush` + 现有 restart 99 tests。
2. `test_cache_invalidates_on_in_place_skill_edit` + skills 87 tests。
3. prompt caching 9 tests。
4. 目标套件 885 tests 全通过。
5. CI 6 shards + Lint/Security/CodeQL 全绿。

## 风险

- SessionStore 与 SessionDB 使用全局 `INTELLECT_HOME`：测试必须隔离 home；batch 测试已用 mock `_save_impl` 断言调用次数。
- Signature 扫描外部目录可能较慢；仅 stat 不做 YAML 解析。
- Coverage schedule 首次运行可能超时；advisory + timeout 60m，R6 记录。
