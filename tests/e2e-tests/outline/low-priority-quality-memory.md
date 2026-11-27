# 项目记忆 — low-priority-quality

- 修复入口: `gateway/session.py` `flush:` 参数 + `gateway/run.py` shutdown 两处 flush。
- 回归口令: `scripts/run_tests.sh tests/gateway/test_restart_resume_pending.py tests/tools/test_skills_tool.py tests/agent/test_prompt_caching.py tests/cli/test_cli_approval_ui.py`
- Skills cache: `tools/skills_tool.py::_skills_cache_signature`；任何新技能扫描目录必须纳入 signature。
- CI coverage: `.github/workflows/tests.yml` weekly schedule + workflow_dispatch；不要移除六 shard 权威 gate。
- 报告: `tests/e2e-tests/reports/REPORT-low-priority-quality-2026-11-27.md`
- R6: 本模块无浏览器 UI，CLI/单元探针为诊断证据；不要伪造截图。
