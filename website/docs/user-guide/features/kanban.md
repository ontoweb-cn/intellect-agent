---
sidebar_position: 12
title: "Kanban (Multi-Agent Board)"
description: "Durable SQLite-backed task board for coordinating multiple Intellect profiles"
---

# Kanban — Multi-Agent Profile Collaboration

> **Want a walkthrough?** Read the [Kanban tutorial](./kanban-tutorial) — four user stories (solo dev, fleet farming, role pipeline with retry, circuit breaker) with screenshots of each. This page is the reference; the tutorial is the narrative.

Intellect Kanban is a durable task board, shared across all your Intellect profiles, that lets multiple named agents collaborate on work without fragile in-process subagent swarms. Every task is a row in `~/.intellect/kanban.db`; every handoff is a row anyone can read and write; every worker is a full OS process with its own identity.

### Two surfaces: the model talks through tools, you talk through the CLI

The board has two front doors, both backed by the same `~/.intellect/kanban.db`:

- **Agents drive the board through a dedicated `kanban_*` toolset** — `kanban_show`, `kanban_list`, `kanban_complete`, `kanban_block`, `kanban_heartbeat`, `kanban_comment`, `kanban_create`, `kanban_link`, `kanban_unblock`. The dispatcher spawns each worker with these tools already in its schema; orchestrator profiles can also enable the `kanban` toolset explicitly. The model reads and routes tasks by calling tools directly, *not* by shelling out to `intellect kanban`. See [How workers interact with the board](#how-workers-interact-with-the-board) below.
- **You (and scripts, and cron) drive the board through `intellect kanban …`** on the CLI or `/kanban …` as a slash command. These are for humans and automation — the places without a tool-calling model behind them.

Both surfaces route through the same `kanban_db` layer, so reads see a consistent view and writes can't drift. The rest of this page shows CLI examples because they're easy to copy-paste, but every CLI verb has a tool-call equivalent the model uses.

This is the shape that covers the workloads `delegate_task` can't:

- **Research triage** — parallel researchers + analyst + writer, human-in-the-loop.
- **Scheduled ops** — recurring daily briefs that build a journal over weeks.
- **Digital twins** — persistent named assistants (`inbox-triage`, `ops-review`) that accumulate memory over time.
- **Engineering pipelines** — decompose → implement in parallel worktrees → review → iterate → PR.
- **Fleet work** — one specialist managing N subjects (50 social accounts, 12 monitored services).

For the full design rationale, comparative analysis against Cline Kanban / Paperclip / NanoClaw / Google Gemini Enterprise, and the eight canonical collaboration patterns, see `docs/intellect-kanban-v1-spec.pdf` in the repository.

## Kanban vs. `delegate_task`

They look similar; they are not the same primitive.

| | `delegate_task` | Kanban |
|---|---|---|
| Shape | RPC call (fork → join) | Durable message queue + state machine |
| Parent | Blocks until child returns | Fire-and-forget after `create` |
| Child identity | Anonymous subagent | Named profile with persistent memory |
| Resumability | None — failed = failed | Block → unblock → re-run; crash → reclaim |
| Human in the loop | Not supported | Comment / unblock at any point |
| Agents per task | One call = one subagent | N agents over task's life (retry, review, follow-up) |
| Audit trail | Lost on context compression | Durable rows in SQLite forever |
| Coordination | Hierarchical (caller → callee) | Peer — any profile reads/writes any task |

**One-sentence distinction:** `delegate_task` is a function call; Kanban is a work queue where every handoff is a row any profile (or human) can see and edit.

**Use `delegate_task` when** the parent agent needs a short reasoning answer before continuing, no humans involved, result goes back into the parent's context.

**Use Kanban when** work crosses agent boundaries, needs to survive restarts, might need human input, might be picked up by a different role, or needs to be discoverable after the fact.

They coexist: a kanban worker may call `delegate_task` internally during its run.

## Core concepts

- **Board** — a standalone queue of tasks with its own SQLite DB, workspaces
  directory, and dispatcher loop. A single install can have many boards
  (e.g. one per project, repo, or domain); see [Boards (multi-project)](#boards-multi-project)
  below. Single-project users stay on the `default` board and never see the
  word "board" outside this docs section.
- **Task** — a row with title, optional body, one assignee (a profile name), status (`triage | todo | ready | running | blocked | done | archived`), optional tenant namespace, optional idempotency key (dedup for retried automation).
- **Link** — `task_links` row recording a parent → child dependency. The dispatcher promotes `todo → ready` when all parents are `done`.
- **Comment** — the inter-agent protocol. Agents and humans append comments; when a worker is (re-)spawned it reads the full comment thread as part of its context.
- **Workspace** — the directory a worker operates in. Three kinds:
  - `scratch` (default) — fresh tmp dir under `~/.intellect/kanban/workspaces/<id>/` (or `~/.intellect/kanban/boards/<slug>/workspaces/<id>/` on non-default boards). **Deleted when the task completes** — scratch is ephemeral by design, so the dir is wiped the moment the worker (or `intellect kanban complete <id>`) marks the task done. If you want to keep the worker's output, use `worktree:` or `dir:<path>` instead. The first time a scratch workspace is created on an install, the dispatcher logs a warning and emits a `tip_scratch_workspace` event on the task (visible via `intellect kanban show <id>`).
  - `dir:<path>` — an existing shared directory (Obsidian vault, mail ops dir, per-account folder). **Must be an absolute path.** Relative paths like `dir:../tenants/foo/` are rejected at dispatch because they'd resolve against whatever CWD the dispatcher happens to be in, which is ambiguous and a confused-deputy escape vector. The path is otherwise trusted — it's your box, your filesystem, the worker runs with your uid. This is the trusted-local-user threat model; kanban is single-host by design. **Preserved on completion.**
  - `worktree` — a git worktree under `.worktrees/<id>/` for coding tasks. Use `worktree:<path>` to pin the exact target path. Worker-side `git worktree add` creates it, using `--branch` when provided. **Preserved on completion.**
- **Dispatcher** — a long-lived loop that, every N seconds (default 60): reclaims stale claims, reclaims crashed workers (PID gone but TTL not yet expired), promotes ready tasks, atomically claims, spawns assigned profiles. Runs **inside the gateway** by default (`kanban.dispatch_in_gateway: true`). One dispatcher sweeps all boards per tick; workers are spawned with `INTELLECT_KANBAN_BOARD` pinned so they can't see other boards. After `kanban.failure_limit` consecutive spawn failures on the same task (default: 2) the dispatcher auto-blocks it with the last error as the reason — prevents thrashing on tasks whose profile doesn't exist, workspace can't mount, etc.
- **Tenant** — optional string namespace *within* a board. One specialist fleet can serve multiple businesses (`--tenant business-a`) with data isolation by workspace path and memory key prefix. Tenants are a soft filter; boards are the hard isolation boundary.

## Boards (multi-project)

Boards let you separate unrelated streams of work — one per project, repo,
or domain — into isolated queues. A new install has exactly one board
called `default` (DB at `~/.intellect/kanban.db` for back-compat). Users who
only want one stream of work never need to know about boards; the feature
is opt-in.

Per-board isolation is absolute:

- Separate SQLite DB per board (`~/.intellect/kanban/boards/<slug>/kanban.db`).
- Separate `workspaces/` and `logs/` directories.
- Workers spawned for a task see **only** their board's tasks — the
  dispatcher sets `INTELLECT_KANBAN_BOARD` in the child env and every
  `kanban_*` tool the worker has access to reads it.
- Linking tasks across boards is not allowed (keeps the schema simple; if
  you really need cross-project refs, use free-text mentions and look
  them up by id manually).

### Managing boards from the CLI

```bash
# See what's on disk. Fresh installs show only "default".
intellect kanban boards list

# Create a new board.
intellect kanban boards create atm10-server \
    --name "ATM10 Server" \
    --description "Minecraft modded server ops" \
    --icon 🎮 \
    --switch                   # optional: make it the active board

# Operate on a specific board without switching.
intellect kanban --board atm10-server list
intellect kanban --board atm10-server create "Restart ATM server" --assignee ops

# Change which board is "current" for subsequent calls.
intellect kanban boards switch atm10-server
intellect kanban boards show             # who's active right now?

# Rename the display name (the slug is immutable — it's the directory name).
intellect kanban boards rename atm10-server "ATM10 (Prod)"

# Archive (default) — moves the board's dir to boards/_archived/<slug>-<ts>/.
# Recoverable by moving the dir back.
intellect kanban boards rm atm10-server

# Hard delete — `rm -rf` the board dir. No recovery.
intellect kanban boards rm atm10-server --delete
```

Board resolution order (highest precedence first):

1. Explicit `--board <slug>` on the CLI call.
2. `INTELLECT_KANBAN_BOARD` env var (set by the dispatcher when spawning a
   worker, so workers can't see other boards).
3. `~/.intellect/kanban/current` — the slug persisted by `intellect kanban
   boards switch`.
4. `default`.

Slugs are validated: lowercase alphanumerics + hyphens + underscores, 1-64
chars, must start with alphanumeric. Uppercase input is auto-downcased.
Anything else (slashes, spaces, dots, `..`) is rejected at the CLI layer
so path-traversal tricks can't name a board.

## File attachments

Tasks can carry file attachments — PDFs, images, source documents — so a
worker has the source material it needs without you pasting paths into the
body and hoping it finds them.

- **Upload** — use `intellect kanban attach <id> <file>` to attach files
  to a task. Each upload is capped at 25 MB.
- **Storage** — files land under
  `<intellect-home>/kanban/attachments/<task_id>/` for the default board, or
  `<intellect-home>/kanban/boards/<slug>/attachments/<task_id>/` for a named
  board. Set `intellect_KANBAN_ATTACHMENTS_ROOT` to pin a custom location.
- **What the worker sees** — when the dispatcher hands a task to a worker,
  the worker's context includes an **Attachments** section listing each
  file's name and its **absolute path**. The worker has full file/terminal
  tool access, so it reads attachments directly (`read_file`, or shell
  tools like `pdftotext`).
- **Remove** — use `intellect kanban detach <id> <filename>` to remove an
  attachment, which deletes both the metadata row and the on-disk file.

:::note Remote terminal backends
Attachment paths resolve directly on the **local** terminal backend, which
is the default for Kanban workers. If you run workers on a remote backend
(Docker, Modal), mount the board's `attachments/` directory into the
sandbox so the absolute paths in the worker context are reachable.
:::


## Quick start

The commands below are **you** (the human) setting up the board and creating tasks. Once a task is assigned, the dispatcher spawns the assigned profile as a worker, and from there **the model drives the task through `kanban_*` tool calls, not CLI commands** — see [How workers interact with the board](#how-workers-interact-with-the-board).

```bash
# 1. Create the board (you)
intellect kanban init

# 2. Start the gateway (hosts the embedded dispatcher)
intellect gateway start

# 3. Create a task (you — or an orchestrator agent via kanban_create)
intellect kanban create "research AI funding landscape" --assignee researcher

# 4. Watch activity live (you)
intellect kanban watch

# 5. See the board (you)
intellect kanban list
intellect kanban stats
```

When the dispatcher picks up `t_abcd` and spawns the `researcher` profile, the very first thing that worker's model does is call `kanban_show()` to read its task. It doesn't run `intellect kanban show t_abcd`.

### Gateway-embedded dispatcher (default)

The dispatcher runs inside the gateway process. Nothing to install, no
separate service to manage — if the gateway is up, ready tasks get picked
up on the next tick (60s by default).

```yaml
# config.yaml
kanban:
  dispatch_in_gateway: true        # default
  dispatch_interval_seconds: 60    # default
```

Override the config flag at runtime via `intellect_KANBAN_DISPATCH_IN_GATEWAY=0`
for debugging. Standard gateway supervision applies: run `intellect gateway
start` directly, or wire the gateway up as a systemd user unit (see the
gateway docs). Without a running gateway, `ready` tasks stay where they are
until one comes up — `intellect kanban create` warns about this at creation
time.

Running `intellect kanban daemon` as a separate process is **deprecated**;
use the gateway. If you truly cannot run the gateway (headless host
policy forbids long-lived services, etc.) a `--force` escape hatch keeps
the old standalone daemon alive for one release cycle, but running both
a gateway-embedded dispatcher AND a standalone daemon against the same
`kanban.db` causes claim races and is not supported.

### Idempotent create (for automation / webhooks)

```bash
# First call creates the task. Any subsequent call with the same key
# returns the existing task id instead of duplicating.
intellect kanban create "nightly ops review" \
    --assignee ops \
    --idempotency-key "nightly-ops-$(date -u +%Y-%m-%d)" \
    --json
```

### Bulk CLI verbs

All the lifecycle verbs accept multiple ids so you can clean up a batch
in one command:

```bash
intellect kanban complete t_abc t_def t_hij --result "batch wrap"
intellect kanban archive  t_abc t_def t_hij
intellect kanban unblock  t_abc t_def
intellect kanban block    t_abc "need input" --ids t_def t_hij
```

## How workers interact with the board

**Workers do not shell out to `intellect kanban`.** When the dispatcher spawns a worker it sets `INTELLECT_KANBAN_TASK=t_abcd` in the child's env, and that env var flips on a dedicated **kanban toolset** in the model's schema. The same toolset is also available to orchestrator profiles that enable `kanban` in their toolsets config. These tools read and mutate the board directly via the Python `kanban_db` layer, same as the CLI does. A running worker calls these like any other tool; it never sees or needs the `intellect kanban` CLI.

| Tool | Purpose | Required params |
|---|---|---|
| `kanban_show` | Read the current task (title, body, prior attempts, parent handoffs, comments, full pre-formatted `worker_context`). Defaults to the env's task id. | — |
| `kanban_list` | List task summaries with filters for `assignee`, `status`, `tenant`, archived visibility, and limit. Intended for orchestrators discovering board work. | — |
| `kanban_complete` | Finish with `summary` + `metadata` structured handoff. | at least one of `summary` / `result` |
| `kanban_block` | Escalate for human input with a `reason`. | `reason` |
| `kanban_heartbeat` | Signal liveness during long operations. Pure side-effect. | — |
| `kanban_comment` | Append a durable note to the task thread. | `task_id`, `body` |
| `kanban_create` | (Orchestrators) fan out into child tasks with an `assignee`, optional `parents`, `skills`, etc. | `title`, `assignee` |
| `kanban_link` | (Orchestrators) add a `parent_id → child_id` dependency edge after the fact. | `parent_id`, `child_id` |
| `kanban_unblock` | (Orchestrators) move a blocked task back to `ready`. | `task_id` |

A typical worker turn looks like:

```
# Model's tool calls, in order:
kanban_show()                                     # no args — uses INTELLECT_KANBAN_TASK
# (model reads the returned worker_context, does the work via terminal/file tools)
kanban_heartbeat(note="halfway through — 4 of 8 files transformed")
# (more work)
kanban_complete(
    summary="migrated limiter.py to token-bucket; added 14 tests, all pass",
    metadata={"changed_files": ["limiter.py", "tests/test_limiter.py"], "tests_run": 14},
)
```

An **orchestrator** worker fans out instead:

```
kanban_show()
kanban_create(
    title="research ICP funding 2024-2026",
    assignee="researcher-a",
    body="focus on seed + series A, North America, AI-adjacent",
)
# → returns {"task_id": "t_r1", ...}
kanban_create(title="research ICP funding — EU angle", assignee="researcher-b", body="…")
# → returns {"task_id": "t_r2", ...}
kanban_create(
    title="synthesize findings into launch brief",
    assignee="writer",
    parents=["t_r1", "t_r2"],                     # promotes to ready when both complete
    body="one-pager, 300 words, neutral tone",
)
kanban_complete(summary="decomposed into 2 research tasks + 1 writer; linked dependencies")
```

The "(Orchestrators)" tools — `kanban_list`, `kanban_create`, `kanban_link`, `kanban_unblock`, and `kanban_comment` on foreign tasks — are available through the same toolset; the convention (enforced by the `kanban-orchestrator` skill) is that worker profiles don't fan out or route unrelated work, and orchestrator profiles don't execute implementation work. Dispatcher-spawned workers are still task-scoped for destructive lifecycle operations and cannot mutate unrelated tasks.

### Why tools instead of shelling to `intellect kanban`

Three reasons:

1. **Backend portability.** Workers whose terminal tool points at a remote backend (Docker / Modal / Singularity / SSH) would run `intellect kanban complete` *inside* the container, where `intellect` isn't installed and `~/.intellect/kanban.db` isn't mounted. The kanban tools run in the agent's own Python process and always reach `~/.intellect/kanban.db` regardless of terminal backend.
2. **No shell-quoting fragility.** Passing `--metadata '{"files": [...]}'` through shlex + argparse is a latent footgun. Structured tool args skip it entirely.
3. **Better errors.** Tool results are structured JSON the model can reason about, not stderr strings it has to parse.

**Zero schema footprint on normal sessions.** A regular `intellect chat` session has zero `kanban_*` tools in its schema unless the active profile explicitly enables the `kanban` toolset for orchestrator work. Dispatcher-spawned task workers get task-scoped tools because `INTELLECT_KANBAN_TASK` is set; orchestrator profiles get the broader routing surface through config. No tool bloat for users who never touch kanban.

The `kanban-worker` and `kanban-orchestrator` skills teach the model which tool to call when and in what order.

### Recommended handoff evidence

`kanban_complete(summary=..., metadata={...})` is intentionally flexible:
the summary is the human-readable closeout, and `metadata` is the
machine-readable handoff that downstream agents or reviewers can
reuse without scraping prose.

For engineering and review tasks, prefer this optional metadata shape:

```json
{
  "changed_files": ["path/to/file.py"],
  "verification": ["pytest tests/intellect_cli/test_kanban_db.py -q"],
  "dependencies": ["parent task id or external issue, if any"],
  "blocked_reason": null,
  "retry_notes": "what failed before, if this was a retry",
  "residual_risk": ["what was not tested or still needs human review"]
}
```

These keys are a convention, not a schema requirement. The useful property is
that every worker leaves enough evidence for the next reader to answer four
questions quickly:

1. What changed?
2. How was it verified?
3. What can unblock or retry this if it fails?
4. What risk is still deliberately left open?

Keep secrets, raw logs, tokens, OAuth material, and unrelated transcripts out of
`metadata`. Store pointers and summaries instead. If a task has no files or
tests, say so explicitly in `summary` and use `metadata` for the evidence that
does exist, such as source URLs, issue ids, or manual review steps.

### The worker skill

Any profile that should be able to work kanban tasks must load the `kanban-worker` skill. It teaches the worker the full lifecycle in **tool calls**, not CLI commands:

1. On spawn, call `kanban_show()` to read title + body + parent handoffs + prior attempts + full comment thread.
2. `cd $intellect_KANBAN_WORKSPACE` (via the terminal tool) and do the work there.
3. Call `kanban_heartbeat(note="...")` every few minutes during long operations. **If your work may run longer than 1 hour, call `kanban_heartbeat` at least once an hour** — the dispatcher reclaims tasks that have been running past `kanban.dispatch_stale_timeout_seconds` (default 4 h) with no heartbeat in the last hour, on the assumption the worker crashed without cleanup. A reclaim is benign (the task goes back to `ready` for re-dispatch without a failure-counter tick) but you lose your current run's progress.
4. Complete with `kanban_complete(summary="...", metadata={...})`, or `kanban_block(reason="...")` if stuck.

That final `kanban_complete` / `kanban_block` call is part of the worker
protocol. If the worker process exits with status 0 while the task is still
`running`, the dispatcher treats that as a protocol violation, emits a
`protocol_violation` event, and auto-blocks the task on the next tick instead
of respawning it into the same loop. This usually means the model wrote a
plain-text answer and exited without using the Kanban tool surface.

`kanban-worker` is a bundled skill, synced into every profile during install and
update — there is no separate Skills Hub install step. Verify it is present in
whichever profile you use for kanban workers (`researcher`, `writer`, `ops`,
etc.):

```bash
intellect -p <your-worker-profile> skills list | grep kanban-worker
```

If the bundled copy is missing, restore it for that profile:

```bash
intellect -p <your-worker-profile> skills reset kanban-worker --restore
```

The dispatcher also auto-passes `--skills kanban-worker` when spawning every worker, so the worker always has the pattern library available even if a profile's default skills config doesn't include it.

### Pinning extra skills to a specific task

Sometimes a single task needs specialist context the assignee profile doesn't carry by default — a translation job that needs the `translation` skill, a review task that needs `github-code-review`, a security audit that needs `security-pr-audit`. Rather than editing the assignee's profile every time, attach the skills directly to the task.

**From an orchestrator agent** (the usual case — one agent routing work to another), use the `kanban_create` tool's `skills` array:

```
kanban_create(
    title="translate README to Japanese",
    assignee="linguist",
    skills=["translation"],
)

kanban_create(
    title="audit auth flow",
    assignee="reviewer",
    skills=["security-pr-audit", "github-code-review"],
)
```

**From a human (CLI / slash command)**, repeat `--skill` for each one:

```bash
intellect kanban create "translate README to Japanese" \
    --assignee linguist \
    --skill translation

intellect kanban create "audit auth flow" \
    --assignee reviewer \
    --skill security-pr-audit \
    --skill github-code-review
```

These skills are **additive** to the built-in `kanban-worker` — the dispatcher emits one `--skills <name>` flag for each (and for the built-in), so the worker spawns with all of them loaded. The skill names must match skills that are actually installed on the assignee's profile (run `intellect skills list` to see what's available); there's no runtime install.

### Goal-mode cards (`--goal`)

By default each worker gets **one shot** at its card — do the work, call `kanban_complete`/`kanban_block`, exit. Pass `--goal` (CLI) or `goal_mode=True` (the `kanban_create` tool / dashboard) to instead run that worker in a **goal loop**, the same Ralph-style engine behind the `/goal` slash command: after every turn an auxiliary judge checks the worker's output against the card's title + body (treated as the acceptance criteria), and if the work isn't done — and the turn budget remains — the worker keeps going **in the same session** until the judge agrees, the worker terminates the task itself, or the budget runs out (which **blocks** the card for human review rather than exiting silently).

```bash
intellect kanban create "Translate the docs site to French" \
    --body "Acceptance: every page translated, no English left, links intact." \
    --assignee linguist \
    --goal \
    --goal-max-turns 15      # optional; default 20
```

Use it for open-ended, multi-step, or "keep going until X is true" cards. Skip it for cheap one-shot work — the per-turn judge overhead isn't worth it, and the dispatcher's existing retry/circuit-breaker already handles transient worker failures. The judge is only as good as your goal text, so write the body as **explicit acceptance criteria**.

### Goal-mode cards (`--goal`)

By default each worker gets **one shot** at its card — do the work, call `kanban_complete`/`kanban_block`, exit. Pass `--goal` (CLI) or `goal_mode=True` (the `kanban_create` tool / dashboard) to instead run that worker in a **goal loop**, the same Ralph-style engine behind the `/goal` slash command: after every turn an auxiliary judge checks the worker's output against the card's title + body (treated as the acceptance criteria), and if the work isn't done — and the turn budget remains — the worker keeps going **in the same session** until the judge agrees, the worker terminates the task itself, or the budget runs out (which **blocks** the card for human review rather than exiting silently).

```bash
hermes kanban create "Translate the docs site to French" \
    --body "Acceptance: every page translated, no English left, links intact." \
    --assignee linguist \
    --goal \
    --goal-max-turns 15      # optional; default 20
```

Use it for open-ended, multi-step, or "keep going until X is true" cards. Skip it for cheap one-shot work — the per-turn judge overhead isn't worth it, and the dispatcher's existing retry/circuit-breaker already handles transient worker failures. The judge is only as good as your goal text, so write the body as **explicit acceptance criteria**.

### The orchestrator skill

A **well-behaved orchestrator does not do the work itself.** It decomposes the user's goal into tasks, links them, assigns each to one of the profiles you've set up, and steps back. The `kanban-orchestrator` skill encodes this as tool-call patterns: anti-temptation rules, a Step-0 profile-discovery prompt (the dispatcher silently fails on unknown assignee names, so the orchestrator must ground every card in profiles that actually exist on your machine), and a decomposition playbook keyed on `kanban_create` / `kanban_link` / `kanban_comment`.

A canonical orchestrator turn (two parallel researchers handing off to a writer):

```
# Goal from user: "draft a launch post on the ICP funding landscape"
kanban_create(title="research ICP funding, NA angle",  assignee="researcher-a", body="…")  # → t_r1
kanban_create(title="research ICP funding, EU angle",  assignee="researcher-b", body="…")  # → t_r2
kanban_create(
    title="synthesize ICP funding research into launch post draft",
    assignee="writer",
    parents=["t_r1", "t_r2"],        # promoted to 'ready' when both researchers complete
    body="one-pager, neutral tone, cite sources inline",
)                                     # → t_w1
# Optional: add cross-cutting deps discovered later without re-creating tasks
kanban_link(parent_id="t_r1", child_id="t_followup")
kanban_complete(
    summary="decomposed into 2 parallel research tasks → 1 synthesis task; writer starts when both researchers finish",
)
```

`kanban-orchestrator` is a bundled skill. It is synced into each profile during
install and update, so there is no separate Skills Hub install step. Verify it is
present in your orchestrator profile:

```bash
intellect -p orchestrator skills list | grep kanban-orchestrator
```

If the bundled copy is missing, restore it for that profile:

```bash
intellect -p orchestrator skills reset kanban-orchestrator --restore
```

For best results, pair it with a profile whose toolsets are restricted to board operations (`kanban`, `gateway`, `memory`) so the orchestrator literally cannot execute implementation tasks even if it tries.

## CLI command reference

This is the surface **you** (or scripts, cron) use to drive the board. Workers running inside the dispatcher use the `kanban_*` [tool surface](#how-workers-interact-with-the-board) for the same operations — the CLI here and the tools there both route through `kanban_db`, so the two surfaces agree by construction.

```
intellect kanban init                                     # create kanban.db + print daemon hint
intellect kanban create "<title>" [--body ...] [--assignee <profile>]
                                [--parent <id>]... [--tenant <name>]
                                [--workspace scratch|worktree|worktree:<path>|dir:<path>]
                                [--branch <name>]
                                [--priority N] [--triage] [--idempotency-key KEY]
                                [--max-runtime 30m|2h|1d|<seconds>]
                                [--max-retries N]
                                [--goal] [--goal-max-turns N]
                                [--skill <name>]...
                                [--json]
intellect kanban list [--mine] [--assignee P] [--status S] [--tenant T] [--archived]
        [--workflow-template-id <id>] [--current-step-key <key>]
        [--sort created|created-desc|priority|priority-desc|status|assignee|title|updated]
        [--json]
intellect kanban show <id> [--json]
intellect kanban assign <id> <profile>                    # or 'none' to unassign
intellect kanban link <parent_id> <child_id>
intellect kanban unlink <parent_id> <child_id>
intellect kanban claim <id> [--ttl SECONDS]
intellect kanban comment <id> "<text>" [--author NAME]

# Bulk verbs — accept multiple ids:
intellect kanban complete <id>... [--result "..."]
intellect kanban block <id> "<reason>" [--ids <id>...]
intellect kanban unblock <id>...
intellect kanban archive <id>...

intellect kanban tail <id>                                # follow a single task's event stream
intellect kanban watch [--assignee P] [--tenant T]        # live stream ALL events to the terminal
        [--kinds completed,blocked,…] [--interval SECS]
intellect kanban heartbeat <id> [--note "..."]            # worker liveness signal for long ops
intellect kanban runs <id> [--json]                       # attempt history (one row per run)
intellect kanban assignees [--json]                       # profiles on disk + per-assignee task counts
intellect kanban dispatch [--dry-run] [--max N]           # one-shot pass
        [--failure-limit N] [--json]
intellect kanban daemon --force                           # DEPRECATED — standalone dispatcher (use `intellect gateway start` instead)
        [--failure-limit N] [--pidfile PATH] [-v]
intellect kanban stats [--json]                           # per-status + per-assignee counts
intellect kanban log <id> [--tail BYTES]                  # worker log from ~/.intellect/kanban/logs/
intellect kanban notify-subscribe <id>                    # gateway bridge hook (used by /kanban in the gateway)
        --platform <name> --chat-id <id> [--thread-id <id>] [--user-id <id>]
intellect kanban notify-list [<id>] [--json]
intellect kanban notify-unsubscribe <id>
        --platform <name> --chat-id <id> [--thread-id <id>]
intellect kanban context <id>                             # what a worker sees
intellect kanban specify [<id> | --all] [--tenant T]      # flesh out a triage-column idea
        [--author NAME] [--json]                       #   into a full spec and promote to todo
intellect kanban gc [--event-retention-days N]            # workspaces + old events + old logs
        [--log-retention-days N]
```

All commands are also available as a slash command in the interactive CLI and in the messaging gateway (see [`/kanban` slash command](#kanban-slash-command) below).

`--max-retries` is a per-task circuit-breaker override for the dispatcher. `--max-retries 1` blocks the task on the first non-successful attempt, while `--max-retries 3` allows two retries and blocks on the third failure. Omit it to use `kanban.failure_limit` from `config.yaml`, then the built-in default.

### Concurrency, scheduling, and child promotion config

| Config key | Default | What it does |
|------------|---------|--------------|
| `kanban.max_in_progress` | unset (unlimited) | Caps the number of simultaneously running tasks. When the board already has N running, the dispatcher skips spawning more — useful for slow workers (local LLMs, resource-constrained hosts) so they finish what they have before more pile up and time out. Invalid or below-1 values log a warning and behave as unlimited. |
| `kanban.auto_promote_children` | `true` | After `decompose_triage_task()` produces children with no parent-blocker dependencies, they're automatically promoted to `ready` so the dispatcher can pick them up. Set to `false` to require manual review — children stay in `todo` until you promote them. |
| `kanban.default_workdir` | unset | Board-level default working directory applied to new tasks when neither `--workspace` nor the task itself overrides it. Per-task `workspace:` still wins. |

```yaml
kanban:
  max_in_progress: 2
  auto_promote_children: false
  default_workdir: ~/work/active-project
```

### Scheduled task starts (`scheduled_at`)

Set `scheduled_at` on a task to delay dispatch until a specific time. The dispatcher skips ready tasks whose `scheduled_at` is in the future and picks them up on the first tick after that timestamp.

```bash
intellect kanban create "nightly backup audit" \
  --assignee ops --scheduled-at "2026-06-01T03:00:00Z"
```

### Respawn guard

The dispatcher refuses to re-spawn a ready task when it hit a quota/auth/429 error on the previous run (`blocker_auth`), or completed a run successfully within the guard window (`recent_success`), or a recent task comment links to a GitHub PR (`active_pr`). This prevents repeat worker storms on the same bug or task while a human catches up. See the `respawn_guarded` row in the [event reference](#event-reference).

### Kanban Swarm topology helper

`intellect kanban swarm` creates a durable **Kanban Swarm v1** graph in one shot: a completed root/blackboard card, N parallel worker cards, a verifier card gated on all workers, and a synthesizer card gated on the verifier. Shared swarm context (the "blackboard") is stored as structured JSON comments on the root card so any worker can read it.

```bash
intellect kanban swarm "Design a multi-region failover plan" \
  --workers researcher,architect,sre \
  --verifier reviewer --synthesizer writer
```

The resulting graph dispatches normally — workers run in parallel, the verifier wakes after they all finish, the synthesizer wakes after the verifier marks the work clean.

## `/kanban` slash command {#kanban-slash-command}

Every `intellect kanban <action>` verb is also reachable as `/kanban <action>` — from inside an interactive `intellect chat` session **and** from any gateway platform (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, email, SMS). Both surfaces call the exact same `intellect_cli.kanban.run_slash()` entry point that reuses the `intellect kanban` argparse tree, so the argument surface, flags, and output format are identical across CLI, `/kanban`, and `intellect kanban`. You don't have to leave the chat to drive the board.

```
/kanban list
/kanban show t_abcd
/kanban create "write launch post" --assignee writer --parent t_research
/kanban comment t_abcd "looks good, ship it"
/kanban unblock t_abcd
/kanban dispatch --max 3
/kanban specify t_abcd                  # flesh out a triage one-liner into a real spec
/kanban specify --all --tenant engineering  # sweep every triage task in one tenant
```

Quote multi-word arguments the same way you would on a shell — `run_slash` parses the rest of the line with `shlex.split`, so `"..."` and `'...'` both work.

### Mid-run usage: `/kanban` bypasses the running-agent guard

The gateway normally queues slash commands and user messages while an agent is still thinking — that's what stops you from accidentally starting a second turn while the first is in flight. **`/kanban` is explicitly exempted from this guard.** The board lives in `~/.intellect/kanban.db`, not in the running agent's state, so reads (`list`, `show`, `context`, `tail`, `watch`, `stats`, `runs`) and writes (`comment`, `unblock`, `block`, `assign`, `archive`, `create`, `link`, …) all go through immediately, even mid-turn.

This is the whole point of the separation:

- A worker blocks waiting on a peer → you send `/kanban unblock t_abcd` from your phone and the dispatcher picks the peer up on its next tick. The blocked worker isn't interrupted — it just stops being blocked.
- You spot a card that needs human context → `/kanban comment t_xyz "use the 2026 schema, not 2025"` lands on the task thread and the *next* run of that task will read it in `kanban_show()`.
- You want to know what your fleet is doing without stopping the orchestrator → `/kanban list --mine` or `/kanban stats` inspects the board without touching your main conversation.

### Auto-subscribe on `/kanban create` (gateway only)

When you create a task from the gateway with `/kanban create "…"`, the originating chat (platform + chat id + thread id) is automatically subscribed to that task's terminal events (`completed`, `blocked`, `gave_up`, `crashed`, `timed_out`). You'll get one message back per terminal event — including the first line of the worker's result summary on `completed` — without having to poll or remember the task id.

```
you> /kanban create "transcribe today's podcast" --assignee transcriber
bot> Created t_9fc1a3  (ready, assignee=transcriber)
     (subscribed — you'll be notified when t_9fc1a3 completes or blocks)

… ~8 minutes later …

bot> ✓ t_9fc1a3 completed by transcriber
     transcribed 42 minutes, saved to podcast/2026-05-04.md
```

Subscriptions auto-remove themselves once the task reaches `done` or `archived`. If you script a create with `--json` (machine output) the auto-subscribe is skipped — the assumption is that scripted callers want to manage subscriptions explicitly via `/kanban notify-subscribe`.

### Output truncation in messaging

Gateway platforms have practical message-length caps. If `/kanban list`, `/kanban show`, or `/kanban tail` produce more than ~3800 characters of output, the response is truncated with a `… (truncated; use \`intellect kanban …\` in your terminal for full output)` footer. The CLI surface has no such cap.

### Autocomplete

In the interactive CLI, typing `/kanban ` and hitting Tab cycles through the built-in subcommand list (`list`, `ls`, `show`, `create`, `assign`, `link`, `unlink`, `claim`, `comment`, `complete`, `block`, `unblock`, `archive`, `tail`, `dispatch`, `context`, `init`, `gc`). The remaining verbs listed in the CLI reference above (`watch`, `stats`, `runs`, `log`, `assignees`, `heartbeat`, `notify-subscribe`, `notify-list`, `notify-unsubscribe`, `daemon`) also work — they're just not in the autocomplete hint list yet.

## Collaboration patterns

The board supports these eight patterns without any new primitives:

| Pattern | Shape | Example |
|---|---|---|
| **P1 Fan-out** | N siblings, same role | "research 5 angles in parallel" |
| **P2 Pipeline** | role chain: scout → editor → writer | daily brief assembly |
| **P3 Voting / quorum** | N siblings + 1 aggregator | 3 researchers → 1 reviewer picks |
| **P4 Long-running journal** | same profile + shared dir + cron | Obsidian vault |
| **P5 Human-in-the-loop** | worker blocks → user comments → unblock | ambiguous decisions |
| **P6 `@mention`** | inline routing from prose | `@reviewer look at this` |
| **P7 Thread-scoped workspace** | `/kanban here` in a thread | per-project gateway threads |
| **P8 Fleet farming** | one profile, N subjects | 50 social accounts |
| **P9 Triage specifier** | rough idea → `triage` → `intellect kanban specify` expands body → `todo` | "turn this one-liner into a spec'd task" |

For worked examples of each, see `docs/intellect-kanban-v1-spec.pdf`.

## Multi-tenant usage

When one specialist fleet serves multiple businesses, tag each task with a tenant:

```bash
intellect kanban create "monthly report" \
    --assignee researcher \
    --tenant business-a \
    --workspace dir:~/tenants/business-a/data/
```

Workers receive `$intellect_TENANT` and namespace their memory writes by prefix. The board, the dispatcher, and the profile definitions are all shared; only the data is scoped.

## Gateway notifications

When you run `/kanban create …` from the gateway (Telegram, Discord, Slack, etc.), the originating chat is automatically subscribed to the new task. The gateway's background notifier polls `task_events` every few seconds and delivers one message per terminal event (`completed`, `blocked`, `gave_up`, `crashed`, `timed_out`) to that chat. Completed tasks also send the first line of the worker's `--result` so you see the outcome without having to `/kanban show`.

You can manage subscriptions explicitly from the CLI — useful when a script / cron job wants to notify a chat it didn't originate from:

```bash
intellect kanban notify-subscribe t_abcd \
    --platform telegram --chat-id 12345678 --thread-id 7
intellect kanban notify-list
intellect kanban notify-unsubscribe t_abcd \
    --platform telegram --chat-id 12345678 --thread-id 7
```

A subscription removes itself automatically once the task reaches `done` or `archived`; no cleanup needed.

## Runs — one row per attempt

A task is a logical unit of work; a **run** is one attempt to execute it. When the dispatcher claims a ready task it creates a row in `task_runs` and points `tasks.current_run_id` at it. When that attempt ends — completed, blocked, crashed, timed out, spawn-failed, reclaimed — the run row closes with an `outcome` and the task's pointer clears. A task that's been attempted three times has three `task_runs` rows.

Why two tables instead of just mutating the task: you need **full attempt history** for real-world postmortems ("the second reviewer attempt got to approve, the third merged"), and you need a clean place to hang per-attempt metadata — which files changed, which tests ran, which findings a reviewer noted. Those are run facts, not task facts.

Runs are also where **structured handoff** lives. When a worker completes a task (via `kanban_complete(...)`) it can pass:

- `summary` (tool param) / `--summary` (CLI) — human handoff; goes on the run; downstream children see it in their `build_worker_context`.
- `metadata` (tool param) / `--metadata` (CLI) — free-form JSON dict on the run; children see it serialized alongside the summary.
- `result` (tool param) / `--result` (CLI) — short log line that goes on the task row (legacy field, kept for back-compat).

Downstream children read the most recent completed run's summary + metadata for each parent. Retrying workers read the prior attempts on their own task (outcome, summary, error) so they don't repeat a path that already failed.

```
# What a worker actually does — a tool call, from inside the agent loop:
kanban_complete(
    summary="implemented token bucket, keys on user_id with IP fallback, all tests pass",
    metadata={"changed_files": ["limiter.py", "tests/test_limiter.py"], "tests_run": 14},
    result="rate limiter shipped",
)
```

The same handoff is reachable from the CLI when you (the human) need to close out a task a worker can't — e.g. a task that was abandoned, or one you need to mark done manually:

```bash
intellect kanban complete t_abcd \
    --result "rate limiter shipped" \
    --summary "implemented token bucket, keys on user_id with IP fallback, all tests pass" \
    --metadata '{"changed_files": ["limiter.py", "tests/test_limiter.py"], "tests_run": 14}'

# Review the attempt history on a retried task:
intellect kanban runs t_abcd
#   #  OUTCOME       PROFILE           ELAPSED  STARTED
#   1  blocked       worker               12s  2026-04-27 14:02
#        → BLOCKED: need decision on rate-limit key
#   2  completed     worker                8m   2026-04-27 15:18
#        → implemented token bucket, keys on user_id with IP fallback
```

`task_events` rows carry the `run_id` they belong to so they can be grouped by attempt, and the `completed` event embeds the first-line summary in its payload (capped at 400 chars) so gateway notifiers can render structured handoffs without a second SQL round-trip.

**Bulk close caveat.** `intellect kanban complete a b c --summary X` is refused — structured handoff is per-run, so copy-pasting the same summary to N tasks is almost always wrong. Bulk close *without* `--summary` / `--metadata` still works for the common "I finished a pile of admin tasks" case.

**Reclaimed runs from status changes.** If a running task is moved off `running` (back to `ready`, or straight to `todo`), or a task is archived while still running, the in-flight run closes with `outcome='reclaimed'` rather than being orphaned. The `task_runs` row is always in a terminal state when `tasks.current_run_id` is `NULL`, and vice versa — that invariant holds across CLI, dispatcher, and notifier.

**Synthetic runs for never-claimed completions.** Completing or blocking a task that was never claimed (e.g. a CLI user runs `intellect kanban complete <ready-task> --summary X`) would otherwise drop the handoff. Instead the kernel inserts a zero-duration run row (`started_at == ended_at`) carrying the summary / metadata / reason so attempt history stays complete. The `completed` / `blocked` event's `run_id` points at that row.

### Forward compatibility

Two nullable columns on `tasks` are reserved for v2 workflow routing: `workflow_template_id` (which template this task belongs to) and `current_step_key` (which step in that template is active). The v1 kernel ignores them for routing but lets clients write them, so a v2 release can add the routing machinery without another schema migration.

## Event reference

Every transition appends a row to `task_events`. Each row carries an optional `run_id` so UIs can group events by attempt. Kinds group into three clusters so filtering is easy (`intellect kanban watch --kinds completed,gave_up,timed_out`):

**Lifecycle** (what changed about the task as a logical unit):

| Kind | Payload | When |
|---|---|---|
| `created` | `{assignee, status, parents, tenant}` | Task inserted. `run_id` is `NULL`. |
| `promoted` | — | `todo → ready` because all parents hit `done`. `run_id` is `NULL`. |
| `claimed` | `{lock, expires, run_id}` | Dispatcher atomically claimed a `ready` task for spawn. |
| `completed` | `{result_len, summary?}` | Worker wrote `--result` / `--summary` and task hit `done`. `summary` is the first-line handoff (400-char cap); full version lives on the run row. If `complete_task` is called on a never-claimed task with handoff fields, a zero-duration run is synthesized so `run_id` still points at something. |
| `blocked` | `{reason}` | Worker or human flipped the task to `blocked`. Synthesizes a zero-duration run when called on a never-claimed task with `--reason`. |
| `unblocked` | — | `blocked → ready`, either manually or via `/unblock`. `run_id` is `NULL`. |
| `archived` | — | Hidden from the default board. If the task was still running, carries the `run_id` of the run that was reclaimed as a side effect. |

**Edits** (human-driven changes that aren't transitions):

| Kind | Payload | When |
|---|---|---|
| `assigned` | `{assignee}` | Assignee changed (including unassignment). |
| `edited` | `{fields}` | Title or body updated. |
| `reprioritized` | `{priority}` | Priority changed. |
| `status` | `{status}` | A status was written directly (e.g. `todo → ready`). Carries the `run_id` of the run that was reclaimed when moving off `running`; otherwise `run_id` is NULL. |

**Worker telemetry** (about the execution process, not the logical task):

| Kind | Payload | When |
|---|---|---|
| `spawned` | `{pid}` | Dispatcher successfully started a worker process. |
| `heartbeat` | `{note?}` | Worker called `intellect kanban heartbeat $TASK` to signal liveness during long operations. |
| `reclaimed` | `{stale_lock}` | Claim TTL expired without a completion; task goes back to `ready`. |
| `crashed` | `{pid, claimer}` | Worker PID no longer alive but TTL hadn't expired yet. |
| `timed_out` | `{pid, elapsed_seconds, limit_seconds, sigkill}` | `max_runtime_seconds` exceeded; dispatcher SIGTERM'd (then SIGKILL'd after 5 s grace) and re-queued. |
| `stale` | `{elapsed_seconds, last_heartbeat_at, heartbeat_age_seconds, timeout_seconds, pid, terminated}` | Task ran longer than `kanban.dispatch_stale_timeout_seconds` (default 4 h) AND no `kanban_heartbeat` arrived in the last hour. Dispatcher SIGTERM'd the host-local worker (if any), reset the task to `ready` for re-dispatch. Does NOT tick the failure counter (stale is dispatcher-side absence detection, not a worker fault). Workers running long operations should call `kanban_heartbeat` at least once an hour to avoid this. |
| `respawn_guarded` | `{reason}` | Dispatcher refused to re-spawn this ready task this tick. Reasons: `blocker_auth` (last failure was a quota/auth/429 error — wait for the rate window to reset), `recent_success` (a completed run happened in the last hour — wait for review before re-running), `active_pr` (a GitHub PR URL appears in a recent comment — a prior worker already opened a PR). The task stays in `ready`; the next tick gets another chance to spawn. If the underlying condition persists, the normal `consecutive_failures` circuit breaker will auto-block via `gave_up` after `failure_limit` failures. |
| `spawn_failed` | `{error, failures}` | One spawn attempt failed (missing PATH, workspace unmountable, …). Counter increments; task returns to `ready` for retry. |
| `protocol_violation` | `{pid, claimer, exit_code}` | Worker exited successfully while the task was still `running`, usually because it answered without calling `kanban_complete` or `kanban_block`. The dispatcher also emits `gave_up` and auto-blocks immediately instead of retrying. |
| `gave_up` | `{failures, effective_limit, limit_source, error}` | Circuit breaker fired after N consecutive non-successful attempts. Task auto-blocks with the last error. The effective limit resolves as task `max_retries`, then dispatcher `failure_limit` / `kanban.failure_limit`, then the built-in default. |

`intellect kanban tail <id>` shows these for a single task. `intellect kanban watch` streams them board-wide.

## Out of scope

Kanban is deliberately single-host. `~/.intellect/kanban.db` is a local SQLite file and the dispatcher spawns workers on the same machine. Running a shared board across two hosts is not supported — there's no coordination primitive for "worker X on host A, worker Y on host B," and the crash-detection path assumes PIDs are host-local. If you need multi-host, run an independent board per host and use `delegate_task` / a message queue to bridge them.

## Design spec

The complete design — architecture, concurrency correctness, comparison with other systems, implementation plan, risks, open questions — lives in `docs/intellect-kanban-v1-spec.pdf`. Read that before filing any behavior-change PR.
