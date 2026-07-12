---
sidebar_position: 6
title: "LLM Wiki 与 Vault"
description: "按作用域划分的 Markdown 知识库，以及 Intellect WebUI 中的 Quartz Vault 浏览"
---

# LLM Wiki 与 Vault

Intellect 内置 **[llm-wiki](/user-guide/skills/bundled/research/research-llm-wiki)** 技能，用于构建和维护 **Karpathy 风格的 LLM Wiki**——由 Agent 持续编译的互联 Markdown 文件目录。与一次性 RAG 检索不同，维基会**复利增长**：交叉引用、矛盾标注与综合结论跨会话保留。

在 **Intellect WebUI** 中，左侧 Rail 的 **Wiki** 标签可将维基浏览为 **Quartz Vault** 站点，触发构建并初始化缺失目录。

:::tip 技能页 vs 功能页
本页说明**产品行为**（路径、WebUI、权限、Vault）。Agent 完整指令见 bundled 技能参考：[Llm Wiki](/user-guide/skills/bundled/research/research-llm-wiki)。
:::

## 与 Memory、RAG 的区别

| 系统 | 存储内容 | 适用场景 |
|------|----------|----------|
| **[持久化记忆](memory.md)** | `MEMORY.md` / `USER.md` 中的短笔记 | 偏好、环境事实、用户画像 |
| **[RAG 提供商](rag-providers.md)** | 外部文档语料与向量检索 | PDF、手册、大型静态资料库 |
| **LLM Wiki** | Agent 维护的 Markdown 页面 + 不可变 `raw/` 源 | 研究笔记、实体页、演进中的综合结论 |

三者可同时启用。维基即磁盘上的 Markdown，可用 Obsidian、VS Code 等任意编辑器打开。

## 按作用域划分的维基路径

启用 [团队、项目与成员](teams-and-members.md) 后，每次对话会话从上下文解析**唯一活跃维基**。Agent 运行时在工具调用前注入：

| 变量 | 含义 |
|------|------|
| `WIKI_PATH` | 目标维基目录（可能尚不存在） |
| `WIKI_SCOPE` | `project` \| `team` \| `member` \| `global` |
| `WIKI_SCOPE_ID` | 团队/项目 slug 或成员 id；global 为空 |
| `WIKI_WRITE_MODE` | `read_write` 或 `read_only` |

**解析顺序**（默认会话，非显式 global 目标）：

1. **项目** — `$INTELLECT_HOME/projects/{slug}/wiki/`
2. **团队** — `$INTELLECT_HOME/teams/{slug}/wiki/`（无活跃项目时）
3. **成员** — `$INTELLECT_HOME/members/{id}/wiki/`（已登录且无团队/项目）
4. **旧版全局** — `~/wiki` 或 `skills.config.wiki.path`（仅单用户模式）

**组织（Global）维基** — `$INTELLECT_HOME/wiki/global/`：

- **全员可读**（Vault + Agent 读取）。
- **仅 `owner` / `admin` 可直接写入**。普通成员在 global 作用域下为 `WIKI_WRITE_MODE=read_only`。
- **团队与项目维基**（v1）：可访问该团队/项目的成员均可读写。

目录采用**预期路径**——在首次初始化或写入前，文件夹不存在是正常的。

:::info 多租户下 `.env` 中的 `WIKI_PATH`
多成员配置下，自动作用域会忽略 profile `.env` 中的 `WIKI_PATH`，运行时始终注入上表路径。单用户安装仍可通过 `skills.config.wiki.path` 或 `WIKI_PATH` 指定旧版全局位置。
:::

## 维基目录结构

```
wiki/
├── SCHEMA.md           # 约定与标签分类
├── index.md            # 分节目录与一行摘要
├── log.md              # 仅追加的操作日志
├── raw/                # 不可变来源（文章、论文、转录）
├── entities/           # 实体页
├── concepts/           # 主题页
├── comparisons/        # 对比分析
└── queries/            # 值得保留的查询结果
```

Agent 每次维基会话开始前会读取 `SCHEMA.md`、`index.md` 及 `log.md` 近期条目，再进行摄取或编辑。

## Intellect WebUI — Wiki 面板

点击左侧 Rail 的 **Wiki** 图标（位于 Chat 与 Tasks 之间）。布局与 Skills/Memory 一致：

| 区域 | 作用 |
|------|------|
| **Rail** | Wiki 入口 |
| **侧栏目录**（`panelWiki`） | 分组：个人、团队、项目、**组织（Global）** |
| **主视图**（`mainWiki`） | Quartz Vault iframe、构建控制、缺失时初始化 |

### 侧栏目录

`GET /api/wiki/catalog` 返回你可访问的所有作用域：显示名称（不仅是 slug）、维基状态徽章（`Ready`、`Building`、`Empty`、`Missing` 等）及 Vault 构建状态。

选中一行即加载对应 Vault URL：

| 作用域 | Vault 路径 |
|--------|------------|
| 个人（成员） | `/vault/m/{member_id}/` |
| 团队 | `/vault/t/{slug}/` |
| 项目 | `/vault/p/{slug}/` |
| Global | `/vault/global/` |

### 构建与初始化

- **Rebuild** — 对当前作用域 `POST /api/wiki/build`；轮询 `GET /api/wiki/build/status`。
- **Initialize Wiki** — 作用域目录缺失时，`POST /api/wiki/init` 脚手架 `SCHEMA.md`、`index.md`、`log.md` 与各层文件夹（与 `intellect_cli.wiki_scaffold` 相同）。
- **新标签页打开** — 在新浏览器标签打开 Vault URL。

定时重建通过 gateway cron + `intellect vault tick`（见下文 [Vault 调度](#vault-调度)）。

### Insights 卡片

**Insights** 标签另有精简 **LLM Wiki** 状态卡（`GET /api/wiki/status`）：条目数、最后写入者、红绿灯可用性、启用/禁用（`POST /api/wiki/toggle`）及快速重建——不在 Wiki 面板时同样有用。

## Global 维基（单用户）

Intellect Agent **永久单用户**。不再提供多用户贡献审核队列（`/api/wiki/contributions` 未挂载）。

请写入个人 / profile 维基（`~/wiki` 或 `skills.config.wiki.path`）。旧版多用户布局下的 `wiki/global/` 除非你在磁盘上手动维护，否则不会使用。

## 配置

```yaml
# ~/.intellect/config.yaml（示例）
skills:
  config:
    wiki:
      enabled: true
      path: ~/wiki          # 单用户旧版默认；多成员自动作用域下会被覆盖

vault:
  routing:
    enabled: true           # 由 WebUI 提供 /vault/* 静态站
  build_trigger: scheduled  # 或 manual
  build_cron: "0 3 * * *"   # scheduled 时使用
```

**转发到 Agent 运行的环境变量**（Docker 与 WebUI）：

- `WIKI_PATH`、`WIKI_SCOPE`、`WIKI_SCOPE_ID`、`WIKI_WRITE_MODE`、`WIKI_SKILL_VERSION`

**CLI 调度：**

```bash
intellect vault tick              # 执行一次定时构建
intellect vault tick --force      # 重建所有符合条件的 Vault
intellect vault tick --json       # 机器可读输出
```

外部调度器也可调用 WebUI `POST /api/vault/tick`。

## 在对话中使用技能

可向 Agent 提出：

- 为某领域创建或初始化维基
- 将论文、文章或会议记录摄取到 `raw/`
- 基于已有维基页面回答问题
- 检查维基健康（断链、过时 `index.md`、孤立页）

可附加 `/llm-wiki` 或在会话设置中启用该技能以显式加载。

隔离仅靠 **profile**（`INTELLECT_HOME` / `intellect -p`）；多用户 members/teams 不可用。

## 相关文档

- [Llm Wiki 技能参考](/user-guide/skills/bundled/research/research-llm-wiki) — Agent 可见的完整 SKILL.md
- [Profiles](../profiles.md) — 按实例隔离
- [RAG 提供商](rag-providers.md) — 文档语料检索（互补）
- [Obsidian 技能](/user-guide/skills/bundled/note-taking/note-taking-obsidian) — 可选 vault 同步模式

## 故障排除

| 现象 | 可能原因 |
|------|----------|
| Wiki 面板显示 **Missing** | 作用域目录未初始化 — 使用 **Initialize Wiki** 或让 Agent 创建 |
| 构建后 Vault iframe 空白 | 构建失败 — 查看状态徽章；Rebuild；确认 `vault.routing.enabled` |
| Vault 内容陈旧 | 触发 Rebuild 或等待 `intellect vault tick` / 定时 cron |

配置变更后运行 `intellect doctor`。WebUI 运行时可从状态 API 查看维基与 Vault 健康情况。
