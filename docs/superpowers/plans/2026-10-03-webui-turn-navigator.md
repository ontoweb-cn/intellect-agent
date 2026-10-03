# WebUI 会话提问导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 在聊天区左侧加一条本会话提问刻度轨。每条用户提问一个刻度，点一下滚到对应气泡，并标出当前视口所在的那一轮。

**Architecture:** 纯函数算出目录（JS 与 Python 镜像，pytest 锁行为）。DOM 轨挂在 `.messages-shell` 上，作为 `#messages` 的兄弟，不进滚动层。点击走现有 `jumpToTurnQuestion(rawIdx)`。不新开 API，不拉全量历史，不引入 React。

**Tech Stack:** `webui/static/turn-outline.js`、`webui/static/ui.js`、`webui/static/style.css`、`webui/static/index.html`、`webui/static/i18n.js`、`webui/api/turn_outline.py`、`tests/webui/test_turn_outline.py`。

**Branch:** `feat/webui-turn-navigator`

**Status:** 方案已评审，未开工。

**参照:** DeepMentor `web/lib/chat-outline.ts` + `web/components/chat/home/TurnNavigator.tsx`。只借行为，不搬组件。

---

## 评审结论（实现时必须遵守）

这些是评审后改过的约束。和 DeepMentor 原文不一致的地方，以这里为准。

1. **轨是 `.messages-shell` 的子节点，不是 `#messages` 的子节点。** `#messages` 是 `overflow-y: auto` 的滚动层。放进去会跟着内容滚走，也会被当成消息高度。Start / End 按钮已经挂在 shell 上，轨用同一层，`position: absolute; left`。
2. **刻度不持久化 key。** `msg-user-${rawIdx}` 的 `rawIdx` 是当前 `S.messages` 下标。向上翻页会整表替换，下标会变（`sessions.js` 的 `_loadOlderMessages`）。每次目录签名变化时按当前数组重算，点击把**当前** `rawIdx` 交给 `jumpToTurnQuestion`。不要用 `_oldestIdx + rawIdx` 做 DOM id。
3. **目录只覆盖已经在内存里的消息。** 首屏 `msg_limit` 默认 30，更早的在 `_messagesTruncated` 后面。禁止为了填满轨去调 `_ensureAllMessagesLoaded()`：那条路径和进行中的翻页有代际竞争（`#1937`），也会把超长会话一次性灌进 DOM。截断时轨顶只放一个「更早」按钮，调用已有的 `_loadOlderMessages()`。
4. **跳转必须先松开滚动钉，并且只滚 `#messages`。** 现有 `jumpToTurnQuestion` 用 `scrollIntoView({behavior:'smooth'})`，没有设 `_programmaticScroll`。平滑滚动的中间事件仍可能被 scroll 处理函数当成「接近底部」而重新 `_scrollPinned=true`，下一次 token 的 `scrollIfPinned()` 会把视口拽回去。跳转开始时设 `_scrollPinned=false`、`_messageUserUnpinned=true`、`_programmaticScroll=true`；用 `#messages.scrollTop` 滚动，不要 `scrollIntoView`（避免带动其它可滚祖先）。结束后把 `_programmaticScroll` 清掉，并把 `_lastScrollTop` 写成当时的 `scrollTop`。
5. **虚拟窗口下当前刻度不能只量 DOM。** `transcript_virtual_window` 打开且可见行数超过 80 时，用户气泡可能在 top pad 里，DOM 里没有。先在已渲染的 `msg-user-*` 上套「相对容器顶 ≤ 140px 的最后一条」；一条都没有时，从 `_messageVirtState.start` 沿 `visWithIdx` 向前找到上一条用户消息。
6. **流式输出不要每 token 重建轨。** `renderMessages()` 在流式期间很热。目录签名 = 用户消息条数 + 最后一条用户 `rawIdx` + 各条 title 的长度序列。签名不变就只更新 `aria-current`，不重画按钮。
7. **窄屏藏轨，快捷键还在。** `#msgInner` 居中且有 `max-width`，宽屏左侧才有 gutter。gutter &lt; 52px，或视口宽度 &lt; 600px（与 `.msg-question-jump-btn` 的断点一致）时不画轨。提问数 ≥ 3 时 `Alt+↑` / `Alt+↓` 仍然有效。提问数 &lt; 3 时轨和快捷键一起不启用。
8. **等长刻度，不做 `sqrt(titleLen/longest)`。** 权重会在翻页后整体重排，刻度长短乱跳。悬停卡片仍显示问题摘要和助手回复第一段。
9. **不做设置开关，不改 12 个语种。** 自动隐藏已经盖住「短会话 / 窄屏」。文案只加 `en` 和 `zh-Hans`，`t()` 会回落到英文。

---

## 行为规格

### 目录

从 `S.messages` 按数组顺序扫描，跳过 `role === 'tool'`、压缩占位、以及 `msgContent` 去工作区前缀后为空的用户消息。

每条用户消息一条：

| 字段 | 规则 |
|---|---|
| `rawIdx` | 在 `S.messages` 中的下标 |
| `title` | `msgContent` → `_stripWorkspaceDisplayPrefix` → 折叠空白 → 去掉少量 markdown 标记（`#`、`*`、反引号、链接壳）→ 截到 96 字符 |
| `reply` | 下一条用户消息之前，第一条 `msgContent` 非空的 assistant。同样清洗，截到 160。没有则为空字符串 |

不读分支、不读 `selectedBranches`。本项目会话是线性的。

少于 3 条时不挂交互（见评审第 7 条）。

### 当前刻度

输入是一组 `{rawIdx, relTop}`，`relTop` = 气泡顶 − `#messages` 顶。

- 取 `relTop <= 140` 的最后一条。
- 若全部 `relTop > 140`，取 `relTop` 最小的那条（线下面最近的一条）。
- 虚拟窗口补一条合成候选：视口起点之前的上一条用户消息，`relTop = -1`，这样「人正在读一条很长的回答、问题气泡已被垫片替掉」时仍能点亮。

滚动监听挂在 `#messages` 上，用已有的 rAF 合并，不另开一条未合并的 scroll 监听。`_programmaticScroll` 为真时也要更新当前刻度（跳转过程中刻度跟着走），但不要在这条路径里改 `_scrollPinned`。

### 跳转

`jumpToTurnQuestion(rawIdx)` 改完后同时服务助手气泡上的「回到问题」和这条轨。

1. 松开钉，置 `_programmaticScroll`。
2. 目标已在 DOM：把 `#messages.scrollTop` 调到让该行出现在距容器顶约 56px 处（纯函数算 delta，见 Task 1）。`prefers-reduced-motion: reduce` 时瞬时，否则平滑。平滑用一次性 `scrollend`（没有则 400ms 超时）结束编程滚动标志。
3. 目标不在 DOM：保持今天的两条路——虚拟窗口用 `_messageVirtPinIndex`，否则加大 `_messageRenderWindowSize`——然后再走第 2 步。
4. 高亮保持现有 `.msg-question-highlight`（1.8s）。

跳转期间 streaming 不得因为钉把视口拉回底部。

### 键盘

`document` 上 `Alt+ArrowUp` / `Alt+ArrowDown`，无 meta / ctrl / shift。

忽略：焦点在 `INPUT` / `TEXTAREA` / `SELECT` / contenteditable；命令下拉开着；提问数 &lt; 3；当前主视图不是聊天（`#mainChat` 不可见）。

阻止默认，相对当前刻度 ±1，端点不再动。不抢焦点。

### 更早

`_messagesTruncated` 为真且轨可见时，轨顶一个按钮，文案走 i18n，点击 `_loadOlderMessages()`。它不是一条假提问。加载完成后签名变化，目录自己变长。

### 无障碍

轨容器 `role="navigation"`，`aria-label` 用 i18n。每个刻度是 `button`，`aria-label` 为 title，当前项 `aria-current="true"`。悬停卡片 `pointer-events: none`，不进 tab 序。刻度本身可键盘聚焦，Enter / Space 走跳转。

文案用 `textContent` / `esc()`，禁止把 title 或 reply 拼进 `innerHTML`。

---

## 非目标

- 不移植 framer-motion、分支树、`[Quiz Performance]` 过滤、刻度权重。
- 不新增 HTTP 接口，不做「只拉问题标题」的服务端目录。
- 不改助手气泡上已有的「回到问题」按钮的出现条件，只修它调用的跳转函数。
- 不改会话侧边栏，不改 `agents.management_enabled`。
- 不在本计划里提交工作区里与本功能无关的未提交改动。

---

### Task 1: 纯函数目录与滚动位移

**Files:**
- Create: `webui/static/turn-outline.js`
- Create: `webui/api/turn_outline.py`
- Create: `tests/webui/test_turn_outline.py`
- Modify: `webui/static/index.html`（在 `ui.js` 之前加 script，与 `virtual_window.js` 同级）

算法与 `webui/static/virtual_window.js` ↔ `webui/api/transcript_virtual_window.py` 一样：JS 给页面用，Python 给 pytest 锁行为。两边函数名和数字常量保持对应，文件头互相指向。

- [ ] `build_turn_outline(messages)`  
      跳过 tool / 空用户消息。title 96、reply 160。回复取下一条用户消息之前第一条有正文的 assistant。工作区前缀 `[Workspace::v1: …]` 与旧 `[Workspace: …]` 都剥掉（规则与 `ui.js` `_stripWorkspaceDisplayPrefix` 一致，Python 侧抄同一正则，不要 import WebUI）。
- [ ] `pick_active_turn(candidates, threshold=140)`  
      `candidates` 为 `{raw_idx, rel_top}`，按文档顺序。规则见「当前刻度」。空列表返回 `None`。
- [ ] `scroll_top_for_turn(scroll_top, row_top_in_container, top_offset=56)`  
      返回新的 `scrollTop`：`scroll_top + row_top_in_container - top_offset`，下限 0。不读 DOM。
- [ ] JS 用 IIFE 挂到 `window.buildTurnOutline` / `window.pickActiveTurn` / `window.scrollTopForTurn`。无 DOM，无 `document`。
- [ ] pytest 覆盖：空列表；不足 3 条仍返回条目（隐藏是 UI 的事）；tool 与空用户被跳过；工作区前缀不进 title；reply 不跨过下一条用户消息；无正文的 tool-call assistant 不当作 reply；截断长度；`pick_active_turn` 的三条（线上最后一条、全部在线下、合成 `rel_top=-1` 与一条 `rel_top=100` 同时存在时选 100 那条）；`scroll_top_for_turn` 的下限 0。
- [ ] 跑 `pytest tests/webui/test_turn_outline.py`。

### Task 2: 修正 `jumpToTurnQuestion`

**Files:**
- Modify: `webui/static/ui.js`（`jumpToTurnQuestion`，约 577 行）
- Modify: `tests/webui/test_turn_outline.py` 或新建 `tests/webui/test_turn_navigator_contract.py`（读源码的契约，本仓库没有 jsdom）

- [ ] 进入函数即 `_scrollPinned=false`、`_messageUserUnpinned=true`、`_programmaticScroll=true`。失败路径也要清 `_programmaticScroll`。
- [ ] 用 `window.scrollTopForTurn` 写 `#messages.scrollTop`。删掉这条路径上的 `scrollIntoView`。
- [ ] `matchMedia('(prefers-reduced-motion: reduce)')` 为真时直接赋值。否则加一个一次性 class 用 CSS `scroll-behavior: smooth` 只作用在这次跳转上，跳完移除。不要改 `#messages` 的常态 `scroll-behavior`（虚拟窗口自己在改 `scrollTop`）。
- [ ] 结束时：`scrollend` 或 400ms，清 `_programmaticScroll`，`_lastScrollTop = container.scrollTop`。
- [ ] 虚拟窗口 / 渲染窗口的 pin 与扩窗逻辑保持现状，扩完后再滚。
- [ ] 契约测试断言 `ui.js` 里 `jumpToTurnQuestion` 函数体包含 `_scrollPinned=false` 与 `scrollTopForTurn`，且该函数体不再包含 `scrollIntoView`。
- [ ] 高亮逻辑不动。

### Task 3: 刻度轨 DOM、样式、刷新

**Files:**
- Modify: `webui/static/index.html`（`.messages-shell` 内、`#messages` 之前或之后均可，只要不是 `#messages` 内部）
- Modify: `webui/static/style.css`
- Modify: `webui/static/ui.js`（`renderMessages` 末尾调用同步；滚动 rAF 里更新当前项）
- Modify: `webui/static/i18n.js`（`en`、`zh-Hans`）

- [ ] 容器 `#turnNavigator`：`role="navigation"`，默认隐藏。位置 `absolute; top: 16px; bottom: 16px; z-index: 8`（低于 Start/End 的 11）。宽度约 36px，不挡消息正文：可见条件是 `#msgInner` 相对 `.messages-shell` 的左 gutter ≥ 52px，且 `window.innerWidth >= 600`。
- [ ] 刻度是等宽短线按钮，纵向均匀分布。超过 24 条后行高从 22px 收到 16px，超过 40 条收到 12px，轨道 `max-height: 62vh; overflow-y: auto`。轨道自己的滚动不要冒泡去改 `#messages` 的钉（在轨上的 wheel 上 `stopPropagation`，避免被 `_recordNonMessageScrollIntent` 误当成消息区上滑）。
- [ ] 悬停或键盘聚焦时，在刻度右侧显示卡片：title、reply（空则用 i18n 占位）。卡片 `pointer-events: none`。靠近视口上下边时夹住，不溢出 shell。
- [ ] `_syncTurnNavigator()`：从 `S.messages` 调 `buildTurnOutline`。签名没变则返回。变了则重建按钮。`< 3` 或 gutter 不够则 `hidden`，但 ≥ 3 且只是 gutter 不够时 DOM 仍在，供快捷键使用。截断时第一个子节点是「更早」按钮。
- [ ] 在 `renderMessages` 末尾调用 `_syncTurnNavigator()`。会话切到空列表时签名变化，轨隐藏。
- [ ] 当前项：滚动 rAF（`_programmaticScroll` 也要跑这一段，放在现有 early-return 之前或单独函数）里收集已渲染 `#msg-user-*` 的 `relTop`，必要时补虚拟窗口合成候选，调 `pickActiveTurn`，只切换 class / `aria-current`。
- [ ] i18n 键：`turn_nav_label`、`turn_nav_earlier`、`turn_nav_reply_empty`。英文与简体中文都写上。
- [ ] 契约测试：`index.html` 里 `#turnNavigator` 出现在 `.messages-shell` 中且不在 `id="messages"` 的 div 内部；`ui.js` 不含 `_ensureAllMessagesLoaded` 与 `_syncTurnNavigator` 的同时调用（导航同步函数体内不出现 `_ensureAllMessagesLoaded`）。

### Task 4: 键盘

**Files:**
- Modify: `webui/static/ui.js` 或 `webui/static/boot.js`（与现有 `document` keydown 放一起，放 `boot.js` 的全局快捷键块）

- [ ] `Alt+ArrowUp` / `Alt+ArrowDown` 规则见「键盘」。
- [ ] 当前项缺失时，Up 从最后一条开始，Down 从第一条开始。
- [ ] 契约测试：处理函数要求 `e.altKey`，并在 `INPUT` / `TEXTAREA` 上返回。

### Task 5: 手工核对（不写新的 e2e）

本仓库 WebUI 没有浏览器测试。实现者在本地 WebUI 看这四件事，记在 PR 说明里：

- [ ] 一条会话 ≥ 3 个提问：轨出现，点击滚到对应气泡并高亮，流式输出进行中不会被拽回底部。
- [ ] 把窗口拉窄到消息列贴边：轨消失，`Alt+↑/↓` 仍能跳。
- [ ] 打开 `transcript_virtual_window`（或 `?virt=1`）的长会话：视口停在一条长回答中间时，对应提问刻度仍是当前项；点击轨上更早的提问会把那一行渲出来。
- [ ] 首屏被截断的会话：轨只列出已加载的提问，顶部「更早」会翻出上一页，新提问出现在轨上，视口不跳。

---

## 评审记录

| 议题 | 决定 | 原因 |
|---|---|---|
| 整段搬 `TurnNavigator.tsx` | 否 | 本项目是普通 JS，没有 React。搬组件还要带 framer-motion。 |
| 轨放进 `#messages` | 否 | 滚动层会带走它。DeepMentor 也是把轨放在滚动层外面，原因是 `mask-image` 裁切；这里的原因是 overflow。 |
| 用绝对下标当 id | 否 | 翻页替换 `S.messages` 后，DOM id 仍是当前下标。持久化绝对下标会和 `jumpToTurnQuestion` 对不齐。 |
| 打开会话就拉全量历史 | 否 | `_ensureAllMessagesLoaded` 与翻页代际（`#1937`）冲突，长会话代价大。v1 只导航已加载部分。 |
| 保留 `sqrt` 权重 | 否 | 翻页后最长标题变化，全部刻度重排。等长更稳。 |
| 只修轨、不改 `jumpToTurnQuestion` | 否 | 现有跳转在平滑滚动中可能重新钉底。助手气泡上的按钮走同一函数，一起修。 |
| 设置项开关 | 不做 | 短会话和窄屏已经隐藏。开关要改 settings 面板，超出这条轨的范围。 |
| 12 语种一次翻完 | 不做 | `t()` 缺键回落英文。补译另开。 |
| 服务端问题索引 | 不做 | 要新 API。已加载目录 + 「更早」够用。 |
| 当前项只读 DOM | 不够 | 虚拟窗口会把用户行换成 top pad。必须有合成候选。 |

未决、但不挡住开工：平滑滚动结束用 `scrollend` 还是固定 400ms。计划写了「有 `scrollend` 用它，否则 400ms」。不要为了这个再引入计时器库。
