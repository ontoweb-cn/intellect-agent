# Stable Assistant Turn Anchors — RFC v1

> **Status**: **REVIEWED** — 2026-07-12  
> **Date**: 2026-07-12  
> **Track**: WebUI Hermes parity P1-A (W3)  
> **Parent plan**: [`docs/plans/2026-07-12-w3-turn-anchors-p1a.md`](../../plans/2026-07-12-w3-turn-anchors-p1a.md)  
> **Depends on**: Session SSE P0 ([`session-sse-contract-v1.md`](./session-sse-contract-v1.md), **REVIEWED**) for journal cursor + replay  
> **Non-goals**: Parallel activity journal; agent prompt-cache changes; full `transparent_stream` UI; P1-B transcript virtualization

---

## 0. Decisions at a glance (A1–A8)

| # | Topic | Locked answer |
|---|--------|---------------|
| **A1** | Scene 落点 | **`run_journal`** 同行 cursor（与 SSE S1 一致）；wire `event:` = `activity_scene`；**禁止**平行 log |
| **A2** | 何时双写 | **仅 terminal 路径一次**：先 `activity_scene`，再 `done` / `cancel` / `apperror` / `stream_end`；live 中只更新 inflight localStorage |
| **A3** | Payload | Wire envelope §4.0；最小集：`v,turn_id,stream_id,session_id,mode,display,disclosure,segments[],elapsed_ms` |
| **A3a** | 作者 | **服务端**从 journal 已写 tool/thinking/text 组 segments；`disclosure` **默认** `{expanded:false,user_intent:null}`；真实 disclosure **仅 localStorage**（W3 不上传） |
| **A4** | Segment cap | Provisional **max_segments=40**；drop-oldest tool/thinking（保留最新 text） |
| **A5** | Deferred worklog | W3 **只文档** `N≥8`；实现延期（W4+） |
| **A6** | Display alias | `chat_activity_display_mode`: `compact_worklog` \| `transparent_stream` → 映射今日 `simplified_tool_calling` |
| **A7** | Opt-C 范围 | convert = **disclosure remap**（W2 已有）；C-A1 闪烁属 MVP **可选**，非 RFC / journal 切片阻塞 |
| **A8** | Seq / 终端性 | Scene **推进** journal/SSE `seq`；`terminal: false`；**不得**进 `_TERMINAL_SSE_EVENTS`；不得当 `latest_run_summary` 终端 |

**Session SSE S5 alignment:** Session SSE does **not** require per-frame scene dual-write. This RFC defines **later optional** `activity_scene` rows on the **same** `run_journal` (see companion §4.4). That path is explicitly allowed and is **not** an S5 violation.

---

## 1. Problem (W2 reality)

Intellect already has `.assistant-turn`, live Activity groups, inflight localStorage, and `run_journal` replay — but **live / replay / settled / inflight** still do not share one scene contract.

**W2 already landed (code):**

- `_convertLiveActivityGroupToSettled` **exists** — Opt-C is **disclosure remap** (`live:{streamId}` → `assistant:{idx}` on settle).
- Session SSE resume client + bounded offline buffer + wakeup pause (B4) are in tree (pending merge).

**Still missing for P1-A:**

- Shared `activity_scene_v1` persistence (localStorage MVP, then one terminal-path journal row).
- Config alias `chat_activity_display_mode` → `simplified_tool_calling`.
- Replay that rebuilds Activity from a bounded scene instead of guessing from flat tool SSE alone.
- Idle-deferred worklog materialization (documented threshold only in W3).

C-A1 settle flash reduction is an **optional** MVP enhancement — not a blocker for journal scene.

---

## 2. Goals

1. One **scene contract** that can render identically in four modes: `live`, `replay`, `settled`, `inflight`.
2. **Evolve** existing DOM (`ensureActivityGroup`, `#liveAssistantTurn`, Opt-C convert) — do not invent a second transcript model.
3. Persist scenes **bounded**: localStorage MVP first; then one optional `activity_scene` row on the same `run_journal` / `put()` path (A1–A2).
4. Keep agent **prompt cache** untouched (WebUI-only).

---

## 3. `activity_scene_v1` (frozen contract)

### 3.1 Object shape

```json
{
  "v": 1,
  "turn_id": "live:strm_…",
  "stream_id": "strm_…",
  "session_id": "…",
  "mode": "live|replay|settled|inflight|interrupted",
  "display": "compact_worklog|transparent_stream",
  "disclosure": { "expanded": false, "user_intent": null },
  "segments": [
    { "kind": "thinking", "text": "…" },
    { "kind": "tool", "tid": "…", "name": "…", "status": "done", "summary": "…" },
    { "kind": "text", "anchor": "…" }
  ],
  "elapsed_ms": 0
}
```

| Field | Rule |
|-------|------|
| `v` | Always `1` inside the scene object. |
| `turn_id` | During live / terminal write: prefer `live:{stream_id}`; after `done`, client remaps disclosure to `assistant:{idx}` (W2 Opt-C). |
| `display` | Maps today's `simplified_tool_calling` (`true` → `compact_worklog`). Alias: A6. |
| `disclosure` | Server default `{expanded:false,user_intent:null}` (A3a). Real UI disclosure stays localStorage-only in W3. |
| `segments` | Built by **server** from already-journaled tool/thinking/text for journal rows (A3a). Client builds the same shape for inflight localStorage MVP. |
| Cap | **max_segments=40** (A4): drop-oldest tool/thinking; keep newest text. |

### 3.2 Wire envelope (§4.0)

```text
SSE / journal row:
  event: activity_scene
  id:   <seq>          # advances cursor; not a control frame
  data: <JSON = activity_scene_v1 object at root>
        # data root IS the scene — not { "scene": {...} }
```

- Old clients: unknown `event:` → ignore (same as today).
- Unlike `session_snapshot`: snapshot has **no** journal seq; `activity_scene` **does** advance seq (A8).

---

## 4. Render modes

| Mode | Source | DOM |
|------|--------|-----|
| `live` | SSE + in-memory / inflight scene | `#liveAssistantTurn` + `data-live-*` |
| `inflight` | localStorage `activity_scene_v1` | Rebuild via same segment renderer |
| `replay` | `run_journal` after_seq (+ `activity_scene` if present) | Same tree as live, no live attrs |
| `settled` | History messages + Opt-C disclosure remap (+ optional deferred worklog later) | `.assistant-turn` without live markers |

**Invariant**: switching mode must not duplicate Activity groups or thinking blocks.

**Replay latch (journal path):** once `activity_scene` applied for a `stream_id`, ignore flat tool/thinking frames for Activity tree construction; on terminal, remap disclosure — never create a second live Activity group.

---

## 5. MVP vs journal slice (W3 sequencing)

### 5.1 W2 already done (not re-scoped)

1. `_convertLiveActivityGroupToSettled` — **disclosure remap** (Opt-C / A7).
2. Stable keys: `live:{streamId}` → `assistant:{idx}` on `done`.
3. Session SSE client resume + journal-first fill (P0 path).

### 5.2 P1-A MVP (localStorage + alias — no journal scene yet)

1. Snapshot bounded `activity_scene_v1` into inflight localStorage (`scene` field; same A3 shape; A4 cap in compact).
2. Restore Activity from scene on mid-stream refresh / session switch-back (fallback: today's messages/toolCalls).
3. Config alias **A6**: `chat_activity_display_mode` ↔ `simplified_tool_calling` (dual-write one release; read path accepts both permanently).
4. Preserve settle scroll signatures (`_scrollAfterMessageRender` / open-tool) — A-M6.
5. Display toggle applies to live **and** settled without full reload — A-M5.
6. **Optional:** C-A1 settle flash reduction — enhance only; failure falls back to today's path; **not** blocking.

### 5.3 Journal scene first slice (after MVP + Session SSE merge)

1. On terminal path **only** (A2): `put(activity_scene)` **then** `put(done|cancel|apperror|stream_end)`.
2. Same `run_journal` + `streaming.put()` path as other durable events (A1) — **no** journal-only append, **no** parallel log.
3. Cancel / apperror still emit exactly one scene (`mode: interrupted` or equivalent; segments may be truncated).
4. Replay rebuilds Activity from scene; latch prevents double-render with flat tools.
5. Oversize: shrink segments/detail; **always keep the seq row** (no skipped seq → fake gap). Dual caps: ≤ StreamChannel `DEFAULT_MAX_BYTES` (2 MiB) per event; replay still under `MAX_REPLAY_EVENTS` / `MAX_REPLAY_BYTES`.

### 5.4 Deferred (not W3 implementation)

- Idle-deferred worklog (`requestIdleCallback` / expand-on-open) for **N≥8** tools — **doc-only** in W3 (A5); implement later.
- Full `transparent_stream` UI — alias only in W3 (A6 / R5).
- Client upload of disclosure / scene to server.
- Live per-tool scene writes.

---

## 6. Full path (after Session SSE P0)

Session SSE S5 forbids **forcing** scene dual-write as part of the Session SSE contract. This RFC is the **later optional rows** path on the same journal (companion Session SSE §4.4):

1. Append one scene snapshot on the terminal path into the existing journal (**no** parallel log) — A1/A2/A8.
2. Replay rebuilds an identical Activity tree from journal + scene.
3. Idle-deferred worklog materialization for N+ tools (threshold locked at **N≥8**, implementation deferred).
4. Optional true `transparent_stream` rendering (post-W3).

---

## 7. Acceptance

| # | Criterion |
|---|-----------|
| A-M1 | Mid-stream refresh: Activity position, disclosure, tool count match (localStorage scene). |
| A-M2 | Fast session switch during stream: no cross-session Activity DOM (#1366). |
| A-M3 | `chat_activity_display_mode=compact_worklog` ≡ today's simplified on. |
| A-M4 | Cap: >40 segments drops oldest tool/thinking; inflight still writable. |
| A-M5 | **Display mode toggle** respected on live and settled **without full reload**. |
| A-M6 | **Long turn (20+ tools):** scroll stable through settle (not worse than W2). |
| A-J1 | Each stream journals exactly one `activity_scene` **before** terminal (including cancel/apperror). |
| A-J2 | Dead-worker replay + after_seq: no duplicate Activity; settled disclosure key = `assistant:{idx}`. |
| A-J3 | Old journals without scene: legacy flat replay unchanged. |
| A-J4 | Oversized scene truncated but parseable; seq contiguous; no fake `gap`. |
| A-J5 | Scene not in `_TERMINAL_SSE_EVENTS`; channel does not close on scene. |

---

## 8. Out of scope

- Parallel activity / turn journal (forbidden — A1).
- Agent prompt-cache or tool-schema / agent-loop changes.
- Full `transparent_stream` UI in W3.
- Transcript spacer virtualization (P1-B).
- Gateway restart / Journey P1-3 / SessionChannel.
- Per-frame live scene dual-write (S5 / A2).
- Implementing Idle-deferred worklog in W3 (A5 doc-only).

---

## 9. Open questions — closed

| # | Question | Locked answer |
|---|----------|---------------|
| 1 | Scene rows in `run_journal` vs `turn_journal`? | **`run_journal`** (A1) — same stream cursor as SSE. |
| 2 | When to materialize deferred worklog by default? | **N ≥ 8 tools** (A5) — document in W3; implement later. |
| 3 | Does scene advance SSE seq? | **Yes** (A8); `terminal: false`; not in `_TERMINAL_SSE_EVENTS`. |
| 4 | Scene vs done order? | **scene → terminal** (A2); never after-done. |
| 5 | Who authors server scene? | **Server** segments; default disclosure (A3a). |
| 6 | Emit path? | Same `put()` as other durable events (A1); no journal-only. |
| 7 | Opt-C / C-A1? | Opt-C = disclosure remap (done); C-A1 flash optional (A7). |

---

## 10. Document history

| Date | Note |
|------|------|
| 2026-07-11 | DRAFT v1 — P1-A sketch; open A1/A5 |
| 2026-07-12 | **REVIEWED** — lock A1–A8 + wire envelope; align with W2 Opt-C + Session SSE S5/§4.4; W3 plan SoT |
