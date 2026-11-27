# TC-LPQ-03 — 原地编辑 SKILL.md 缓存失效

目的：验证目录 mtime 不变时，文件内容/mtime+size 变化能刷新 `_find_all_skills()` 缓存。
前置：`SKILLS_DIR` 指向临时目录，创建一个技能。
测试数据：`fixtures/low-priority-quality/make_skill.py`。
优先级：P1
关联手册：2

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 创建 `e2e-low-priority-skill`，description=Old | `_find_all_skills()` 返回 Old | `results/low-priority-quality/TC-LPQ-03.txt` |
| 2 | 原地重写 SKILL.md，description=Updated in place | 文件 mtime/size 变化 | 同上 |
| 3 | 再次 `_find_all_skills()` | 返回 Updated in place | 同上 |
