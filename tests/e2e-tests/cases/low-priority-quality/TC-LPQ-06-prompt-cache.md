# TC-LPQ-06 — Prompt caching 公共 API tool marker

目的：恢复 tool message/native Anthropic 与 OpenRouter 的 marker 覆盖。
前置：无。
优先级：P2
关联手册：3

| # | 操作 | 预期结果 | 截图/输出 |
|---|---|---|---|
| 1 | `apply_anthropic_cache_control([...tool...], native_anthropic=True)` | tool 消息有 top-level cache_control | `results/low-priority-quality/TC-LPQ-06.txt` |
| 2 | 同输入 `native_anthropic=False` | tool 消息无 cache_control | 同上 |
