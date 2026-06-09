---
sidebar_position: 15
title: "团队、项目与成员"
description: "单个 Intellect 配置文件内的多用户访问——成员、邀请、注册审核、团队与项目"
---

# 团队、项目与成员

> **范围：** 一个 **配置文件**（`~/.intellect` 或 `~/.intellect/profiles/<name>`），多个 **成员**。这与 [配置文件（Profiles）](./../profiles) 不同——后者是在同一台机器上运行多个独立的 Intellect 主目录。

当 `members.enabled` 为 `true` 时，一个 Intellect 配置文件可承载多名用户。每名成员有独立 id、可选密码、OAuth 身份、记忆（可按成员隔离）和 API token。**团队**提供共享协作上下文；**项目**提供共享工作区（常为 git 仓库）及项目级密钥与约定。

数据保存在该配置文件下的 `state.db`（位于 `~/.intellect/` 或当前激活的配置文件目录）。

## 启用功能

```yaml
# ~/.intellect/config.yaml
members:
  enabled: true
  teams:
    enabled: true      # 可选
  projects:
    enabled: true      # 可选
  bootstrap:
    default_admin_login: alice   # 可选；空库首次引导时的首个成员登录名
  registration:
    invite_ttl_hours: 168
    local_requires_approval: true   # 默认：本地注册需管理员审批
  oauth:
    enabled: true
    callback_base_url: http://127.0.0.1:9119   # 使用 Intellect WebUI 时填写 WebUI 源地址
    providers: []
    trusted_header:              # 可选：反向代理 SSO
      enabled: false
      header: X-Authenticated-User
  rbac:
    version: 1                   # 设为 2 启用数据库驱动的自定义角色
```

各子功能默认均为 `false`。仅开启 `members.enabled` 不会自动启用团队或项目。

## 首次设置（bootstrap）

一步创建初始 owner、默认团队与默认项目：

```bash
intellect members bootstrap
# 或指定名称：
intellect members bootstrap --admin-login alice --team family --project default
```

首名成员始终为 **owner**（完整权限）。完成后登录：

```bash
intellect members login alice
```

若尚未设置密码，首次登录时 CLI 会提示创建密码。

## 角色与权限（摘要）

| 角色 | 典型用途 |
|------|----------|
| **owner** | 配置文件所有者——增删成员、启用/停用、授予 owner、重置他人密码 |
| **admin** | 日常管理——发放邀请、管理 API token、绑定 OAuth、团队/项目管理 |
| **member** | 普通用户——加入团队/项目（常需审批）、使用 agent |
| **guest** | 只读或受限操作 |

**仅 owner（CLI）：** `add`、`activate`、`deactivate`、`delete`、`grant-owner`、`reset`。

**owner 或 admin：** `invite`（创建邀请码）。

**持有有效邀请码的任何人：** `register`。

团队/项目成员管理还支持 **团队/项目内管理员** 在全局角色仅为 `member` 时审批加入（双重门控）。完整矩阵见仓库内 `docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md`。

## 成员生命周期（CLI）

| 命令 | 执行者 | 说明 |
|------|--------|------|
| `intellect members add <login> [--name] [--email] [--id]` | owner | 直接添加成员（无需邀请）；`--id` 可指定 member id |
| `intellect members invite [login] [--email] [--ttl] [--id]` | owner、admin | 生成邀请码；`--id` 预留注册时的 member id |
| `intellect members register <code>` | 任何人 | 凭邀请注册 → 选择 id（若未预留）+ 密码 → 自动登录 |
| `intellect members activate <login>` | owner | 重新启用已停用成员 |
| `intellect members deactivate <login>` | owner | 软停用（`enabled=0`，非待审状态） |
| `intellect members delete <login>` | owner | 永久删除并级联清理 |
| `intellect members grant-owner <login>` | owner | 提升为 owner（需确认） |
| `intellect members list` / `show` / `whoami` | 已登录 | 目录与当前身份；`whoami` 显示登录名、角色、团队与项目 |
| `intellect members login <login>` | 已启用成员 | 密码登录；写入 `~/.intellect/.cli-session.json` |
| `intellect members login --oauth <provider>` | 已绑定身份 | OAuth 登录（GitHub、Google、Azure AD、企业微信、钉钉等） |
| `intellect members register <code> [--oauth <provider>]` | 任何人 | 邀请注册；注册时可同时绑定 OAuth |
| `intellect members logout` | 已登录 | 清除 CLI 会话文件 |
| `intellect members passwd` | 本人 / owner | 修改密码 |
| `intellect members bind [--login <login>] --oauth <provider>` | 本人、owner、admin | 绑定 OAuth；管理员可为他人绑定 |
| `intellect members identities <login>` | admin+ | 查看已绑定的 OAuth 身份 |
| `intellect members role …` | owner、admin | `members.rbac.version: 2` 时的自定义角色（见下文） |

删除需输入登录名确认。会移除 `state.db` 中的成员及相关数据、清空 `sessions.member_id`，并在适用时清理文件型成员会话。

## 邀请 → 注册流程

1. **Owner 或 admin** 执行：

   ```bash
   intellect members invite charlie --email charlie@example.com
   # 可选：预留 member id
   intellect members invite charlie --id charlie
   ```

2. 将邀请码（如 `IM-CH-XXXXXXXX`）与有效期（默认由 `invite_ttl_hours` 决定，常为 7 天）发给对方。

3. **新用户** 执行：

   ```bash
   intellect members register IM-CH-XXXXXXXX
   ```

   输入 member id（若邀请含 `--id` 则固定）、显示名和密码（两次）。系统校验邀请码后创建 **已启用** 成员（`enabled=1`）、保存密码哈希并标记邀请已使用。

### OAuth + 邀请

在 **Intellect WebUI**（`/register`）中，OAuth 流程可在 `state` 中携带邀请码，IdP 登录后将新账号与邀请绑定。无邀请的纯 OAuth 注册受 `members.oauth.auto_provision` 控制（默认 `false`，可能需管理员后续添加用户）。

CLI 对应命令：

```bash
intellect members register IM-CH-XXXXXXXX --oauth github
intellect members login --oauth github
intellect members bind alice --oauth github   # 管理员为他人绑定
```

注册过程中若 OAuth 授权被取消，成员账号仍会创建；可稍后使用 `members bind` 补绑。

## CLI 与 Gateway 登录

| 场景 | 登录 | 登出 |
|------|------|------|
| **CLI** | `intellect members login <login>` 或 `login --oauth <provider>` | `intellect members logout` |
| **Gateway**（Telegram、Slack 等） | Slash `/login <login>` 或 `/login <member_id>`（按会话 sticky） | `/logout` |
| **API / WebUI** | Bearer `imt_…` 或 WebUI 会话 cookie | 在 UI 撤销 token，或 CLI `members logout` |

Gateway 的 `/login` 将 `session:{session_key}:member_id` 写入 `state_meta`（与 `/team`、`/project` 相同）。未 sticky 登录时，仍可通过 `identities` 表用平台用户 id 解析成员。

在消息平台上 `/whoami` 会显示成员登录名、全局角色与在线状态（需 `members.enabled`）。

**guest** 角色可使用允许的只读能力，但 **不能发起 agent 对话**（API Server 与 Gateway 校验 `Action.CHAT`）。在已解析成员上下文时，工具层会拦截会修改状态的操作（memory 写入、cronjob、写入 `~/.intellect` 下项目 `.env` 等路径）。

## 数据库驱动 RBAC（v2）

v1（默认）使用成员行上的固定角色 `owner` / `admin` / `member` / `guest`。v2 通过 `role_definitions` 与带作用域的 `member_role_bindings` 支持自定义权限（全局或团队/项目级）：

```yaml
members:
  enabled: true
  rbac:
    version: 2
```

```bash
intellect members role list
intellect members role create doc-editor --permissions chat,read,team:member:list
intellect members role grant charlie doc-editor --scope project --id web-app
intellect members role revoke charlie doc-editor --scope project --id web-app
```

内置角色在 schema 升级时自动 seed。owner 始终拥有全部权限。

## 企业 SSO（trusted header）

若由反向代理完成用户认证（OAuth2 Proxy、Authentik、企业网关等），可配置：

```yaml
members:
  oauth:
    trusted_header:
      enabled: true
      header: X-Authenticated-User   # 或 X-Forwarded-User 等
```

Gateway 与 API Server 将头字段映射到成员登录名或 id（成员须已存在，除非启用 auto-provision）。请运行 `intellect doctor` 检查配置。

除 GitHub/Google/Gitee 外，还支持 **Azure AD**（`azure_ad`）、**企业微信**（`wecom`）、**钉钉**（`dingtalk`）等 OAuth preset。

## 本地自助注册与审批

当 `members.registration.local_requires_approval` 为 `true`（默认）时：

| 状态 | `enabled` | `registration_pending` | 含义 |
|------|-----------|------------------------|------|
| 本地注册待审 | `0` | `1` | 等待 profile 管理员审批 |
| 已停用 | `0` | `0` | 已禁用（不在待审列表） |
| 正常 | `1` | `0` | 可登录 |

- **本地注册**（WebUI `/register` 本地标签或对应 API）创建 `registration_pending=1` 的行。
- **OAuth 预注册** **不**进入此队列，而是通过 OAuth `state` 中的短期 `registration_token` 完成。
- **批准** 设置 `enabled=1` 并清除 `registration_pending`。
- **拒绝** 会 **物理删除** 待审行（API 可能返回 `status: "deleted"`，仅为响应标签，非数据库状态列）。

仅全局角色 **`owner`** 与 **`admin`** 可审批/拒绝本地待审注册并**创建邀请码**。不能删除成员或授予 owner（后者仅 owner）。**admin 不能对 `owner` 账号执行启用/停用或重置密码**，这些操作需由已登录的 **owner** 对其它 owner 执行。WebUI 在 **Members** 面板操作待审队列与邀请。

**显示名（display_name）** 在同一配置文件内必须唯一；注册检查 API 与服务器都会拒绝重复显示名。

若希望本地注册立即可用，可设置：

```yaml
members:
  registration:
    local_requires_approval: false
```

## Intellect WebUI

[Intellect WebUI](https://github.com/ONTOWEB/intellect-webui) 在端口 **9119** 嵌入同一套成员体系（不是 9009 端口的旧 dashboard）。

| 页面 / 面板 | 用途 |
|-------------|------|
| `/login` | OAuth、成员 id + 密码、本机 dev 选择器 |
| `/register` | 本地账号、OAuth 或邀请码 |
| **Members** 面板 | 邀请、API token、身份绑定、**待审本地注册** |
| **Teams** / 标题栏团队下拉 | 加入团队、切换当前团队（`X-Intellect-Team`） |
| 请求头 | 当前团队与项目（`X-Intellect-Project`） |

反向代理后请将 `members.oauth.callback_base_url` 设为 WebUI 的对外源地址，OAuth 回调 URI 必须完全一致。

成员 **账号密码** 与可选的 **WebUI 访问密码**（`INTELLECT_WEBUI_PASSWORD`）相互独立。多用户模式下，在非本机访问时，有效成员会话通常即可通过门禁，不必每位用户再输入共享 WebUI 密码。

## 团队与项目（概览）

启用 `members.teams.enabled` 与 `members.projects.enabled` 后：

- **团队** — 在 `~/.intellect/teams/<team-id>/` 下共享 SOUL、技能、env 与工作区。通过 `intellect teams join <id>` 加入（常处于 **pending**，需团队管理员批准）。
- **项目** — 在 `~/.intellect/projects/<project-id>/` 下各有 `SOUL.md`、`CONVENTIONS.md`、`.env` 与 `workspace/`。通过 `intellect projects join <id>` 加入，审批流程类似。

CLI 示例：

```bash
intellect teams list
intellect teams join my-team
intellect projects list
intellect projects join my-app
```

Gateway 与 API 请求可通过 `X-Intellect-Team`、`X-Intellect-Project` 及成员 Bearer token（`imt_…`）固定上下文。HTTP 集成与会话头见 [API 服务器](./api-server)。

## 记忆范围

```yaml
members:
  memory_scope: profile   # 默认 — 成员共享 MEMORY.md
  # memory_scope: member  — 隔离到 members/<id>/memories/
```

## 相关文档

- [配置文件（Profiles）](../profiles) — 同一机器上的多个 Intellect 主目录
- [API 服务器](./api-server) — 兼容 OpenAI 的 HTTP 与团队/项目/成员上下文头
- [安全](../security) — 密钥、token 与加固
- 开发者规格：`docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md`、`docs/plans/member-management-redesign.md`

## 故障排查

| 现象 | 可能原因 |
|------|----------|
| 待审用户不出现在队列 | 账号为停用状态（`registration_pending=0`）或走了 OAuth 预注册流程 |
| WebUI 无法按 id 删除成员 | id 可能为保留字（如 `admin`）；改用登录名或迁移 id |
| 注册时邀请无效 | 码已过期、已使用，或预留 id 已被占用 |
| OAuth 重定向失败 | `callback_base_url` 须与浏览器地址一致（含 `localhost` 与 `127.0.0.1` 区别） |
| Gateway 提示需要 linked member | 使用 `/login`、绑定平台 identity、传入 `imt_…`，或启用 trusted header SSO |
| 工具返回 Permission denied（guest） | guest 无 `chat`/写权限；请管理员调整角色或授予 v2 作用域角色 |
| OAuth 登录提示账号未绑定 | 使用 `register --oauth` 或密码登录后执行 `members bind` |

修改配置后可运行 `intellect doctor`；在 `members.enabled` 为 true 时会包含成员与 OAuth 健康检查。
