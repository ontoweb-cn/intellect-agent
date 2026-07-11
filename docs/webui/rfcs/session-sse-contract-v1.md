# Session SSE Contract — RFC v1

> **Status**: REVIEWED — **Approve** (2026-07-12)  
> **Date**: 2026-07-12  
> **Track**: WebUI Hermes parity P0 (W1 Track B0)  
> **Parent plan**: [`docs/plans/2026-07-12-w1-journey-e2e-and-session-sse.md`](../../plans/2026-07-12-w1-journey-e2e-and-session-sse.md) §3  
> **Parity**: [`docs/plans/2026-07-11-webui-hermes-parity-analysis.md`](../../plans/2026-07-11-webui-hermes-parity-analysis.md) §3 P0 + §8 DECIDED #2  
> **Companion**: [`stable-assistant-turn-anchors.md`](./stable-assistant-turn-anchors.md) (P1-A **REVIEWED**; Session SSE does **not** force per-frame scene dual-write — Turn Anchors may **later** append optional `activity_scene` rows on the same `run_journal`, which is **not** an S5 violation)  
> **Non-goals (W1)**: Claiming P0 complete; wakeup gate (B4); shipping B2+B3 without a same-milestone client

---

## 0. Decisions at a glance (S1–S9)

| # | Topic | Decision |
|---|--------|----------|
| **S1** | Data plane | **Extend** existing per-`(session_id, run_id)` `run_journal` jsonl. **No** parallel per-session event log. |
| **S2** | Disconnect | Bounded offline buffer; resume **journal-fills first**; `session_snapshot` only when continuity cannot be proven. |
| **S3** | Cursor lock | Lock resume cursor + journal baseline **before** response headers (TOCTOU-safe). |
| **S4** | Client | Same milestone as server (B2+B3). Pattern: `kanban_bridge` `Last-Event-ID`. |
| **S5** | Turn Anchors | This RFC does **not** require per-frame `activity_scene_v1` dual-write. **Later optional** scene rows on the same `run_journal` (Turn Anchors A1/A2) are allowed and are **not** an S5 violation — see §4.4. |
| **S6** | Endpoint | Evolve `GET /api/chat/stream` (+ optional later alias). **Do not** claim `GET /api/sessions/{id}/events` without disambiguating list SSE. |
| **S7** | Cursor identity | `event_id = {run_id}:{seq}` (today’s shape). Session resume = active run only; no silent multi-journal stitch. |
| **S8** | `session_snapshot` | Triggers + minimal fields defined below. |
| **S9** | B1 bounds (provisional) | `max_events=500`, `max_bytes≈2MiB`, drop-oldest, `dropped_offline_events` (+ optional `lowest_retained_seq`). **No** snapshot emission in B1. |

**W1 ≠ P0 done.** Wakeup / credential-exhaustion pause (B4) and a resumable EventSource client remain P0 backlog. This document only locks the contract so B1–B3 can be reviewed and scheduled.

---

## 1. Problem

Intellect WebUI already journals stream SSE into `run_journal` and can replay via `GET /api/chat/stream?stream_id=&after_seq=`. That is **not** yet a resumable session contract:

1. Live frames emit `id:` from `STREAM_LAST_EVENT_ID`, and `messages.js` tracks `_lastRunJournalSeq`, but there is no unified **Last-Event-ID → lock cursor → replay → live attach → honest gap** protocol.
2. `StreamChannel._offline_buffer` is an **unbounded** `list` (`webui/api/config.py`). Long disconnects can grow memory without a diagnosable drop.
3. Hermes-style “session events” naming collides with an **existing** list-invalidation SSE at `GET /api/sessions/events` (not per-session run events).
4. Parity analysis DECIDED #2: evolve `run_journal`, same-milestone client — do **not** ship server-only Phase-1.

Without this contract, mid-stream refresh, tab sleep, and proxy drops continue to risk duplicate renders, silent gaps, or unbounded buffering.

---

## 2. Non-goals

| In scope later / elsewhere | Explicitly out of this RFC / W1 |
|----------------------------|----------------------------------|
| B1 bounded `StreamChannel` buffer (optional same-wave PR using S9 caps only) | Claiming **P0 complete** |
| B2 endpoint hardening + journal-first resume + B3 client (after REVIEWED) | Wakeup / `process_wakeup_paused` (B4) |
| Formalizing today’s journal event types | Parallel `SessionChannel` or second event log |
| Optional path alias after chat/stream facade | Forcing per-frame P1-A scene dual-write as part of *this* contract (S5); later optional rows are Turn Anchors' job |
| Journal **replay** byte/event caps (parity P0 “有界回放”) | Deferred to **B2** — B1 only bounds the **memory** offline buffer |
| | P1-B transcript virtualization |
| | Gateway restart / Journey P1-3 |

---

## 3. Existing surfaces (code reality)

### 3.1 Durable journal — `webui/api/run_journal.py`

- Path: `{SESSION_DIR}/_run_journal/{session_id}/{run_id}.jsonl`
- One append-only file per `(session_id, run_id)` (run_id ≈ stream_id today).
- Each row includes: `version`, `event_id`, `seq`, `run_id`, `session_id`, `event`/`type`, `created_at`, `terminal`, `terminal_state`, `payload`.
- **`event_id` shape today**: `f"{run_id}:{assigned_seq}"` (S7 locks this).

### 3.2 Live buffer — `StreamChannel` (`webui/api/config.py`)

- Per-stream in-memory broadcast channel.
- While no subscriber: append to `_offline_buffer: list[tuple[str, object]]` (**unbounded** today).
- On subscribe: replay buffer under lock, then fan-out.
- Diagnostics today: `subscriber_count`, `offline_buffered_events` (no drop counter yet — S9 adds `dropped_offline_events`).

### 3.3 Replay + live SSE — `GET /api/chat/stream`

- Routed in `webui/api/routes.py` → `_handle_sse_stream`.
- If `STREAMS[stream_id]` missing but journal summary exists → `_replay_run_journal` with `after_seq`.
- If live: subscribe; emit `id:` from `STREAM_LAST_EVENT_ID[stream_id]` (stage-364) so reconnect cursors advance during live streaming.
- Cursor parse helper: `runtime_adapter._cursor_to_after_seq` accepts `{run_id}:{seq}` or bare seq (takes suffix after last `:`).

### 3.4 Frontend cursor — `webui/static/messages.js`

- `_lastRunJournalSeq` advanced from `EventSource.lastEventId` via `_rememberRunJournalCursor`.
- Replay query: `&replay=1&after_seq=<seq>` (intent flag; server chooses replay when the live worker is gone).

### 3.5 Name collision — `GET /api/sessions/events` (**list** SSE)

Already exists as **session-list invalidation** (`_handle_session_events_stream`): lightweight `sessions_changed`-style events for the sidebar (`sessions.js` `EventSource('api/sessions/events')`).

| Path | Purpose |
|------|---------|
| `GET /api/sessions/events` | **List** SSE — sidebar / session inventory invalidation |
| `GET /api/chat/stream?stream_id=` | **Run** SSE — chat tokens, tools, done/cancel (this contract) |
| *(optional later)* `GET /api/sessions/{id}/runs/{run_id}/events` | Alias / facade over the same run journal — **not** the list endpoint |

**Do not** document or implement “session events” as `GET /api/sessions/events` for run resume. That path is taken.

### 3.6 Reference pattern — `kanban_bridge`

`GET /api/kanban/events/stream`: `?since=` → else `Last-Event-ID` → else `0`; every frame emits `id: <cursor>`. Session run SSE should mirror this resolution chain (S4 / S6).

---

## 4. Event model

### 4.1 Identity

| Field | Meaning |
|-------|---------|
| `run_id` / `stream_id` | One agent run / stream (journal file key). |
| `seq` | Monotonic int **within** that run, starting at 1. |
| `event_id` | Canonical SSE `id:` value: `{run_id}:{seq}`. |

Clients may store either full `event_id` or numeric seq for a known `run_id`. Server resume parsing **must** accept both (existing `_cursor_to_after_seq` behavior).

### 4.2 Journal row (v1)

Unchanged from today’s writer; contract consumers treat these as the durable source of truth:

```json
{
  "version": 1,
  "event_id": "strm_abc:12",
  "seq": 12,
  "run_id": "strm_abc",
  "session_id": "sess_…",
  "event": "token",
  "type": "token",
  "created_at": 1720000000.0,
  "terminal": false,
  "terminal_state": null,
  "payload": {}
}
```

Terminal names already special-cased in journal / stream loops include: `done`, `cancel`, `apperror`, `error`, `stream_end` (and synthetic stale-interrupted on incomplete journals).

### 4.3 Wire frames

- Durable / replayed events: SSE `event:` + `data:` + `id: {event_id}`.
- Live frames: same `id:` via `STREAM_LAST_EVENT_ID` (or direct journal `event_id` once B2 unifies).
- Control / recovery: `event: session_snapshot` (S8) — **not** written as a normal journal row unless a later revision explicitly decides to; v1 treats it as a **connection control frame**.
- Control payload field name: `"v": 1` (intentional; journal rows keep `"version": 1` — different envelope).
- Heartbeats remain SSE comments (`: heartbeat`) and do not advance the cursor.

### 4.4 Mapping today’s payloads

No rename wave in v1. Existing chat SSE event names continue to flow through the journal. Turn Anchors / scene snapshots (P1-A) may **later** append optional `activity_scene` rows into the **same** journal (terminal-path once; see Turn Anchors A1/A2/A8). That later path is **out of scope for Session SSE** and does **not** violate S5 — S5 only forbids making scene dual-write a requirement of *this* contract or forcing it on every frame.

---

## 5. Resume protocol (S6–S8)

### 5.1 S6 — Endpoint naming

**Lean decision:** evolve **`GET /api/chat/stream`** as the resumable run SSE facade.

Required behaviors (B2):

1. Accept resume cursor from (in order): query `after_seq` / `cursor` → `Last-Event-ID` → start-of-run / snapshot.
2. Keep `stream_id` (run_id) as the primary attach key when known.
3. Optionally add session-scoped bootstrap: e.g. `?session_id=` when the client only knows the session (resolves `active_stream_id`, then attaches or snapshots).

**Query `cursor` (B2):** alias for the same identity as SSE `id:` / `event_id`. Accept bare int seq **or** `{run_id}:{seq}` via the same rules as `_cursor_to_after_seq`. Precedence: **query wins over** `Last-Event-ID` when both are present. If `cursor` embeds a `run_id` that disagrees with `stream_id` → `stale_run` / `unknown_cursor`; **never** apply run A’s seq onto run B.

Today’s `replay=1` query flag remains a client intent hint; the server may ignore it once resume is driven solely by cursor + live/journal presence.

**Optional later alias** (same handler, documentation-only until needed):

`GET /api/sessions/{session_id}/runs/{run_id}/events`

**Forbidden without explicit rename of list SSE:** treating `GET /api/sessions/{id}/events` or `GET /api/sessions/events` as the run-resume endpoint. Document the collision in every API table and OpenAPI note.

### 5.2 S7 — Cursor identity and session algorithm

**Per-run cursor:** `event_id = {run_id}:{seq}` (or current `STREAM_LAST_EVENT_ID` shape — same string).

**Session resume (default = active run only):**

```text
1. Resolve session_id (cookie/profile-scoped auth as today).
2. Freeze resume cursor (S3) before headers.
3. If client supplies stream_id/run_id (or Last-Event-ID / cursor embeds run_id):
     target that run.
   Else if server knows active_stream_id for the session:
     target that run.
   Else:
     emit session_snapshot (no_active_run / journal_missing), then idle or wait —
     do not invent events.
4. For the target run (B2 live or dead worker):
     a. Journal-backfill from locked after_seq through journal high-water
        when rows exist and are contiguous from the cursor (S1 SoT).
     b. Then live-attach for remainder if the stream worker is still up.
     c. session_snapshot(reason=gap|…) only if journal (and any retained
        offline-buffer window) cannot prove contiguous seq after the cursor.
5. Bare subscribe() with no cursor = “live tail only” (today’s multi-tab
   behavior when another subscriber already cleared the offline buffer).
   That path is NOT the resumable contract — resumable clients always send a cursor.
```

**Multi-run rules:**

- **Do not** silently concatenate multiple run journals into one SSE subscription.
- Cross-run resume requires an **explicit** cursor that names the run (`event_id` with `run_id`, or `stream_id` + `after_seq`).
- Default subscription scope = **active run only**. Historical runs: open a new attach with that `run_id` (replay-only) or reload messages via REST.

**Stale Last-Event-ID** (run_id no longer active / unknown): treat as unknown cursor → `session_snapshot` with `reason=unknown_cursor` or `stale_run`, then follow active-run or idle path. Do not apply seq from run A onto run B.

**Malformed cursor (B2 breaking change):** today’s `_parse_run_journal_after_seq` / `_cursor_to_after_seq` may coerce garbage → `0` (full replay / duplicate risk). Under this contract, unparseable cursors **must** yield `unknown_cursor` snapshot — not `after_seq=0`.

### 5.3 S3 — TOCTOU cursor lock

Before `end_headers()` on a resume connection:

1. Parse and **freeze** the resume cursor (header/query).
2. Snapshot journal high-water / baseline for that run (or decide “no journal”).
3. Decide replay window vs live attach vs `session_snapshot`.
4. Only then write SSE headers and emit frames.

No concurrent writer may change the *interpretation* of the locked cursor mid-handshake. Live appends after the lock are fine — they appear after replay as new frames.

### 5.4 S8 — `session_snapshot`

**Triggers (any one):**

| `reason` | When |
|----------|------|
| `gap` | After journal-first fill attempt, server still cannot guarantee contiguous seq after cursor (B2). Offline-buffer eviction alone is **not** sufficient. |
| `unknown_cursor` | Malformed / unparseable / foreign `Last-Event-ID` or `cursor` (B2: must not coerce to `0`). |
| `stale_run` | Cursor names a run that is not active and not safely attachable. |
| `no_active_run` | Session idle; no live stream to attach. |
| `journal_missing` | Expected journal file / summary absent. |

**Minimal payload (v1):**

```json
{
  "v": 1,
  "session_id": "sess_…",
  "active_stream_id": "strm_…" ,
  "messages_reload": true,
  "messages_tail_hint": null,
  "reason": "gap",
  "detail": optional short string
}
```

- `active_stream_id`: string or `null`.
- `messages_reload`: boolean — client should refetch session messages (or equivalent) rather than assume transcript continuity.
- `messages_tail_hint`: optional opaque hint (e.g. last message id / count); may be `null` in v1 if reload is enough.
- After snapshot, server may close, hold for next run, or attach live **only** if `active_stream_id` is non-null and the client opts in — product detail for B2 plan; honesty of the gap is mandatory.

---

## 6. Bounds (S9 + path to formal constants)

### 6.1 Provisional B1 constants (may land before or with RFC REVIEWED)

| Knob | Provisional value | Behavior |
|------|-------------------|----------|
| `max_events` | **500** | Cap on `StreamChannel` offline buffer length |
| `max_bytes` | **≈ 2 MiB** | Cap on serialized payload bytes in the offline buffer |
| Eviction | **drop-oldest** | Whichever limit hits first |
| Oversize single | **reject** | If one event’s accounted size `> max_bytes`, do **not** buffer it; increment `dropped_offline_events` (journal remains SoT) |
| Diagnostics | **`dropped_offline_events`** | Monotonic counter on channel / aggregate health stats |
| Optional | **`lowest_retained_seq`** | Channel-local diagnostic only — **not** exposed on unauthenticated `/health?deep=1` |

**Accounting rules (hardening):** size uses strict JSON encode (no `default=str`) plus a str/bytes content floor; serialization failure (cycles / non-JSON) **fail-closed** as oversize. Estimation runs **outside** the channel lock.

**B1 scope split (required):** B1 ships **caps + drop-oldest + diagnostics only**. It does **not** emit `session_snapshot`. Dual-write today already persists events to `run_journal`; an offline-buffer drop/reject is **not** by itself a durable gap.

**B2 gap decision:** on resume, journal-fill from the locked cursor; emit `session_snapshot` with `reason=gap` **only if** the journal (and any retained offline window) cannot guarantee contiguous `seq` after the cursor. Never force a transcript reload solely because the in-memory offline buffer evicted rows that still exist on disk.

### 6.2 Formal constants (B2+)

After soak, promote S9 values to named config (e.g. under WebUI streaming settings) with the same semantics. Changing limits is an operational tuning change, not a wire-protocol break, as long as gap remains honest.

**Journal replay bounds** (parity P0 “有界回放” line/byte caps on `_replay_run_journal`) are **deferred to B2** — orthogonal to B1’s memory offline-buffer caps. When B2 adds replay caps, exceeding them without journal continuity → `session_snapshot`/`gap`.

Journal disk retention / max file size remains out of B1 scope.

---

## 7. Auth / profile scope

- Same auth surface as today’s chat stream (session cookie / WebUI auth gate).
- **Profile / home isolation:** journal paths and `active_stream_id` resolution must use the **active** Intellect home (existing chat/stream profile wiring). No cross-profile stream attach.
- List SSE (`/api/sessions/events`) remains a separate, lighter channel; it does not authorize run-journal reads by itself.

---

## 8. Client algorithm (B3 — same milestone as B2)

Reference: kanban `Last-Event-ID` + today’s `_lastRunJournalSeq`.

```text
on connect / reconnect:
  1. Open EventSource on chat/stream (stream_id if known; else session bootstrap).
  2. Browser sends Last-Event-ID automatically when available; also keep JS seq mirror.
  3. Apply generation / session-switch guards (no cross-session DOM — #1366).
  4. On normal events: render; advance cursor from lastEventId.
  5. On session_snapshot:
       - stop assuming live continuity
       - if messages_reload: refetch messages
       - if active_stream_id: optional re-attach with empty/fresh cursor for that run
       - else: idle UI
  6. Never stitch two runs in one EventSource without an explicit user/navigation action.
```

**Milestone rule (S4):** do not merge B2 server resume without B3 client consumption in the same release train. Server-only “Phase-1” is an explicit non-goal (Hermes lesson / parity R3).

---

## 9. Acceptance + test plan

### 9.1 Contract acceptance

- [ ] S1–S9 written and REVIEWED (this doc).
- [ ] Docs and code comments disambiguate list SSE vs run SSE (S6).
- [ ] `event_id` / Last-Event-ID round-trip uses `{run_id}:{seq}` (S7).
- [ ] Gap / unknown / idle paths emit `session_snapshot` with required fields (S8).
- [ ] B1 (if shipped): limits + `dropped_offline_events` + gap on drop (S9).
- [ ] No claim of P0 complete without B3 client + B4 wakeup tracked separately.

### 9.2 Suggested tests (implementation waves)

| Wave | Tests |
|------|--------|
| B1 | Buffer caps; drop-oldest; `dropped_offline_events`; **no** snapshot side effects |
| B2 | Last-Event-ID + after_seq equivalence; TOCTOU lock; journal-first fill; stale/malformed cursor → snapshot (not `after_seq=0`); active-run-only; live+cursor backfill when offline buffer empty |
| B3 | EventSource reconnect without duplicate tokens; snapshot → reload; session switch guards |
| Cross | Replay parity with `_replay_run_journal`; no write to list SSE path |

---

## 10. Migration / rollout

1. **B0 (this RFC):** DRAFT → REVIEWED (sign-off). No runtime flag required.
2. **B1:** Land bounded buffer behind existing StreamChannel; constants from S9; metrics first, behavior compatible for clients that already tolerate reconnect.
3. **B2+B3:** Feature flag optional (e.g. `webui.session_sse_v1`) if dual-path risk is high; default path should still be the evolved `chat/stream` once stable. Alias path only after facade is proven.
4. **Deprecations:** none for list SSE. Do not repurpose `/api/sessions/events`.
5. **Turn Anchors:** may later append optional `activity_scene` rows on the same journal/cursor (companion RFC); no migration step in this RFC (S5).

---

## 11. Open questions

1. **B4 wakeup coupling** — Should a future `process_wakeup_paused` (or credential-exhaustion pause) emit a dedicated SSE event vs only appear inside `session_snapshot.detail`? **Lean:** separate event type when B4 ships; do not block this contract. P0 remains incomplete until B4 + resumable client exist.
2. **Idle hold vs close** after `no_active_run` snapshot — product choice for B2 plan (hold for next run vs close EventSource).
3. **Config names** for S9 promotion — leave to B1/B2 PR; provisional literals are enough for early buffer PR.
4. **Hermes path names** — thought alignment only; Intellect paths stay on `chat/stream` / run_journal (DECIDED #2).

---

## 12. Document history

| Date | Note |
|------|------|
| 2026-07-12 | DRAFT v1 — W1 Track B0; locks S1–S9 for RFC review |
| 2026-07-12 | Review Request changes → journal-first gap; S9/B1 split (caps only); cursor alias; malformed≠0; replay bounds → B2 |
| 2026-07-12 | **REVIEWED / Approve** — S1–S9 locked; B1 may proceed under §6.1 |
| 2026-07-12 | B1 hardening: oversize reject; fail-closed size estimate; estimate off-lock; health omits `lowest_retained_seq` |
| 2026-07-12 | S5 / §4.4 companion clarify: later optional Turn Anchors `activity_scene` rows on same journal ≠ S5 violation |
