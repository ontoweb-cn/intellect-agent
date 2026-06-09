# WebUI Collab 代码审查整改计划

**日期：** 2026-06-02
**基于：** `/code-review` 对 `9338eac55..HEAD` 的审查结果（2 角度 × 10 条 findings）

### 实施状态（2026-06-02）

| Fix | 状态 | 说明 |
|-----|------|------|
| 3 | ✅ | `register_from_invite` / `register_local_pending` 调用 `validate_member_id` |
| 5 | ✅ | `approve_registration` / `reject_registration` 按 `rowcount` 返回 bool；WebUI 无匹配 → 404 |
| — | ✅ | 本地待审与停用分离：`members.registration_pending` + `list_pending_registrations` |
| — | ✅ | WebUI 注册/OAuth `registration_token` / `MembershipStore` 签名对齐 |
| 1, 2, 6–8 | ✅ | `display_name_taken`、TOCTOU、`webui_authorize`、`fcntl` 会话锁（2026-06-02 加固） |
| 4 | ✅ | `MembershipDB.close` 后换行；死别名已移除 |
| 9 | ✅ | 8 个插件 HTTP 依赖改为 `plugins.http_deps.require_*` 清晰 `ImportError` |
| — | ✅ | intellect-webui：zh 多用户/i18n 补全（`code-review-fixes-2026-06-02` #7） |
| — | ✅ | intellect-webui：`code-review-fixes-2026-06-02` #4 loopback 限制 + `tests/test_loopback_sensitive_api.py` |
| — | ⏳ | **多用户会话隔离 Phase 4** — `intellect-webui/docs/plans/session-member-isolation-plan.md`（A–E）；`2026-06-02-members-webui-hardening-design.md` §18 |

成员生命周期与邀请/审核的规范见 `member-management-redesign.md` 与 `intellect-webui/docs/members-oauth-webui.md`（含 loopback 敏感端点说明）。会话隔离待开发任务见 WebUI `docs/plans/README.md` § 待开发清单。

**详细实施方案（成员/WebUI 待办）：** [`2026-06-02-members-webui-hardening-design.md`](./2026-06-02-members-webui-hardening-design.md)

---

## P0 — 必须立即修复

### Fix 1: `display_name_taken()` 查询错误列

**文件:** `agent/membership.py:1128`
**深度分析:** 方法名 `display_name_taken` 参数名 `display_name`，但实现调用 `get_member_by_login(display_name)` 查的是 `login_name` 列。`members` 表有独立的 `display_name` 和 `login_name` 列，且均无 UNIQUE 约束。`MembershipDB` 无 `get_member_by_display_name` 方法。

**修复:** 新增 `get_member_by_display_name()` 或内联 `SELECT * FROM members WHERE display_name = ?`。

---

### Fix 2: `create_member(member_id=)` TOCTOU 竞态

**文件:** `agent/membership.py:362-379`
**深度分析:** `get_member(member_id)` 是 SELECT（无锁），INSERT 在 `_execute_write` 的 `BEGIN IMMEDIATE` 内。两并发请求可同时通过存在性检查。`_execute_write` 只重试 `OperationalError`（lock/busy），不重试 `IntegrityError`（UNIQUE 冲突）。

**修复:** 将 `get_member` 检查移入 `_insert` 闭包（受 `BEGIN IMMEDIATE` 保护），INSERT 前再查一次。

---

### Fix 3: `create_registration_pending` / `register_from_invite` 绕过验证

**文件:** `agent/membership.py:1175-1205`
**深度分析:** 两个方法接受原始 `member_id` 直接 INSERT，未调用 `validate_member_id()`——无保留字检查、无路径遍历拒绝、无格式校验。这些是 WebUI 调用的入口点。

**修复:** 在 INSERT 前添加 `member_id = validate_member_id(member_id)` 调用。

---

## P1 — 建议近期修复

### Fix 4: 死代码清理

**文件:** `agent/membership.py:1098, 1113`
**深度分析:** 
- 行 1098：`MembershipStore = MembershipDB` 别名被行 1102 `class MembershipStore(TeamDB)` 立即覆盖
- 行 1113：重复的裸字符串字面量（类文档字符串的残留副本）

**修复:** 删除两行死代码。

---

### Fix 5: `approve_registration` / `reject_registration` 返回值 + 审计

**文件:** `agent/membership.py:1221-1243`
**深度分析:** 
- `approved_by` / `rejected_by` 参数接受但永不写入 DB
- 无条件返回 True，即使 WHERE 匹配 0 行

**修复:** 如果是明确的设计简化（审计字段留待以后），至少应添加注释说明。返回值应反映实际操作的行数。

---

### Fix 6: `_webui_authorize` 命名

**文件:** `agent/membership.py:1109`
**深度分析:** `_` 前缀约定表示私有/内部使用，但此方法是为 WebUI 外部调用设计的。WebUI 开发者可能发现不了它。

**修复:** 改为 `webui_authorize`（移除 `_` 前缀），添加模块级 `__all__` 或 docstring 说明其作为公共 API。

---

## P2 — 质量改进

### Fix 7: `validate_member_id` / `validate_team_id` DRY

**文件:** `agent/membership.py:164-212`
**深度分析:** 约 80% 代码重复。`validate_team_id` 唯一的区别是不检查 `_RESERVED_IDS`。

**修复:** 提取 `_validate_slug_id(raw, label, reserved=None)` 公共函数，两个公开函数委托给它。

---

### Fix 8: `member_session.py` 并发安全

**文件:** `agent/member_session.py:56-81`
**深度分析:** JSON 文件无锁保护。`os.replace` 是原子的但整个 load-modify-save 不是。

**修复:** 添加 `fcntl.flock` 文件锁（与 `auth.json` 模式一致），或接受当前"best-effort"语义并添加文档说明。

---

### Fix 9: 插件 `requests=None` / `httpx=None` 后续调用安全

**文件:** 8 个插件文件
**深度分析:** 当 requests/httpx 为 None 时，任何方法调用（如 `requests.post()`）会抛 `AttributeError`，而非清晰的 `ImportError`。

**修复:** 添加惰性导入检查函数，在首次使用时若为 None 则抛清晰的 `ImportError("requests is required but not installed")`。

---

## 预计工作量

| 优先级 | 修复数 | 时间 |
|--------|--------|------|
| P0 | 3 | 1.5h |
| P1 | 3 | 1h |
| P2 | 3 | 1h |
| **合计** | **9** | **3.5h** |
