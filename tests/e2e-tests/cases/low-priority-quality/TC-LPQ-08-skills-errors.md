# TC-LPQ-08 — 空目录/无效技能不崩溃

目的：验证 skills cache/扫描的错误路径。
前置：临时 `SKILLS_DIR`。
优先级：P2
关联手册：2

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 空目录调用 `_find_all_skills()` | 返回 `[]` | `results/low-priority-quality/TC-LPQ-08.txt` |
| 2 | 写入无 frontmatter/invalid 的 SKILL.md | 跳过，不抛异常 | 同上 |
