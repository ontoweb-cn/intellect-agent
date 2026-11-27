# TC-LPQ-02 — shutdown 批量 flush

目的：验证 `flush=False` 不在单次标记时写盘，`flush()` 一次写入全部标记。
前置：临时 `SessionStore`，两个 session。
测试数据：`e2e-low-priority-batch-a/b`。
优先级：P1
关联手册：1

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 创建 A/B，两个都 `mark_resume_pending(flush=False)` | 返回 True | `results/low-priority-quality/TC-LPQ-02.txt` |
| 2 | 新建 store 读取 | 尚无 resume_pending 标记 | 同上 |
| 3 | 原 store `flush()` | 返回无异常 | 同上 |
| 4 | 新 store 读取 | A/B 均 `resume_pending == True` | 同上 |
