# Gateway Lifecycle (Same-Host) — RFC

> **Status**: DRAFT → REVIEWED with W13 plan ([`2026-07-12-w13-gateway-lifecycle-and-openat.md`](../../plans/2026-07-12-w13-gateway-lifecycle-and-openat.md))  
> **Date**: 2026-07-12  
> **Track**: WebUI Hermes parity — Gateway layers A/B/C (same-host only)  
> **Parent**: [`2026-07-11-webui-hermes-parity-analysis.md`](../../plans/2026-07-11-webui-hermes-parity-analysis.md) §4 + §8 DECIDED #1/#4  
> **Non-goals**: Cross-container Restart; restarting the WebUI process; mixing `gateway_watcher` with messaging gateway; `--all` multi-profile kill from WebUI

---

## 0. Decisions at a glance (G1–G10)

| # | Topic | Decision |
|---|--------|----------|
| **G1** | Scope | Lifecycle follows **WebUI active profile** (`INTELLECT_HOME` / `--profile`), same as `intellect gateway restart` |
| **G2** | Health probe | **(a′)** Prefer active-profile `gateway.pid` when live; else **root fallback**. Payload exposes `probe_scope`, `active_profile_pid`, `root_pid` |
| **G3** | Layer C API | `POST /api/gateway/{start,stop,restart}` — extend `gateway_lifecycle.py`, no parallel module |
| **G4** | Shared state | `_STATE` includes `operation: "restart"\|"start"\|"stop"\|null` |
| **G5** | Updates prove | `ensure_gateway_restarted_for_agent_update` succeeds **only** if `operation=="restart"` and `status=="completed"` |
| **G6** | Busy | Concurrent op → **409** + `status: busy` |
| **G7** | Wait | Settings panel default `wait=true`, hard cap **60s**; banner may use short poll / `wait=false`; CLI subprocess timeout may remain 120s; update prove timeout stays 90s |
| **G8** | Banner | Thin wrap of layer C restart (same helper as `POST /api/gateway/restart`) |
| **G9** | Watcher | `gateway_watcher` is WebUI `state.db` poller — **never** started/stopped by these APIs |
| **G10** | In-gateway | Refuse start/stop/restart when `_INTELLECT_GATEWAY=1` or `_HERMES_GATEWAY=1` |

---

## 1. Problem

Intellect already ships Opt-D layers A/B (`request_gateway_restart`, health banner, agent-update prove). Missing:

1. Explicit settings-panel lifecycle (Hermes layer C).
2. Honest alignment between **root** health PID history and **profile-scoped** restart.
3. Operation-aware shared state so stop/start cannot satisfy DECIDED #4 restart proof.

---

## 2. Probe algorithm (G2 / L3(a′))

```text
root_pid_path     = get_default_intellect_root() / "gateway.pid"
active_pid_path   = get_active_intellect_home() / "gateway.pid"

active_live = get_running_pid(active_pid_path) is not None
root_live   = get_running_pid(root_pid_path) is not None

if active_live:
    probe_scope = "active_profile"
    alive from active runtime
elif root_live or fresh root runtime_status:
    probe_scope = "root_fallback"
    alive from root
else:
    probe_scope = "active_profile" if active_pid_path != root_pid_path else "root"
    alive False / None per existing tri-state rules
```

UI must show profile name + `probe_scope` so operators are not told “gateway down” without knowing which PID file was checked.

---

## 3. HTTP contract

| Method | Path | Body | Notes |
|--------|------|------|-------|
| POST | `/api/gateway/restart` | `{wait?: bool}` default true for settings | 200 / 409 busy |
| POST | `/api/gateway/start` | `{wait?: bool}` | 200 / 409 busy / already running message |
| POST | `/api/gateway/stop` | `{wait?: bool}` | 200 / 409 busy / not running message |
| GET | `/api/gateway/status` | — | Include `probe_scope`, profile label, pid fields |
| POST | `/api/health/restart` | `{wait?: bool}` | **Thin wrap** → same as gateway restart helper |
| GET | `/api/health/restart/status` | — | Shared `_STATE` including `operation` |

**Auth / CSRF:** Same as other mutating WebUI routes — **not** public, **not** CSRF-exempt.

**Response shape (success):** `{ ok, status, operation, message, started_at?, finished_at? }`  
**Busy:** HTTP 409 + `{ ok: false, status: "busy", operation?, message }`

---

## 4. CLI mapping

```text
intellect [--profile NAME] gateway start|stop|restart
INTELLECT_HOME=<active home>
```

WebUI never passes `--all` or `--system` unless a future RFC says so.

---

## 5. Security & honesty

- Authenticated user only; no full stdout/stderr to browser.
- Same-host assumption; cross-container PID namespace → prefer existing freshness/`inconclusive` paths — never fake `completed`.
- Do not conflate messaging gateway with WebUI or `gateway_watcher`.

---

## 6. Acceptance

1. Settings Start/Stop/Restart call layer C; busy → 409.  
2. Banner restart uses same helper; copy mentions current profile + probe_scope when not active.  
3. Agent update prove fails if only stop/start completed.  
4. Root-only gateway + profile WebUI still shows alive via `root_fallback`.  
5. In-gateway env blocks start/stop/restart.
