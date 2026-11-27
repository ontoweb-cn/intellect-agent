# 低优先级质量项 — 调研（步骤 1）

Date: 2026-11-27
Module: low-priority-quality
Scope source: review report `bd54f40` follow-up section.

## 目标范围

1. `SessionStore.mark_resume_pending()` shutdown 全量写放大。
2. `_find_all_skills()` 缓存只依赖目录 mtime，原地编辑 `SKILL.md` 可能读到旧 metadata。
3. 测试/CI 质量缺口：审批上下文测试弱、prompt-caching marker 覆盖丢失、coverage 仅手动/建议性。

## 代码定位

### Session flush

- `gateway/session.py::SessionStore.mark_resume_pending`：持有 `_lock` 时同步 `_save_now()`；每次调用都会 `_save_impl()` 重写 `session_index`。
- `gateway/session.py::flush` / `_flush`：立即写一次并取消 debounce。
- `gateway/run.py` shutdown 两处循环：
  - pre-drain `mark_resume_pending(...)`（约 3108 行）
  - drain 后 final `mark_resume_pending(...)`（约 3238 行）
  每处按 active agent 数逐次全量写。
- 现有测试：`tests/gateway/test_restart_resume_pending.py`（TestMarkResumePending 等）。

### Skills cache

- `tools/skills_tool.py::_find_all_skills`，缓存字典 `_SKILLS_CACHE`。
- 当前 key：`SKILLS_DIR.stat().st_mtime` + platform。
- 扫描目录：`SKILLS_DIR`、team/project/member skills、external skills dirs（`agent.skill_utils`）。
- 问题：编辑 `SKILL.md` 内容不会更新父目录 mtime，缓存命中返回旧 description。
- 现有测试：`tests/tools/test_skills_tool.py`（TestFindAllSkills、platform filtering）。

### Test / CI

- `tests/cli/test_cli_approval_ui.py`：本次已补多行/完整命令门禁测试。
- `tests/agent/test_prompt_caching.py`：私有 `_apply_cache_marker` 测试已删除；需要公共 `apply_anthropic_cache_control` 的 tool/native 覆盖。
- `.github/workflows/tests.yml`：coverage job 为 manual/advisory；已加 `workflow_dispatch`，缺定期触发。
- 其他弱断言：`tests/tools/test_execute_code_approval_cluster.py` 已用 `print('ok')`；`tests/run_agent/test_run_agent.py` 截断断言接受宽泛 wrapper。按本次范围记为 R6，不扩大改造。

## 真实运行库 / UI 可观测面

- 本仓库是 Python agent + CLI/WebUI；这两个模块没有浏览器页面直接暴露 `SessionStore`/`_SKILLS_CACHE` 内部状态。
- `intellect skills list` 是真实 CLI 用户界面（临时 `INTELLECT_HOME` 可隔离），可用于 skills 可见性基线，但不直接走 `_find_all_skills` 的同一缓存路径。
- Session resume 只在 gateway 重启后可见；自动化浏览器不可用且无对应页面。
- 因此本模块的细粒度验证采用 R6「诊断性只读/单元级探针」记录；不伪造浏览器截图。

## 环境基线

- Repo: `/Users/simon/workspace/intellect-agent`
- Python: `.venv` / 3.12
- Test command: `scripts/run_tests.sh`
- Baseline before fixes: final review commit `bd54f40` CI green.

## 风险与约束

- Session durability fix must not lose resume markers on process kill before flush.
- Skills signature scan must not be prohibitively expensive for normal listings (only SKILL.md metadata, no YAML parse).
- No behavior changes to unrelated modules; no new dependency.
