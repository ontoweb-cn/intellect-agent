# TC-LPQ-05 — 多行命令审批安全门禁

目的：验证多行命令默认 deny，必须 view 后才能批准。
前置：`IntellectCLI` stub，审批状态含 `requires_full_review=True`。
优先级：P1
关联手册：3

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 选择 `once` 并调用 `_handle_approval_selection()` | 不进入 response_queue，状态保留 | `results/low-priority-quality/TC-LPQ-05.txt` |
| 2 | 选择 `view` | 打印完整命令，`viewed_full=True` | 同上 |
| 3 | 再选 `deny` | 队列收到 deny | 同上 |
