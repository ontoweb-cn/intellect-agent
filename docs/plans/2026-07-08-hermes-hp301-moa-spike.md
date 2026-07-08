# HP-301 Spike: MoA 虚拟 Provider 拦截点选型

> 日期：2026-07-08
> 状态：DRAFT — 待评审签字
> 依赖：无
> 产出：本文档（≤5 页）

## 1. 现状分析：`mixture_of_agents` 工具 vs 虚拟 Provider

| 维度 | 现有工具（`tools/mixture_of_agents_tool.py`） | 目标虚拟 Provider（`moa/<preset>`） |
|------|------|------|
| **调用方式** | 必须显式调用 `mixture_of_agents` 工具 | 在任何模型选择器中输入 `moa/default` |
| **Provider 绑定** | **硬绑定 OpenRouter**（`_get_openrouter_client()`） | 解除绑定：参考模型走各自 provider 的 `auxiliary_client` |
| **模型选择** | 硬编码常量（4 参考模型 + 1 聚合器） | preset YAML 配置，可被 `/model`、cron、auxiliary 选中 |
| **流式** | ❌ 不支持 | ✅ 需支持聚合器流式输出 |
| **工具调用** | ❌ 不支持 | ❌ 首版不支持（与 Hermes 一致） |
| **Token 计量** | 无 | Rust `TokenAccumulator` 统一计费 |
| **可发现性** | `_DEFAULT_OFF_TOOLSETS`，需手动开启 | 一等 provider，在 `/model` 列表可见 |
| **共存策略** | 保留为 legacy alias，文档指向虚拟 provider | — |

**结论**：现有工具保留为 `mixture_of_agents` legacy alias（`/moa` 命令重定向），新实现以虚拟 provider 为主路径。

## 2. 拦截点 A：`agent/agent_init.py` Client 工厂

### 2.1 位置

`init_agent()` L296–327：provider 名 → `api_mode` 决策 + L595+ client 构建。

### 2.2 时序

```text
用户/配置模型名字符串 (e.g. "moa/default")
       │
       ▼
┌─────────────────────────────────────────────────────┐
│  init_agent()                                       │
│  ┌─────────────────────────────────────────────┐    │
│  │ 1. provider 名 → api_mode 决策 (L296-327)   │    │
│  │    - "anthropic" → anthropic_messages       │    │
│  │    - "bedrock" → bedrock_converse           │    │
│  │    - default → chat_completions             │    │
│  │                                              │    │
│  │ ★ 拦截点：识别 "moa/" 前缀                   │    │
│  │    → 设置 agent.api_mode = "moa"            │    │
│  │    → 跳过 OpenAI/Anthropic client 构建       │    │
│  │    → 加载 preset 配置                        │    │
│  └─────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────┐    │
│  │ 2. model normalize (L337-346)                │    │
│  │    normalize_model_for_provider()            │    │
│  │    ★ MoA 需豁免 normalize（preset name 不是  │    │
│  │      真实模型名）                             │    │
│  └─────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────┐    │
│  │ 3. client 构建 (L595+)                        │    │
│  │    switch(api_mode):                          │    │
│  │      anthropic_messages → build_anthropic    │    │
│  │      bedrock_converse → boto3                │    │
│  │      chat_completions → OpenAI()             │    │
│  │      ★ moa → build_moa_runner(preset)        │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
       │
       ▼
agent.client  = MoARunner 实例（替代 OpenAI client）
agent.api_mode = "moa"
```

### 2.3 风险评估

| 风险 | 级别 | 缓解 |
|------|------|------|
| `api_mode = "moa"` 需在所有 switch 分支中处理（`build_api_kwargs`、`interruptible_api_call`、`interruptible_streaming_api_call`、context compressor、fallback 等） | 中 | 新增 `agent/transports/moa.py` transport 注册，统一 `build_kwargs` + `call` 接口 |
| MoA 绕过了 OpenAI client 构建路径，影响 `resolve_provider_client()` 的凭证解析 | 低 | MoA preset 中每个参考模型指定 provider，各自走 `auxiliary_client.call_llm()` 独立解析凭证 |
| `fallback_model` 链中混入 `moa/` 前缀需特殊处理 | 低 | `try_activate_fallback()` 中检测 `moa/` 时跳过（成本 N+1 不能作为 fallback） |
| `agent.model` 在系统中多处被读取（banner、logging、token tracking） | 低 | 保留 `agent.model = "moa/default"` 原始字符串，banner 友好展示 |

**结论**：拦截点 A 可行。优先在 `api_mode` 决策层（L327 之后）插入 `moa/` 检测，新增 transport。

## 3. 拦截点 B：`chat_completion_helpers.py` 请求入口

### 3.1 位置

`build_api_kwargs()` L550 + `interruptible_api_call()` L129 / `interruptible_streaming_api_call()` L1572。

### 3.2 时序

```text
conversation_loop._run_agent_turn()
       │
       ▼
build_api_kwargs(agent, api_messages)
       │
       ├── anthropic_messages → transport.build_kwargs(model=agent.model, ...)
       ├── bedrock_converse   → transport.build_kwargs(model=agent.model, ...)
       ├── codex_responses     → transport.build_kwargs(model=agent.model, ...)
       └── chat_completions    → transport.build_kwargs(model=agent.model, ...)
                                      │
                                      ▼
       interruptible_api_call(agent, api_kwargs)  ← ★ 拦截点
              │
              ├── anthropic_messages → client.messages.create()
              ├── bedrock_converse   → boto3 converse()
              ├── chat_completions   → client.chat.completions.create()  ← ★ 在这里替换为 MoA loop
              └── ★ moa → MoARunner.run(api_kwargs)
```

### 3.3 风险评估

| 风险 | 级别 | 缓解 |
|------|------|------|
| 在 `interruptible_api_call` 层拦截意味着所有上层逻辑（`build_api_kwargs`、streaming dispatch、fallback）已执行，可能产生不必要的 kwargs 构建 | 中 | 在 `build_api_kwargs` 中也需检测 `moa` mode 并提前返回 MoA 专用 kwargs |
| 流式路径 `interruptible_streaming_api_call` 对 MoA 需要完全不同的实现（N 参考模型并行 → 聚合器流式） | 高 | 需要独立的 `_call_moa_stream()` 实现，与 `_call_chat_completions()` 平行 |
| 非流式路径较简单：等待所有参考模型完成 → 聚合器一次调用 → 返回 | 低 | `_call_moa_sync()` 复用现有 `call_llm()` infrastructure |

**结论**：拦截点 B 是最终执行点，但**不宜单独使用**——最好与 A 配合（api_mode 决策在 A，执行在 B）。

## 4. 推荐方案：A + B 双点拦截

```
init_agent()                     ← 识别 moa/ → api_mode="moa" + 加载 preset
       │
       ▼
build_api_kwargs()               ← switch api_mode → moa 专用 kwargs
       │
       ▼
interruptible_api_call()         ← switch api_mode → MoARunner.run()
interruptible_streaming_api_call()
```

**实施顺序**：
1. `plugins/model-providers/moa/` ProviderProfile 注册 + preset YAML schema → `intellect_cli/moa_config.py`
2. `agent/agent_init.py`：在 api_mode 决策链中插入 `moa/` 前缀检测
3. `agent/transports/moa.py`：MoA transport（`build_kwargs` + 执行入口）
4. `agent/moa_loop.py`：参考模型并行 + 聚合器循环（复用 `auxiliary_client.call_llm()` 解除 OpenRouter 绑定）
5. `agent/chat_completion_helpers.py`：`api_mode == "moa"` 分支
6. CommandDef `/moa` + legacy 工具文档指向

## 5. 流式 / fallback / smart_model_routing 影响矩阵

### 5.1 流式

```
首 token 延迟 = max(ref_1_latency, ref_2_latency, ..., ref_N_latency) + aggregator_first_token_latency

典型估算（4 参考模型，各 ~3s，聚合器首 token ~1s）：
  首 token ≈ max(3, 3, 3, 3) + 1 = 4s
  总延迟 ≈ 4 + aggregator_streaming_time ≈ 6-8s
```

**建议**：首版限制 N ≤ 5，在 `/moa` help 和 preset 选择时展示估算延迟。

### 5.2 fallback_model

```
MoA 模型不参与 fallback 链：
  - 若 agent.model = "moa/default"，fallback 被禁用（成本 N+1 不能作为 fallback）
  - 若 fallback_model 列表中包含 moa/ 前缀，init_agent() 中跳过该项并 warn
```

### 5.3 smart_model_routing

```
代码库中不存在 smart_model_routing 实现 → 无需处理
若未来引入，MoA 模型需显式豁免路由（标记为 VIRTUAL_PROVIDER 类型）
```

### 5.4 带 tool call 的回合

```
检测：build_api_kwargs 中若 tools_for_api 非空且 api_mode == "moa"：
  → raise ValueError("MoA virtual provider does not support tool-calling rounds.
    Use /model to switch to a standard provider for tool-heavy tasks, or use
    /moa for analysis/synthesis rounds.")
```

## 6. 与现有 `mixture_of_agents` 工具的共存

| 项目 | 处理方式 |
|------|----------|
| 工具注册 | `mixture_of_agents` 保留在 registry，标记 `deprecated: true` |
| `/moa` 命令 | 新 CommandDef，显示 preset 列表 + 使用说明 |
| 旧工具调用 | 打印 deprecation warning："mixture_of_agents is deprecated. Use /model moa/default or /moa list" |
| 配置迁移 | doctor 检查旧 `tools.mixture_of_agents` 配置，提示迁移到 `moa.presets` |

## 7. 出口检查

- [x] 拦截点 A 分析（`agent_init.py` client 工厂）
- [x] 拦截点 B 分析（`chat_completion_helpers.py` 请求入口）
- [x] 推荐方案：A + B 双点拦截
- [x] 流式延迟估算：`max(ref_latency) + aggregator_latency`，首版 N ≤ 5
- [x] fallback_model 豁免策略
- [x] smart_model_routing 无影响（不存在）
- [x] 带 tool call 回合报错方案
- [x] 工具共存策略：legacy deprecation

**待评审签字后进入 HP-302 实施。**
