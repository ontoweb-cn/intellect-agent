# OAuth Code Review Fix Plan

**基于:** `/code-review` 对 OAuth 统一平台的 10 条 findings
**状态:** 大部分已在 OAuth 统一平台 / `v0.4.1` 评审修复中闭环；未决项见 [oauth-follow-up-tasks.md](oauth-follow-up-tasks.md)

## P0 Fixes

1. **oauth_api.py 接入 API server** — 注册路由
2. **DB 屏蔽 config.yaml** — 合并查询，config 覆盖 DB 默认值
3. **handle_callback 破坏** — 使用 state store 重建 session
4. **client_secret 未解密** — 解密后发送

## P1 Fixes

5. **Gateway 连接泄漏** — try/finally
6. **Builtins 3 vs 9** — 统一为 9 个

## P2 Fixes

7. **_toggle_provider 假成功** — rowcount 检查
8. **/bind metadata 覆盖** — JSON 合并
9. **Seed 忽略 config** — 合并 config 状态
10. **3 INSERT 副本** — 提取共享函数
