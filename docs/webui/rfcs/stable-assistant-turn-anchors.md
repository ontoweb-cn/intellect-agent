# Stable Assistant Turn Anchors — RFC v1

> **Status**: DRAFT  
> **Date**: 2026-07-11  
> **Track**: WebUI Hermes parity P1-A  
> **Depends on**: Session SSE P0 (`session-sse-contract-v1`, planned) for full replay parity  
> **Non-goals**: Parallel activity journal; agent prompt-cache changes; full `transparent_stream` in MVP

---

## 1. Problem

Intellect already has `.assistant-turn`, live Activity groups, inflight localStorage, and `run_journal` replay — but **live / replay / settled / inflight** are ad-hoc. Hermes-style `activity_scene_v1` persistence and delayed settled worklog materialization are missing. A commented `_convertLiveActivityGroupToSettled` helper does not exist.

## 2. Goals

1. One **scene contract** that can render identically in four modes: `live`, `replay`, `settled`, `inflight`.
2. **Evolve** existing DOM (`ensureActivityGroup`, `#liveAssistantTurn`) — do not invent a second transcript model.
3. Persist scenes **bounded** (localStorage MVP; server via `run_journal` / `turn_journal` after P0).
4. Keep agent **prompt cache** untouched (WebUI-only).

## 3. `activity_scene_v1` (sketch)

```json
{
  "v": 1,
  "turn_id": "assistant:12",
  "stream_id": "strm_…",
  "session_id": "…",
  "mode": "live|replay|settled|inflight",
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

- `display` maps today's `simplified_tool_calling` (`true` → `compact_worklog`).
- Caps: reuse `inflight_state_max_*` style limits; drop-oldest segments if over budget.

## 4. Render modes

| Mode | Source | DOM |
|------|--------|-----|
| `live` | SSE + in-memory scene | `#liveAssistantTurn` + `data-live-*` |
| `inflight` | localStorage scene / messages | Rebuild via same segment renderer |
| `replay` | `run_journal` after_seq (+ scene if present) | Same tree as live, no live attrs |
| `settled` | History messages + optional deferred worklog | `.assistant-turn` without live markers |

**Invariant**: switching mode must not duplicate Activity groups or thinking blocks.

## 5. MVP (ship before full server persistence)

1. Implement `_convertLiveActivityGroupToSettled(liveGroup, settledKey)` — strip live attrs, set disclosure key.
2. Stable keys: `live:{streamId}` → `assistant:{idx}` on `done`.
3. Snapshot bounded scene into inflight localStorage alongside existing inflight state.
4. Config alias: `chat_activity_display_mode` → existing `simplified_tool_calling` (compat).
5. Document settle scroll: preserve `_scrollAfterMessageRender` / open-tool signatures.

## 6. Full (after Session SSE P0 client)

1. Append scene snapshots on terminal events into existing journal path (**no** parallel log).
2. Replay rebuilds identical Activity tree from journal + scene.
3. Idle-deferred worklog materialization (`requestIdleCallback` or expand-on-open) for N+ tools.
4. Optional true `transparent_stream` rendering.

## 7. Acceptance

- Mid-stream refresh: Activity position, disclosure, tool count match.
- SSE disconnect + journal replay: no duplicate Activity/thinking; settled layout matches live.
- Fast session switch during stream: no cross-session DOM (#1366).
- Display mode toggle respected on live and settled without full reload.
- Long turn (20+ tools): scroll stable through settle.

## 8. Out of scope

- Transcript spacer virtualization (P1-B).
- Gateway restart / SessionChannel.
- Changing tool schemas or agent loop.

## 9. Open questions

1. Scene rows in `run_journal` vs `turn_journal`? **Lean run_journal** (same stream cursor as SSE).  
2. When to materialize deferred worklog by default? **N ≥ 8 tools** tentative.
