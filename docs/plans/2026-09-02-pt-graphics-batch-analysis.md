# PT 图形批次 · 任务介绍与实施分析

> **日期**：2026-09-02
> **状态**：待排期（M5 已关账后的独立批次）
> **来源**：`docs/plans/2026-08-31-hermes-new-features-deep-dive.md` §12（Pets 全参照）；
> M5 PT V1 交付（commit `da60384`，V1 范围裁定记录见 phase-execution-details）。
> **读法**：本文是"PT TUI/图形协议批次"的启动前分析——批次内容、技术要点、已就绪
> 地基、风险与建议切法。实施前按惯例进规划模式细化为逐提交工作项。

---

## 一、为什么单独成批（延后原因，三点仍然成立）

1. **前端构建链**：核心交付物 `petSprite.tsx` 是 ui-tui（Ink/React/TypeScript）
   组件，需要 npm 构建链、`pet.cells` JSON-RPC 通道扩展与 TUI 渲染循环改造——
   与 M5 其余纯 Python 包不同技术面。
2. **图像解码依赖**：webp spritesheet 解码需要引入图像解码依赖（Pillow 或
   rust 侧辅助），触碰依赖 pinning 政策（上限锁定 + `uv lock` 哈希），需单独评审。
3. **终端图形协议深水区**：kitty/iTerm2/sixel 各有探测、编码与回退逻辑，且都
   要求**纯 env 探测、绝不发 DA1 能力查询**（防非交互管道挂死）。

---

## 二、批次内容（四块工作）

### ① Sprite 解码管线（Python 侧，扩展 `agent/pet/render.py`）

- 解码 `<INTELLECT_HOME>/pets/<slug>/spritesheet.webp`——V1 中
  `render_png_placeholder()` 是 `NotImplementedError` 占位；
- **atlas taxonomy 自动推断**（移植要点，不可硬编码）：petdex 有 8 行 legacy
  与 9 行 Codex 两种 sprite 表结构，按实际 sheet 行数自动选择；
- 按 state × frame 切帧：192×208，每 state 6 帧，LOOP_MS=1100；
- 帧数据交付给协议层/TUI 的中间形态：cells（双色半块网格）或 PNG（kitty）。

### ② 终端图形协议层（探测 + 编码 + 降级）

| 协议 | 机制 | 优先级 |
|---|---|---|
| **kitty** | Unicode placeholder 技巧：图像挂到 224 个专用码点的变音符（diacritics）表，作为普通文本单元格输出——不触发重绘冲突 | 首选（TUI 走这条） |
| **iTerm2** | inline image（OSC 1337）转义序列 | 次选 |
| **sixel** | 老式图形协议 | 再次 |
| **unicode 半块** | ✅ V1 已交付（真彩半块网格），作为以上全部不可用时的降级，有独立清晰度下限 | 已有 |

探测规则：纯环境变量推断（`TERM`、`KITTY_WINDOW_ID`、`TERM_PROGRAM` 等），
**绝不发 DA1 查询**——那会在非交互管道里挂死进程。`display.pet.render_mode`
（auto/kitty/iterm2/sixel/unicode）为强制覆盖口。

### ③ TUI 组件（ui-tui 侧，TypeScript）

- `petSprite.tsx`：cells → Ink `<Text>` 双色半块元素；关键约束：**kitty 帧
  不触发 Ink repaint**（图形与文本渲染互斥，同类项目常见闪烁/错位问题）；
- `pet.cells` RPC：新增 JSON-RPC 方法进入 TUI 的 long-handler 池，Python 侧
  每帧推送 cells；
- 挂接点：ui-tui 构建链（`npm run build`），RPC 目录见 `tui_gateway/server.py`
  方法表。

### ④ 可选尾巴（继续后置）

本地孵化工具（`generate/`：base drafts → hatch per-state → atlas 合成校验，
`_MIN_FILLED_STATES=6`）——深潜标注可后置，本批建议继续后置。

---

## 三、已就绪的地基（PT V1 交付，commit `da60384`）

本批**不是从零开始**——以下 Python 基础设施已在位：

- `agent/pet/` 包：`store`（`<home>/pets/<slug>/` 安装、slug 防穿越、资产路径
  逃逸拒绝）、`manifest`（petdex.dev 拉取、host-pin 防 SSRF、300s TTL、
  网络失败降级本地清单）、`state`（活动信号 → 状态优先级）、`constants`
  （192×208 / 6 帧 / LOOP_MS=1100 / host-pin allowlist）；
- `render.py` 的 unicode 半块 truecolor 渲染器（最终降级路径已在位）+
  `render_png_placeholder()` 占位（本批的实现点）；
- `intellect pets` CLI（list/install/select/doctor）与 `display.pet.*` 配置面
  ——`render_mode: auto` 就是本批协议选择开关的挂接点；
- `tests/agent/test_pet_package.py` 12 例（store/manifest/渲染确定性/状态机）。

---

## 四、技术风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| webp 解码依赖引入触碰 pinning 政策 | 中 | 评审时定方案（Pillow 上限锁定 vs rust `image` crate 侧解码经 pyfunction 暴露）；后者零 Python 依赖但需 maturin 重建 |
| kitty placeholder 与 Ink 渲染协调（闪烁/错位） | 高 | 沿深潜约束：kitty 帧不触发 Ink repaint；TUI 侧帧推送走 `pet.cells` 长句柄池，渲染节流独立于 Ink 循环 |
| 协议探测假阳性（env 有 TERM 但实际不支持） | 中 | 纯 env 探测 + 降级链 auto→kitty→iTerm2→sixel→unicode；unicode 永远兜底 |
| unicode 降级清晰度不足 | 低 | 独立清晰度下限：低于阈值直接禁用而非渲染模糊块 |
| petdex 资产格式变化 | 低 | atlas taxonomy 运行时推断（行数自适应），不硬编码 |

---

## 五、建议切法（两段式提交组）

- **提交组 1（纯 Python，可完整测试）**：①解码管线 + ④unicode 强化（清晰度
  下限 + `render_mode` 覆盖口）；webp 方案在此时定（依赖评审）。
- **提交组 2（协议 + TUI）**：②kitty/iTerm2/sixel 编码与探测 + ③petSprite.tsx
  + `pet.cells` RPC；验证用 env 门控 E2E（真终端截图比对，沿门-4 惯例），
  unicode 路径保持纯单测。
- **门验收（建议）**：kitty/iTerm2/sixel 各真终端视觉验收一例；unicode 降级
  纯单测；`display.pet` 开关 off 回归零变化；webp 解码依赖过 pinning 评审。

---

## 六、规模

M~L（约等于 M4 的一半到一个）：①解码 M、②协议 M、③TUI S~M、④后置。
前置依赖：无硬依赖（PT 无依赖轨），但 ui-tui 组件需要 npm 工具链可用。
