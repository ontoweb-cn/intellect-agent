# 在线状态功能 Bug 修复计划

**日期：** 2026-06-01
**基于：** 两轮 `/code-review` 审查结果
**状态：** ✅ 两轮修复已完成（commits `162d2176f` + `当前`，75 tests passed）

---

## P0 — 必须立即修复

### Fix 1: 4 条登录路径跳过 `record_session()`

**文件：** `intellect_cli/main.py`，行 7086 / 7098 / 7483 / 7595

**问题：** OAuth 登录（loopback + device code）、重置码登录、首次设密登录——这 4 条路径都调用了 `_write_cli_session()` 但未调用 `db.record_session()`。只有常规密码登录（行 7124）和注册（行 6571）调用了 `record_session`。

**修复：** 将 `record_session` 移动到 `_write_cli_session()` 内部——这是所有登录路径的共同汇合点。

### Fix 2: `/whoami` 调用不存在的方法

**文件：** `gateway/run.py`，行 9873

**问题：** `self._load_gateway_config()` 是模块级函数（行 1413），不是 `GatewayRunner` 的实例方法。调用 `self._load_gateway_config()` 会抛 `AttributeError`，被 `except: pass` 静默吞下。

**修复：** 改为调用模块级函数 `_load_gateway_config()`（去掉 `self.`）。

### Fix 3: Gateway 连接泄漏

**文件：** `gateway/run.py`，行 17524–17536 和 9879–9894

**问题：** `MembershipDB.close()` 在 `try` 块内，如果 `update_activity()` 或 `get_member()` 抛异常，连接不关闭。

**修复：** 将 `close()` 移到 `finally` 块。

---

## P1 — 建议近期修复

### Fix 4: Logout 先删文件后写 DB

**文件：** `intellect_cli/main.py`，行 7172–7178

**问题：** `session_file.unlink()` 在 `MembershipDB` 创建和 `end_session` 之前执行。DB 失败时 session 文件已删除但 DB 状态未更新。

**修复：** 先用 try/except 包裹 `end_session`，DB 操作成功后再 `unlink`。

### Fix 5: `register` 中 `record_session` 无错误处理

**文件：** `intellect_cli/main.py`，行 6571

**问题：** `record_session` 在账户完全创建后调用，但无 try/except。失败时 CLI 崩溃但账户已提交。

**修复：** 用 try/except 包裹，失败时打印警告但不阻塞注册完成。

### Fix 6: `members show` 无过期清理

**文件：** `intellect_cli/main.py`，行 6721

**问题：** `members list` 调用 `cleanup_expired_sessions()` 但 `members show` 不调用，导致两个命令对同一成员显示不同在线状态。

**修复：** `members show` 也调用 `cleanup_expired_sessions()`。

---

## P2 — 质量改进

### Fix 7: TTL 魔数提取为常量

**文件：** `agent/membership.py`，行 854 / 883

**修复：** `_SESSION_TTL = {"cli": 86400, "gateway": 3600}` + 辅助函数。

### Fix 8: 相对时间复用 `_relative_time()`

**文件：** `intellect_cli/main.py`，行 6700

**修复：** 用已有的 `_relative_time(ts)` 替代内联格式化。

### Fix 9: `_refresh_online_status` 清理死代码

**文件：** `agent/membership.py`，行 989–993

**修复：** 移除 `COUNT(*)` 上无意义的 `LIMIT 1`，简化 guard。

### Fix 10: `members list` 恢复 ID 列

**文件：** `intellect_cli/main.py`，行 6708

**修复：** 加回 ID 列（精简格式：前 8 位），保留 STATUS + ROLE + LAST SEEN。

---

## 预计工作量

| 优先级 | 修复数 | 时间 |
|--------|--------|------|
| P0 | 3 | 1h |
| P1 | 3 | 1h |
| P2 | 4 | 1.5h |
| **合计** | **10** | **3.5h** |
