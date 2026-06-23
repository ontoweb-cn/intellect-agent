---
sidebar_position: 3
---

# 配置模型

Intellect 使用两类模型槽位：

- **主模型** — agent 的思考核心。每条用户消息、每个工具调用循环、每次流式响应都经由该模型处理。
- **辅助模型** — agent 卸载给较小模型的边缘任务。包括上下文压缩、视觉（图像分析）、网页摘要、审批评分、MCP 工具路由、会话标题生成和技能搜索。每项任务有独立槽位，可单独覆盖。

本页介绍如何通过 CLI 和配置文件配置上述两类模型。

:::tip 最快路径：ONTOWEB Portal
[ONTOWEB Portal](/user-guide/features/tool-gateway) 在单一订阅下提供 300+ 个模型。全新安装后，运行 `intellect setup --portal` 即可登录并一键将 OntoWeb 设为提供商。使用 `intellect portal status` 查看当前配置。

- Portal 订阅用户还可享受**代币计费提供商 10% 折扣**。
:::

:::note `model:` schema — 空字符串 vs. 映射
全新安装时，内置默认配置中 `model: ""`（空字符串哨兵值，表示"尚未配置"）。首次运行 `intellect setup` 或 `intellect model` 后，该键会被原地升级为包含 `provider`、`default`、`base_url` 和 `api_mode` 子键的映射——即本页及 [`profiles.md`](./profiles.md) / [`configuration.md`](./configuration.md) 中展示的格式。如果你在 `config.yaml` 中看到空字符串，运行 `intellect model` 即可自动写入字典形式。
:::

## 设置主模型

### `intellect model` — 标准方式

```bash
intellect model            # 交互式提供商 + 模型选择器
```

`intellect model` 引导你选择提供商、完成认证（OAuth 流程会打开浏览器；API key 提供商会提示输入密钥），然后从该提供商的精选目录中选择具体模型。选择结果写入 `~/.intellect/config.yaml` 的 `model.provider` 和 `model.model` 字段。

### `/model` 斜杠命令（会话内切换）

在任意 `intellect chat` 会话内：

```
/model gpt-5.4 --provider openrouter             # 仅当前会话
/model gpt-5.4 --provider openrouter --global    # 同时持久化到 config.yaml
```

`--global` 会持久化到 `config.yaml`，同时原地切换当前运行中会话的模型。

### `intellect setup`

完整的交互式设置向导也可用于模型配置：

```bash
intellect setup model   # 仅模型部分
intellect setup         # 完整向导
```

### 直接编辑配置文件

编辑 `~/.intellect/config.yaml` 后重启相关服务：

```yaml
model:
  provider: openrouter
  default: anthropic/claude-opus-4.7
  base_url: ''        # 切换提供商时清空
  api_mode: chat_completions
```

完整 schema 请见[配置参考](./configuration.md)。

## 设置辅助模型

每项辅助任务默认为 `auto`，即 Intellect 对该任务也使用主模型。当某个边缘任务需要更便宜或更快的模型时，可单独覆盖该槽位。

### 通过 `intellect model` 配置辅助模型

无需手动编辑 YAML，运行 `intellect model` 并选择**"Configure auxiliary models"**菜单项。你将获得交互式的逐任务选择器：

```
$ intellect model
→ Configure auxiliary models

[ ] vision               当前: auto / 主模型
[ ] web_extract          当前: auto / 主模型
[ ] title_generation     当前: openrouter / google/gemini-3-flash-preview
[ ] compression          当前: auto / 主模型
[ ] approval             当前: auto / 主模型
[ ] triage_specifier     当前: auto / 主模型
[ ] kanban_decomposer    当前: auto / 主模型
[ ] profile_describer    当前: auto / 主模型
```

选择任务、选择提供商（OAuth 流程会打开浏览器；API key 提供商会提示输入）、选择模型。更改将持久化到 `config.yaml` 的 `auxiliary.<task>.*`。

### 常见覆盖模式

| 任务 | 何时覆盖 |
|---|---|
| **Title Gen（标题生成）** | 几乎总是。$0.10/M 的 flash 模型生成会话标题的效果与 Opus 相当。默认配置在 OpenRouter 上将此项设为 `google/gemini-3-flash-preview`。 |
| **Vision（视觉）** | 当主模型是不支持视觉的编程模型时。将其指向 `google/gemini-2.5-flash` 或 `gpt-4o-mini`。 |
| **Compression（压缩）** | 当你在用 Opus/M2.7 的推理 token 来摘要上下文时。快速聊天模型以 1/50 的成本即可完成此工作。 |
| **Approval（审批）** | 用于 `approval_mode: smart` — 由快速/廉价模型（haiku、flash、gpt-5-mini）决定是否自动批准低风险命令。此处使用昂贵模型是浪费。 |
| **Web Extract（网页提取）** | 当你大量使用 `web_extract` 时。逻辑同压缩 — 摘要任务不需要推理能力。 |
| **Skills Hub（技能中心）** | `intellect skills search` 使用此槽位。通常保持 `auto` 即可。 |
| **MCP** | MCP 工具路由。通常保持 `auto` 即可。 |

### 直接配置：辅助模型覆盖

**主模型：**
```yaml
model:
  provider: openrouter
  default: anthropic/claude-opus-4.7
  base_url: ''        # 切换提供商时清空
  api_mode: chat_completions
```

**辅助覆盖示例（视觉任务使用 gemini-flash）：**
```yaml
auxiliary:
  vision:
    provider: openrouter
    model: google/gemini-2.5-flash
    base_url: ''
    api_key: ''
    timeout: 120
    extra_body: {}
    download_timeout: 30
```

**辅助任务处于 auto（默认）：**
```yaml
auxiliary:
  compression:
    provider: auto
    model: ''
    base_url: ''
    # ... 其他字段不变
```

`provider: auto` 加 `model: ''` 表示 Intellect 对该任务使用主模型。

## 何时生效？

- **CLI**（`intellect chat`）：下次执行 `intellect chat` 时生效。
- **Gateway**（Telegram、Discord、Slack 等）：下一个*新*会话生效。现有会话保持原有模型。如需强制所有会话使用新配置，重启 gateway（`intellect gateway restart`）。

更改不会使运行中会话的 prompt 缓存失效。这是有意为之：在会话内切换主模型需要重置缓存（系统 prompt 包含模型特定内容），该操作保留给聊天内的显式 `/model` 斜杠命令。

## 故障排查

### 主模型在运行中的聊天里未发生变化

符合预期。配置更改仅对新会话生效。当前打开的聊天是一个活跃的 agent 进程 — 它保持启动时的模型。在聊天内使用 `/model <name>` 对该会话进行热切换。

### 辅助覆盖"未生效"

检查以下三点：

1. **是否启动了新会话？** 现有聊天不会重新读取配置。
2. **`provider` 是否设置为非 `auto` 的值？** 若 `provider: auto`，该任务仍在使用主模型。显式设置一个实际的提供商。
3. **提供商是否已认证？** 若将 `minimax` 分配给某任务但没有 MiniMax API key，该任务将回退到 openrouter 默认值，并在 `agent.log` 中记录警告。

### 我选择了模型，但 Intellect 切换了提供商

在 OpenRouter（或任何聚合器）上，裸模型名称会优先在聚合器内解析。因此 OpenRouter 上的 `claude-sonnet-4` 会解析为 `anthropic/claude-sonnet-4.6`，保持在你的 OpenRouter 认证下。但若在原生 Anthropic 认证下输入 `claude-sonnet-4`，则会保持为 `claude-sonnet-4-6`。若出现意外的提供商切换，请确认当前提供商是否符合预期。

## 自定义别名

为常用模型定义短名称，然后在 CLI 或任意消息平台中使用 `/model <alias>`。有两种等价格式 — 按工作流选择。

**标准格式（顶层 `model_aliases:`）** — 可完整控制 provider + base_url：

```yaml
# ~/.intellect/config.yaml
model_aliases:
  fav:
    model: claude-sonnet-4.6
    provider: anthropic
  grok:
    model: grok-4
    provider: x-ai
```

**短字符串格式（`model.aliases.<name>: provider/model`）** — 通过 shell 设置更方便，因为 `intellect config set` 只写入标量值，但无法携带自定义 `base_url`：

```bash
intellect config set model.aliases.fav anthropic/claude-opus-4.6
intellect config set model.aliases.grok x-ai/grok-4
```

两种格式使用同一加载器（`intellect_cli/model_switch.py`）。`model_aliases:` 中声明的条目优先于同名 `model.aliases:` 条目。

然后在聊天中使用 `/model fav` 或 `/model grok`。用户别名会覆盖内置短名称（`sonnet`、`kimi`、`opus` 等）。完整参考请见[自定义模型别名](/reference/slash-commands#custom-model-aliases)。

查看 CLI 当前实际使用的配置：`intellect config show | grep '^model\.'` 和 `intellect status`。
