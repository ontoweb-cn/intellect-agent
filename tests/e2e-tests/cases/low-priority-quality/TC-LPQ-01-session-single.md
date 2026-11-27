# TC-LPQ-01 — 单次 resume marker 立即持久化

目的：验证 `mark_resume_pending()` 默认 flush 在返回后落盘。
前置：临时 `SessionStore`，创建一个 telegram session。
测试数据：无夹具；`tmp_path` 自建。
优先级：P1
关联手册：1

| # | 操作（真实软件/诊断） | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 调用 `store.get_or_create_session(source)` | 返回 entry | `results/low-priority-quality/TC-LPQ-01.txt` |
| 2 | `store.mark_resume_pending(key)` | 返回 True | 同上 |
| 3 | 新建 store 并 `_ensure_loaded()` | 同 key `resume_pending == True` | 同上 |

> R6：无浏览器 UI；使用真实 `SessionStore` 落盘行为诊断。
