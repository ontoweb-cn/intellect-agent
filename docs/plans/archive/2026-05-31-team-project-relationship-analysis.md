# Team 与 Project 的关系：完整分析

> **关联 Spec:** [2026-05-31-profile-teams-members-projects-spec-v2.md](./2026-05-31-profile-teams-members-projects-spec-v2.md)
> **日期:** 2026-05-31

---

## 1. 哲学层面：两个正交维度

Team 和 Project 不是"谁包含谁"的层级关系，而是对协作空间的**两个独立切面**：

```
                    Member: alice
                   /            \
          Team: kitchen      Project: web-app
          (和谁协作)          (做什么工作)
```

- **Team 回答"我是谁，我和谁一起"** — 人格化的协作身份。Team SOUL 从成员的个人 SOUL 合成而来，描述的是这群人共同的价值观、沟通风格、决策偏好。
- **Project 回答"我在做什么，怎么做"** — 非人格化的工作上下文。Project SOUL 描述技术栈、代码规范、架构决策，可能来自 `CLAUDE.md`、`README.md` 等文件。

**类比**：如果你用 GitHub，Team 类似于 GitHub 的 Team（一群人），Project 类似于 GitHub 的 Repository（一个代码库）。一个人可以在多个 Team 中，也可以访问多个 Repository，两者没有必然的包含关系。

---

## 2. 数据模型：可选的多对多

```
┌──────────┐     project_memberships      ┌──────────┐
│  Member  │◄────────────────────────────►│  Project │
└──────────┘                               └──────────┘
     ▲                                         ▲
     │ team_memberships                   project_teams
     │ (必选：member 必须属于至少一个       │ (可选：project 可以不属于
     │  team，如果 teams.enabled)          │   任何 team)
     ▼                                         ▼
┌──────────┐                               ┌──────────┐
│   Team   │────────────────────────────►│  Project │
└──────────┘     (可选多对多)              └──────────┘
```

三个关键设计：

- **`project_memberships`** — Member 和 Project 的直接多对多关系，与 Team 无关。一个 Member 可以直接加入一个 Project，不需要先属于某个 Team。
- **`project_teams`** — Team 和 Project 的可选多对多链接。仅用于：(a) 可见性继承（`visibility: team_linked` 时，Team 成员自动获得 Project 只读权限），(b) 默认 Project 推荐（当 Member 在 Team 中且未指定 Project 时，提示该 Team 关联的 Project）。
- **`project.yaml` 中的 `default_team`** — 单向的便利字段，仅影响 Project 分辨率第5步（"如果已知 team_id，查找该 team 的默认 project"）。

---

## 3. 运行时组合：三个上下文的叠加

当三个维度同时存在时，Agent 获得的是一个**叠加的上下文**，而不是三选一：

### System Prompt 组装顺序

```
┌──────────────────────────────────────┐
│ 1. Profile SOUL (可选，全局前言)       │  ← "我是一个有帮助的AI助手"
│ 2. Team SOUL (合成或手动)             │  ← "我和Alice、Bob协作，我们注重代码质量"
│ 3. Project SOUL (手动/生成)           │  ← "我们在用React+TypeScript，遵循X规范"
│ 4. Member SOUL (个人)                │  ← "我是Alice，后端工程师，偏好简洁方案"
└──────────────────────────────────────┘
```

### 资源合并顺序

```
skills: profile → team → project → member   (越靠近工作的越优先)
.env:   member → team → project → profile   (越靠近个人的越优先)
cwd:    project > team > personal            (project workspace 优先)
memory: member only                          (不合并，始终跟随 member)
```

**有意思的设计**：skills 和 env 的合并顺序是相反的。Skills 是"越靠近工作的越优先"（project 覆盖 team 覆盖 profile），而 env 是"越靠近个人的越优先"（member 覆盖 project 覆盖 profile，除非配置了 `prefer_personal`）。这反映了安全考量：个人的 API key 不应该被 project 级别的 key 意外覆盖。

---

## 4. 四种典型配置场景

### 场景 A：完整协作（Team + Project）

```
Alice → Team: engineering → Project: web-app
Bob   → Team: engineering → Project: web-app
```

- Alice 和 Bob 在同一个 Team 中，协作构建同一个 Project
- Agent 同时加载 Team SOUL（两人合成的协作人格）和 Project SOUL（web-app 的技术上下文）
- cwd = `projects/web-app/workspace/`
- 这是"我们在 engineering 团队里一起做 web-app"的完整上下文

**适用**：公司团队，多人协作一个代码库。

### 场景 B：仅 Team，无 Project

```
Alice → Team: family
Bob   → Team: family
```

- 家庭成员之间的一般性对话
- 没有 Project SOUL，没有 project workspace
- cwd = `members/<id>/workspace/`（个人目录）
- 这是"我和家人聊天"的场景，不需要特定的工作上下文

**适用**：家庭、兴趣小组、一般性讨论。

### 场景 C：仅 Project，无 Team

```
Alice → Project: blog (solo project)
Alice → Project: startup-idea (solo side project)
```

- Alice 没有团队（`teams.enabled: false` 或她没有加入任何 Team）
- 她有两个个人 Project，各自有独立的 workspace 和 skills
- 没有 Team SOUL（只有 Profile + Project + Member 三层）
- 这是"我在做我的个人博客"的场景

**适用**：个人开发者、独立创作者。

### 场景 D：跨 Team 的 Project

```
Alice → Team: engineering → Project: shared-infra
Alice → Team: platform     → Project: shared-infra
Bob   → Team: engineering → Project: shared-infra
```

- Project `shared-infra` 同时链接了 `engineering` 和 `platform` 两个 Team
- Alice 以不同 Team 身份访问同一个 Project 时，Team SOUL 不同（engineering 的协作风格 vs platform 的协作风格），但 Project SOUL 和 workspace 相同
- 关键洞察：**同一个 Project + 不同 Team = 不同的协作上下文 + 相同的工作内容**

**适用**：跨团队的基础设施项目。

---

## 5. 为什么不设计成层级关系？

一个自然的疑问是"为什么不把 Project 放在 Team 下面？"比如：

```
Team: engineering
  └── Project: web-app
  └── Project: mobile-app
```

这个设计在直觉上很合理（"工程团队有两个项目"），但我们选择了正交设计，原因如下：

| 场景 | 层级设计的困境 | 正交设计的处理 |
|------|---------------|---------------|
| 个人项目（Alice 做 side project，没有团队） | Project 必须挂在某个 Team 下 → 需要创建"伪 Team" | `teams.enabled: false` + `projects.enabled: true`，Project 直接对 Member |
| 跨团队项目（infra 项目被 engineering 和 platform 共用） | Project 只能属于一个 Team → 权限和可见性难以共享 | `project_teams` 多对多，两个 Team 都可以关联同一个 Project |
| 团队重组（engineering 拆分为 frontend 和 backend，但 project 不变） | 需要迁移 Project 的归属 → 破坏 session key 和缓存 | Project 独立存在，只需更新 Team 关联 |
| 无团队协作（两个人临时合作一个 Project，没有正式的 Team） | 需要创建临时 Team → 仪式感过重 | Project 直接添加两个 Member，不需要 Team |

**核心原则**：Team 是可选的（已经有 `teams.enabled: false` 模式），Project 也应该是可选的。两者各自独立开关，任意组合。

---

## 6. 与主流产品的对比

| 产品 | Team/Group 概念 | Project/Repo 概念 | 关系 |
|------|----------------|-------------------|------|
| **GitHub** | Team（Organization 下的用户组） | Repository | 多对多（Team 可以访问多个 Repo，Repo 可以被多个 Team 访问） |
| **Linear** | Team（功能团队） | Project（跨团队的工作集合） | 多对多（Project 可以跨越多个 Team） |
| **Bitwarden** | Group（用户组） | Project（Secret 容器） | Group 通过 Access Policy 关联到 Project |
| **Notion** | Teamspace | 数据库/页面 | 页面属于一个 Teamspace（层级），但可以跨 Teamspace 链接 |
| **Intellect** | Team（协作组） | Project（工作上下文） | 正交多对多（本设计） |

可以看出，**GitHub/Linear/Bitwarden 都选择了正交设计**，而非层级设计。这是经过大规模实践验证的模式。

---

## 7. 设计中的一个微妙张力

有一个值得注意的点：**Project 需要 Team 上下文才能完全发挥协作功能**。

```
仅 Project，无 Team:
  - 有 workspace ✓
  - 有 project skills ✓
  - 有 project SOUL ✓
  - 有 project env ✓
  - 没有 team SOUL ✗  ← Agent 不知道"和谁一起，以什么风格"
  - 没有 group chat context ✗

仅 Team，无 Project:
  - 有 team SOUL ✓
  - 有 chat context ✓
  - 没有 project workspace ✗ ← Agent 不知道"代码在哪"
  - 没有 project conventions ✗
```

**这是刻意的设计，而非缺陷。** 它允许用户按需组合：
- 想聊天？用 Team。
- 想写代码？加个 Project。
- 一个人写代码？只需要 Project。
- 一个人聊天？用默认的 single-user 模式就行。

---

## 8. 最终结论

Team 和 Project 的关系可以总结为一句话：

> **Team 决定 Agent "以什么身份、和谁一起"说话，Project 决定 Agent "在什么环境下、做什么事"。两者可以独立存在，也可以任意组合。当它们同时存在时，Team 提供人格层，Project 提供工具层，彼此互补而非冲突。**

这对实现的影响是：代码层面 Team 和 Project 应该共享尽可能多的模式（membership 的 CRUD、approval flow、directory template），但在运行时上下文中保持独立的分辨率和合并路径。这也是为什么 spec 中 Project 的实现大量复用了 Team 的模式（如 §27 的 membership 状态机、§6 的 schema 结构），但在 §10.2 和 §11.2 中有独立的分辨率和 SOUL 逻辑。

---

*End of analysis.*
