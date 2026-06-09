# Profile 管理临时屏蔽 — 恢复手册

> **日期:** 2026-06-03  
> **状态:** 已落地（临时产品门控）  
> **目的:** 记录本次为「屏蔽 profile 增删改/切换」改动的**全部文件与函数**，便于日后一键恢复。  
> **关联:** 多用户加固 `docs/plans/2026-06-02-members-webui-hardening-design.md`、会话隔离 `intellect-webui/docs/plans/session-member-isolation-plan.md`

---

## 1. 开关与行为摘要

| 配置项 | 位置 | 默认值 | 含义 |
|--------|------|--------|------|
| `profiles.management_enabled` | `~/.intellect/config.yaml`（合并自 `intellect_cli/config.py` `DEFAULT_CONFIG`） | **`false`** | `true` = 恢复 CLI/WebUI 的 profile 创建、切换、删除 |

**屏蔽时仍可用：**

- `intellect -p <已有 profile 名> chat|gateway|…`（`intellect_cli/main.py` 启动前 `-p` 解析，未改）
- 只读：`intellect profile`、`intellect profile list`、`intellect profile show`、`intellect profile describe`、`intellect profile export` 等
- WebUI 其余功能；请求上下文固定为 **`default`** profile（忽略 `intellect_profile` cookie）

**屏蔽时不可用：**

- CLI：`use`、`create`、`delete`、`rename`、`import`、`install`、`alias`
- WebUI：Profiles 侧栏整页、composer profile 芯片、跨 profile 会话列表切换
- API：`POST /api/profile/switch|create|delete` → 403

---

## 2. 快速恢复步骤

1. 在活跃 profile 的 `config.yaml` 中设置：
   ```yaml
   profiles:
     management_enabled: true
   ```
2. 重启 WebUI 进程（`server.py`），刷新浏览器。
3. 验证：
   - `intellect profile create test-gate` 可执行（或按环境策略仅测 WebUI）
   - WebUI 侧栏出现 **Profiles** 标签，composer 出现 profile 芯片
   - 会话侧栏在存在其他 profile 会话时出现 “Show N from other profiles”
4. （可选）若要从代码库**完全移除**门控：按 §4 逆向删除 `TEMPORARY` 注释块；或保留门控、仅改配置为 `true`。

---

## 3. 按仓库列文件

### 3.1 intellect-agent（本仓库）

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `intellect_cli/profile_gate.py` | **新增** | 门控核心：`is_profile_management_enabled()`、`CLI_MUTATING_PROFILE_ACTIONS` |
| `intellect_cli/config.py` | 修改 | `DEFAULT_CONFIG["profiles"]["management_enabled"] = False` + 注释 |
| `intellect_cli/main.py` | 修改 | `cmd_profile()` 入口拦截 mutating 子命令 |
| `tests/intellect_cli/test_profile_gate.py` | **新增** | 门控单元测试 |

### 3.2 intellect-webui（同级仓库）

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `api/profiles.py` | 修改 | WebUI 侧门控 + 固定 default 上下文 |
| `api/routes.py` | 修改 | GET/POST profile 相关路由 |
| `static/boot.js` | 修改 | 启动时调用 `_applyProfileManagementUiGate` |
| `static/panels.js` | 修改 | UI 隐藏 + 客户端函数早退 |
| `static/sessions.js` | 修改 | 移除跨 profile 会话切换 UI |
| `static/index.html` | 修改 | HTML 注释标记 TEMPORARY 元素 |

**WebUI 副本路径（便于搜索）：** `../intellect-webui/docs/plans/profile-management-disabled-restore.md` 为本文档的索引页（指向本文件）。

---

## 4. 函数与符号清单（恢复时对照）

### 4.1 `intellect_cli/profile_gate.py`（新增模块）

| 符号 | 类型 | 作用 |
|------|------|------|
| `CLI_MUTATING_PROFILE_ACTIONS` | `frozenset` | 被禁 CLI 子命令：`use`, `create`, `delete`, `rename`, `import`, `install`, `alias` |
| `is_profile_management_enabled(config=None)` | 函数 | 读 `profiles.management_enabled`，默认 `False` |
| `profile_management_disabled_message()` | 函数 | CLI/WebUI 统一错误文案 |

### 4.2 `intellect_cli/main.py`

| 符号 | 变更 |
|------|------|
| `cmd_profile(args)` | 在 `action = getattr(args, "profile_action")` 之后、分支逻辑之前，若 `action in CLI_MUTATING_PROFILE_ACTIONS` 且门控关闭则 `sys.exit(1)` |

**未修改（仍可用）：** `_apply_profile_override()` / `-p` / `--profile` 预解析（约 L251–336）。

### 4.3 `intellect_cli/config.py`

| 符号 | 变更 |
|------|------|
| `DEFAULT_CONFIG["profiles"]` | 新增节 `management_enabled: False` + TEMPORARY 注释 |

### 4.4 `tests/intellect_cli/test_profile_gate.py`

| 测试函数 | 作用 |
|----------|------|
| `test_profile_management_disabled_by_default` | 默认关闭 |
| `test_profile_management_enabled_when_config_true` | 配置为 true 时开启 |
| `test_cmd_profile_create_blocked_when_disabled` | `cmd_profile` 拦截 create |

---

### 4.5 `intellect-webui/api/profiles.py`

| 符号 | 变更 |
|------|------|
| `is_profile_management_enabled()` | **新增** — 委托 `intellect_cli.profile_gate` |
| `_require_profile_management_enabled()` | **新增** — 未开启时 `raise ValueError` |
| `get_active_profile_name()` | 门控关闭时**始终返回** `"default"` |
| `set_request_profile(name)` | 门控关闭时**强制** `name = "default"` |
| `switch_profile(name, *, process_wide=True)` | 开头调用 `_require_profile_management_enabled()` |
| `create_profile_api(...)` | 开头调用 `_require_profile_management_enabled()` |
| `delete_profile_api(name)` | 开头调用 `_require_profile_management_enabled()` |

**未改但相关（恢复后仍会走）：** `list_profiles_api()`, `get_active_intellect_home()`, `_validate_profile_name()`, `switch_profile` 内 STREAMS 锁逻辑。

### 4.6 `intellect-webui/api/routes.py`

| 路由 / 位置 | 变更 |
|-------------|------|
| `GET /api/profiles`（`handle_get`） | 门控关闭时 `profiles: []`，响应含 `management_enabled` |
| `GET /api/profile/active` | 响应增加 `management_enabled` |
| `GET /api/sessions`（profile 过滤段） | `all_profiles` 仅在 `is_profile_management_enabled()` 为真时读 query `?all_profiles=1` |
| `GET /api/projects`（profile 过滤段） | 同上 |
| `POST /api/profile/switch`（`handle_post`） | 门控关闭 → **403** |
| `POST /api/profile/create` | 门控关闭 → **403** |
| `POST /api/profile/delete` | 门控关闭 → **403** |

辅助符号（已有，本次调用）：`_all_profiles_query_flag(parsed)`。

### 4.7 `intellect-webui/static/panels.js`

| 符号 | 变更 |
|------|------|
| `_profileManagementEnabled` | **新增** 模块级变量 |
| `isProfileManagementEnabled()` | **新增** |
| `_applyProfileManagementUiGate(enabled)` | **新增** — 隐藏 `[data-panel=profiles]`、`#profileChipWrap`、`#panelProfiles`、`#mainProfiles`；`S.activeProfile='default'`；`_showAllProfiles=false` |
| `switchPanel(...)` | `nextPanel === 'profiles'` 时若门控关闭则改跳 `chat` |
| `activateCurrentProfile()` | 早退 |
| `toggleProfileDropdown()` | 早退 |
| `switchToProfile(name)` | 早退 |
| `openProfileCreate()` | 早退 |
| `saveProfileForm()` | 早退 |
| `deleteProfile(name)` | 早退 |

**未加早退但面板已隐藏：** `loadProfilesPanel()`, `renderProfileDropdown()`, `deleteCurrentProfile()`, `openProfileDetail()` 等 — 恢复 UI 后仍可用。

### 4.8 `intellect-webui/static/boot.js`

| 位置 | 变更 |
|------|------|
| 启动流程（约 L1637–1644） | `GET /api/profile/active` 后调用 `_applyProfileManagementUiGate(p.management_enabled!==false)` |

### 4.9 `intellect-webui/static/sessions.js`

| 符号 | 变更 |
|------|------|
| `renderSessionList` 内 profile 切换块（约 L3505–3507） | **删除** UI；见 §5 附录恢复原代码 |
| `_showAllProfiles` | 仍声明；门控时由 `_applyProfileManagementUiGate` 置 `false` |
| `loadSessionList` / `renderSessionList` | 仍使用 `_showAllProfiles` 拼 `?all_profiles=1`（门控下服务端忽略） |

### 4.10 `intellect-webui/static/index.html`

| 元素 | 变更 |
|------|------|
| `data-panel="profiles"` 按钮（rail + sidebar nav） | 仅 HTML 注释 TEMPORARY |
| `#panelProfiles` | 注释 |
| `#profileChipWrap` | 注释 |

---

## 5. 附录：恢复 `sessions.js` 跨 profile 切换 UI

屏蔽时删除了 `renderSessionList`（或同级列表渲染）中约 L3505–3522 的代码块。恢复时在该位置（ archived 切换之前）插回：

```javascript
  // Profile filter toggle (show sessions from other profiles).
  // Cross-profile rows live SERVER-SIDE behind ?all_profiles=1, so the toggle
  // must trigger a refetch — there's no client-cached aggregate to slice through.
  const otherProfileCount = _otherProfileCount;
  if (otherProfileCount > 0 && !_showAllProfiles) {
    const pfToggle = document.createElement('div');
    pfToggle.style.cssText = 'font-size:10px;padding:4px 10px;color:var(--muted);cursor:pointer;text-align:center;opacity:.7;';
    pfToggle.textContent = 'Show ' + otherProfileCount + ' from other profiles';
    pfToggle.onclick = () => { _showAllProfiles = true; renderSessionList(); };
    list.appendChild(pfToggle);
  } else if (_showAllProfiles) {
    const pfToggle = document.createElement('div');
    pfToggle.style.cssText = 'font-size:10px;padding:4px 10px;color:var(--muted);cursor:pointer;text-align:center;opacity:.7;';
    pfToggle.textContent = 'Show active profile only';
    pfToggle.onclick = () => { _showAllProfiles = false; renderSessionList(); };
    list.appendChild(pfToggle);
  }
```

并确保 `api/routes.py` 中 `/api/sessions` 与 `/api/projects` 的 `all_profiles` 逻辑仍仅在 `is_profile_management_enabled()` 为真时启用（或移除该条件以始终允许 query 参数）。

---

## 6. 代码内搜索关键词（审计用）

在两个仓库中全文搜索可定位所有临时改动：

```text
TEMPORARY
profiles.management_enabled
profile_gate
is_profile_management_enabled
isProfileManagementEnabled
_applyProfileManagementUiGate
CLI_MUTATING_PROFILE_ACTIONS
```

---

## 7. 产品文档更新（用户可见）

| 文档 | 更新内容 |
|------|----------|
| `website/docs/user-guide/profiles.md` | §「临时屏蔽」— 指向本恢复手册 |
| `intellect-webui/docs/members-oauth-webui.md` | 相关功能 § 增加门控说明 |
| `docs/plans/2026-06-02-members-webui-hardening-design.md` | §18 增加交叉引用 |

---

## 8. 变更历史

| 日期 | 说明 |
|------|------|
| 2026-06-03 | 初版：CLI + WebUI 门控；默认 `management_enabled: false`；Profiles 页与跨 profile 会话列表关闭 |
