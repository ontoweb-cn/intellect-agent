# TC-LPQ-04 — platform 切换缓存失效

目的：验证缓存 key 仍包含 platform，切换 platform 不会复用错误过滤结果。
前置：临时 skills 目录，一个 `platforms: [macos]` 技能。
优先级：P2
关联手册：2

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | `mock_sys.platform="darwin"`，调用 `_find_all_skills()` | 包含 mac-only | `results/low-priority-quality/TC-LPQ-04.txt` |
| 2 | 切到 `"linux"`，再调用 | 不包含 mac-only | 同上 |
