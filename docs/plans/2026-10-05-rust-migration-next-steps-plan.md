# Python→Rust 迁移：剩余改进环节方案（含评审修订）

> **日期**：2026-10-05
> **状态**：📋 提案 + ✅ 快赢批次已实施（同日，见 §8 实施记录）；批次三"评估后决定"各项待维护者拍板后方可立项
> **输入**：对 rust-core 现状 / Python 热路径 / 既有迁移规划的全库扫描（3 路探索 + 定向抽查验证）、
> `docs/architecture/rust-python-interaction.md` §6–§7、`docs/plans/bench-baseline.json`（G-14/G-21 裁决）。
> **定位**：本文是 §6.6"迁移优先级（2026-09-14 复核）"的**提议性更新**——落地任一 P2+ 批次项时，
> 须在同一 PR 内把结论回写进架构文档，避免两份优先级清单漂移。

---

## 1. 背景与现状

"搬模块"阶段已基本完成：rust-core 共 ~8.3k 行 / 21 模块（存储、沙箱、加密、流累计、错误分类、
预算/退避、merge queue 等），自 v0.6.2 起为**强制运行时依赖**（无 Python fallback，
`intellect_rust.py::ensure_rust_available()` 硬失败）。架构文档自测迁移率 ~21.8%。

剩余空间分三类：

1. **仍是纯 Python 的高频算法环节**（§3，P 系列）；
2. **迁移工程本身的欠账**（§4，E 系列）；
3. **已有"不迁移"裁决、不应重开的边界**（§5）——包括网关平台适配器的专项裁决（§6）。

---

## 2. 统一迁移门槛（所有 P 系列项强制适用）

评审修订后升级为硬性规则，源自 G-14/G-21 已验证的项目内流程：

1. **先 bench 后动手**：`scripts/bench/` 建基线，裁决（做/不做）写入 `docs/plans/bench-baseline.json`。
   预期收益低于 G-21 量级（µs 级/占比 <0.1%）的项直接否决，不立项。
2. **黄金语料差分测试**：迁移前用现有 Python 实现对真实数据（流式 delta 序列、真实 patch 语料、
   真实会话历史）生成 golden corpus，Rust 实现须逐字节对齐。**v0.6.2 起无 fallback、无灰度，
   每项迁移都是硬切换**——语料门不是可选项。
3. **GIL 释放标准**：模块注册为 `gil_used = true`；新迁移项的 CPU 密集/阻塞段必须
   `allow_threads` 释放 GIL（先例：`backend.rs` 的 GIL-free row materialization），
   否则会阻塞网关事件循环，抵消收益。
4. **文档同 PR 更新**：涉及 §6.5/§6.6 边界或优先级变化的，同一 PR 修订架构文档。

---

## 3. 代码层改进项（P 系列，评审修订版）

### P1. 流式标签过滤链 —— **已改写：Python 去重为主，Rust 化大概率 bench 否决**

**现状**（评审核正后的真实架构）：

- 上游**已经集中化**：`run_agent.py:3731` `_fire_stream_delta` 先过 `StreamingThinkScrubber`
  再过 `StreamingContextScrubber`，每个 `stream_delta_callback` 看到的都是已过滤文本
  （`agent/think_scrubber.py` docstring 明示此设计意图）。
- 存在四份实现但**不是四层意外叠加**：每 delta 实际穿 3 层（2 上游 + 1 下游防御副本），
  且过滤词汇表不同（think 家族 vs `<memory-context>`，`agent/memory_manager.py:88`）——
  "统一为一个状态机"实为"一个参数化引擎 + ≥3 套配置"。
- 性能真相：两份上游 scrubber 缓冲区只保留部分标签尾巴（µs/delta 量级，与 G-21 实测的
  16µs/delta 同量级）；唯一真实热点是 `cli.py:4244-4249` 在 reasoning 块**内部**不裁剪已消费
  前缀，O(n²) 退化（仅块内，受块长约束）。
- 下游副本非简单冗余：CLI 副本承担 `show_reasoning` 模式路由（`cli.py:4144-4167`）；
  网关副本防御"最终 strip 前的中间 edit"（`gateway/stream_consumer.py:296-297`）。

**方案**：

- (a) 快赢：修 `cli.py` 块内前缀裁剪（~5 行 Python，消除唯一二次方）。
- (b) 以**可维护性**（四份微妙状态机、有 reasoning 泄漏史）立项评估下游两份副本能否退役；
  需先解开 `show_reasoning` 模式耦合并做 E2E 验证。Python 层面去重，不迁 Rust。
- (c) Rust 化仅在上游单遍实测超 G-21 阈值时才立项——预期不会。

### P2. 模糊补丁匹配 + V4A patch 解析 —— **有条件保留：需先修订 §6.5 边界**

**现状**：`tools/fuzzy_match.py:343-840` 九策略每次 `patch` 全文件跑，归一化副本按策略重建，
`difflib.SequenceMatcher`（`:599/:632/:810`）近失锚点二次方退化；`tools/patch_parser.py:69-224`
逐行正则。**收益排序应是先正确性后速度**（编辑主路径）。

**前置条件（评审新增）**：架构文档 §6.5 明文"工具实现（`tools/*`）迁移无意义"。本项目必须
框定为"**算法库抽取到 rust-core，`tools/` 内编排不动**"（先例：`tool_utils.rs` ←
`skill_manager_tool.py`、`sandbox.rs` ← `approval.py`），且同一 PR 修订 §6.5/§6.6 措辞。

**风险**：`SequenceMatcher` 的 autojunk 启发式行为难以逐位复现——匹配锚点变化即 patch 结果
变化，必须大语料 parity 后才可切换。工作量 L，风险 M-H。

### P3. 消息列表 token 估算 —— **保留，但先做纯 Python 修复再 bench**

**现状**：调用点热度已核实——`agent/conversation_loop.py:875` 每次 API 调用 + 
`agent/context_compressor.py` 8 处 + `agent/moa_loop.py` 2 处 + webui 2 处，每次 O(全历史)；
`agent/model_metadata.py:1698` `_estimate_message_chars` 对大字段 `len(str(v))` 完整物化。
另有第二套粗估 `agent/chat_completion_helpers.py:57-107` 同样每次调用。

**修订后的顺序**：先纯 Python 修复 `str()` 物化（非 str 标量不物化、list 分段求和）→ bench →
剩余成本仍显著才迁 Rust（`estimate_messages_tokens_rough_rs`，注意 PyO3 借用遍历 vs
`json.dumps`+Rust 解析两条路线需 bench 对比，PyO3 逐对象转换对小消息可能更慢）。

### P4. 敏感信息脱敏 —— **降级：存在正则移植硬阻塞**

**现状**：`agent/redact.py:338` 13+ 正则，83 个调用点（每 tool result / 每日志行 / 每流式 flush）。

**硬阻塞（评审核实）**：`:204` 使用 lookbehind + lookahead，`:124` 使用反向引用 `\2`——
Rust `regex` crate **均不支持**。仅两条路：引入 `fancy-regex`（新依赖、更慢，与供应链收缩
政策相悖）或逐条重写模式语义（每条都是行为变更风险）。且"13 遍线性放大"说法过头：每遍是
C 速度 `re`，短行实测 1.8µs；只有大 tool 输出（100KB+）单遍扫描器才可能有收益。

**裁决**：降至"评估后决定"。前置：fancy-regex 依赖审批或重写方案通过语料 parity。

### P5. 会话历史水合 —— **保留，优先级下调**

**现状**：`intellect_state.py:2511` 每行 3 次 `json.loads` + 逐条 sanitize 正则。调用方为
**每次 session resume / API 历史拉取**（cli 3 处、tui_gateway 3 处、api_server 2 处、acp 1 处），
网关常驻会话不逐消息调用——频率低于原评估。写侧 `append_message_batch_rs`（HP-402）已有，
读侧是天然对称缺口，但排 P2 之后。若做：批处理 + GIL 释放（门槛 §2.3）。

### P6. 请求载荷构建 —— **维持：最后评估**

`agent/anthropic_adapter.py:2108` 每请求全历史重建载荷；`agent/agent_runtime_helpers.py:1845`
`sanitize_api_messages` 每次 LLM 调用前无条件跑 O(历史) id 配对嵌套循环。拆两阶段：先只迁
id 配对（语义独立易测）；载荷重建触碰 prompt-caching 不变量，须 gate-1 缓存字节回归测试护航，
仅在 bench 证明瓶颈后做。工作量 L，风险 H。

### P7. SSE 帧级 repr 浪费 —— **维持，已证实，快赢**

`agent/chat_completion_helpers.py:1978` 对每个 chunk 无条件 `len(repr(chunk))`（无 debug 门控，
`:2223` 还有第二处）。改为累计 delta 长度或下沉到 Rust 累计器。影响每响应毫秒级，纯浪费。

---

## 4. 迁移工程欠账（E 系列）

| 项 | 内容 | 备注 |
|---|---|---|
| E1 | **PyPI 轮子缺 Rust 扩展**：`upload_to_pypi.yml` 只发纯 Python wheel，用户装完必撞 `ensure_rust_available()` 硬失败。方案 (a) 复用 gitee-release 现成 4 平台轮子直接传 PyPI（零代码改动，首选）；方案 (b) abi3-py312 单轮子——`requires-python = ">=3.12"`（`pyproject.toml:20`）使其成为干净选项，但有性能开销与测试负担。 | 分发链最大缺口 |
| E2 | **死导出清理**：`PlatformRetryScheduler`、`TokenBucket`、`rust_canonical_tool_args`、`rust_truncate_content`、`rust_strip_yaml_frontmatter`、`rust_paths_overlap`、`contains_cjk_rs`/`count_cjk_rs`、裸 `RustFailoverReason`/`RustClassifiedError`、`rust_evaluate_reset_policy` 零消费者。逐一"接线或删除"，删除前同步去掉 parity 测试中的 availability 断言。 | 强制依赖里的死代码 = 体积 + 供应链审计面 |
| E3 | **CJK 搜索收尾**：`backend.rs:993-997` 注明 CJK 检测与 LIKE 回退留在 Python，而 `contains_cjk_rs` 已导出却零消费。把分支判定下沉进 backend 内部，正式关闭 Stage 1 尾巴。 | |
| E4 | **测试缺口**：`prompt_caching.rs`（131 行）0 个 Rust 测试，守护的恰是最贵不变量；`error_classifier.rs`（876 行）行为验证全靠 Python 侧。回灌 Rust 单测；`tests.yml` 加 Cargo 缓存（按 `Cargo.lock` key，省 15-25 min/平台）。 | |
| E5 | **M5/M7 遗留处置**：context_compressor（M5）大概率复制 G-14 结局——用现成 `scripts/bench/bench_compression.py` 跑一次，裁决写入 bench-baseline.json，正式关闭而非挂起。 | |
| E6 | **`SESSIONDB_USE_RUST_RW` 双存储路径归宿**（评审新增）：`intellect_state.py:280` 常量=1，但 `agent/storage/sqlite_backend.py:358-465` 仍完整维护 Python sqlite3 读写并行实现。运行时既对缺扩展硬失败，此 flag 只剩调试价值。与 E2 同类：文档化保留或删除，二选一。 | 原方案完全遗漏 |

---

## 5. 不可重开的边界（除非工作负载发生实质变化）

- **G-14** `list_sessions_rich`（92% 成本在 SQLite C 层）、**G-21** `stream_consumer` + `delivery.py`
  （0.036% CPU、瓶颈是平台 edit API）——bench 正式否决，裁决在 `docs/plans/bench-baseline.json`。
- **架构文档 §6.5"始终保留在 Python"**：`tools/*` 工具实现、CLI/TUI、网关平台适配器（专项见 §6）、
  插件系统、ACP Server。
- **§6.6"不建议迁移"**：`conversation_loop.py`、`auxiliary_client.py`、`display.py`、`tool_executor.py`。
- **沙箱双层分治是设计**：Python AST 层与 Rust 正则层互不吞并（`rust-core/README.md`）。

---

## 6. 专项裁决：网关平台适配器迁 Rust —— 无收益，不做

**结论**：CPU 侧无可回收份额，成本侧是 5.5 万行重写，且适配器即插件边界、与 agent 同进程。
维持 §6.5 既有裁决。

**证据**：

| 维度 | 证据 |
|---|---|
| 时间去向 | G-21 实测：网关最热 Python 环节 24.96 MB/s、16µs/delta、负载下 CPU 占比 0.036%；**平台 edit API（秒级、限速主导）才是瓶颈** |
| 每消息 CPU | telegram 出站最重工作（markdown 表格重排 `plugins/platforms/telegram/adapter.py:219-281`、格式回退链 `:2374-2415`）为每消息一次、KB 级文本，µs–ms，占比 <1% |
| 重写规模 | `plugins/platforms/` 27 个适配器共 55,261 行（discord 6.2k / telegram 6k / feishu 5.1k / yuanbao 4.9k…）= 整个 rust-core 的 6.6 倍 |
| 插件边界 | 适配器已是插件（`gateway/platforms/telegram.py` 仅 `from plugins.platforms.telegram import adapter as _impl` 桥接）；AGENTS.md 规定插件面 = Python 动态加载，`ADDING_A_PLATFORM.md` 定义 Python 编写契约 |
| 进程模型 | agent 与网关同进程（`gateway/agent_runner.py:481/3129` 直接调 `run_conversation`）；适配器迁 Rust 后每条消息跨 FFI 折返，桥接开销大概率超过省下的 CPU |
| 并发优势落空 | 永久单用户（WONTFIX）；连接数 ≈ 每平台 1–2 条；asyncio 无压力，CPU 密集环节已在 Rust 且释放 GIL |
| 唯一半成立候选 | `gateway/platforms/yuanbao_proto.py`（1,209 行纯 Python protobuf 编解码）确实慢 100×，但每条聊天消息才跑一次、消息以分钟计；真要优化接 C 加速 `protobuf` 库即可，轮不到 Rust |

**翻转条件（三者须同时成立）**：(a) Stage 6"Rust 主进程 intellectd + 嵌入 Python"立项；
(b) 放弃单用户边界（已被 WONTFIX 封死）；(c) 入站流量达千条 webhook/s 级。

**若未来有人在适配器内提出具体热点**：按 §2.1 纪律 bench 后裁决，预期结局与 G-21 相同。
现有正确切法保持：纯函数进 Rust（`gateway.rs` 退避/批量过期/session key），I/O 与插件面留 Python。

---

## 7. 修订后推进批次

| 批次 | 内容 | 状态 |
|---|---|---|
| 快赢（~1 周） | P7（repr 浪费）、P1(a)（cli.py O(n²) 修复）、E2（死导出）、E6（双存储路径裁决）、E3（CJK 收尾）、E4（测试回灌 + Cargo 缓存） | ✅ 已实施 2026-10-05（P1(a) 复核后撤销、E3 改为接线、E4 缓存项发现已存在，详见 §9） |
| 第一批迁移 | P3（先 Python 修复再 bench 决定）、P5（历史水合） | ⏳ 待启动 |
| 第二批迁移 | P2（前置：§6.5/§6.6 文档修订同 PR）、P1(b)（下游副本退役评估，Python 去重） | ⏳ 待启动 |
| 评估后决定（待拍板） | P4（前置：fancy-regex 审批或重写方案）、P6、E1（PyPI 分发策略 a/b）、E5（M5 bench 关闭） | ⏳ 待拍板 |

---

## 8. 证据索引

- Rust 层现状与桥接：`rust-core/src/`（21 模块 ~8.3k 行）、`intellect_rust.py`（统一适配门）、
  `intellect_community_core/__init__.py`（stub + 版本握手）
- 既有裁决：`docs/plans/bench-baseline.json`（G-14/G-21 verdict + numbers）、
  `docs/architecture/rust-python-interaction.md` §6.5/§6.6/§7
- P1 证据链：`agent/think_scrubber.py`（docstring 设计意图 + 历史 bug 记录）、
  `run_agent.py:3630-3668/3710-3731`（上游链路与 flush 顺序）、`agent/memory_manager.py:88-145`、
  `cli.py:4137-4249`、`gateway/stream_consumer.py:296-355`
- P4 阻塞证据：`agent/redact.py:124`（反向引用 `\2`）、`:204`（lookaround）
- 分发链：`upload_to_pypi.yml`、`gitee-release.yml`、`scripts/install.sh:1361-1400`、
  `docs/packaging/design.md`（Homebrew/Windows 安装器缺 Rust 构建步骤等已知缺口）

---

## 9. 快赢批次实施记录（2026-10-05，实施时二次评审后）

细化阶段的代码级复核**修正了原方案三处**，随后实施：

| 项 | 实施结果 | 说明 |
|---|---|---|
| P1(a) | **撤销（无缺陷）** | 细读 `cli.py:4268-4274` 发现块内缓冲每 delta 无条件裁剪到 max_tag_len（24 字符），`show_reasoning` 只控制显示路由不控制裁剪；非块路径同样只保留部分标签尾（`:4241`）。**O(n²) 不存在**，原评审对 :4244-4249 的读法漏看了 :4269 的裁剪。零改动结案 |
| P7 | ✅ 已实施 | `chat_completion_helpers.py` 两处 `len(repr(chunk/event))` 移除；改为 5 个提取点计长（reasoning/content/tool-args/text/thinking），`stream_diag.py` 字段语义注释更新。消费方仅为流断线取证日志（`stream_diag.py:168,190`），语义变化安全 |
| E2 | ✅ 已实施 | 核实后删除 12 个零消费导出：crypto 3（secure_random_bytes/secure_token_urlsafe/decode_jwt_claims_rs）+ tool_utils 4（strip_yaml_frontmatter/truncate_content/paths_overlap/canonical_tool_args，含 7 个测试）+ gateway 5（TokenBucket、PlatformRetryScheduler、backoff_delay_rs、backoff_delay_batch_rs、evaluate_reset_policy 导出）。`evaluate_reset_policy_rs` 保留为内部函数（batch 版调用）；`intellect_rust.py` 同步删 7 个绑定；parity 断言同步收缩。`FailoverReason`/`ClassifiedError` 保留（是 `classify_api_error_rs` 的返回类型，非死码） |
| E3 | ✅ 改为"接线"实施 | 完整下沉需移植 trigram/LIKE 慢路径（非快赢）。实际实施：`rust_contains_cjk`/`rust_count_cjk` 绑定加入 `intellect_rust.py`，`SessionDB._contains_cjk`/`_count_cjk` 委托 Rust（Python 循环保留为无扩展 fallback），两侧码位表已核对一致（7 区段）；`backend.rs` 注释更新职责划分；parity 新增 `test_cjk_helpers_parity` |
| E6 | ✅ 裁决：文档化保留 | 证据：Python 连接在 flag=1 时仍是 G-14 读池（`sqlite_backend.py:417-450`）与 backward-compat `execute()`（`:400`）的载体，WAL 读写一致性耦合已有文档（`docs/plans/2026-08-30-agent-core-hermes-gap-analysis.md:63,134`，结论"非低改动"）。删除属重构非清理，不在快赢范围。`intellect_state.py` flag 注释已落实裁决与依据 |
| E4 | ✅ 部分已实施 | `prompt_caching.rs` 0→10 个测试（断点布局/ttl/字符串包装/list 尾部/tool 双模式/深拷贝不突变）；`error_classifier.rs` 5→13 个测试（401→auth、RateLimitError 强制 429、413/503/500、thinking_signature、grok 订阅、long_context_tier）。**tests.yml 的 Cargo 缓存已存在**（`tests.yml:47-55`，含 `rust-core/target`，Cargo.lock 键控）——原方案该项是重复提议，无改动 |

**技术发现（过程中）**：

1. Python 触碰型 Rust 测试在 pyo3 0.29 无 `auto-initialize` 特性时需显式 `Python::initialize()`（幂等）——已在两个测试模块落地，配合既有 `--test-threads=1` 约定。
2. **macOS arm64 签名陷阱**：maturin wheel 往返后链接器 adhoc 签名可能页级失效（内核以 "Code Signature Invalid" SIGKILL 拒绝 `.so` 导入，静态 `codesign -v` 却通过）。修复：`codesign -f -s -` 重签；已把重签步骤固化进 `scripts/sync_rust_extension.sh`（Darwin 分支）。

**验证记录**：`cargo test --no-default-features -- --test-threads=1` **202/202 绿**（198−14 删+18 增，CI 门 ≥84 满足）；`scripts/run_tests.sh` 定向 223 个 Python 测试全绿（parity+handshake+merge_queue 67、intellect_state+流式诊断 131、gateway session 25）；parity 文件收集数 49（CI 门 ≥45 满足）；扩展经 `maturin develop` 重建 + 同步脚本落地。
