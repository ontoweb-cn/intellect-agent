# TC-LPQ-07 — Coverage workflow 触发

目的：验证 coverage 为 advisory，支持 `workflow_dispatch` 和 weekly schedule；六 shard 仍为权威 gate。
前置：读取 `.github/workflows/tests.yml`。
优先级：P3
关联手册：3

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | 检查 `on:` | 含 `workflow_dispatch` + `schedule` | `results/low-priority-quality/TC-LPQ-07.txt` |
| 2 | 检查 coverage job | `continue-on-error: true`、timeout 存在 | 同上 |
| 3 | 检查 test job | 6 个 shard 仍运行 | 同上 |
