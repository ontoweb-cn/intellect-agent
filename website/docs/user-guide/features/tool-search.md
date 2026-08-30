---
title: Tool Search
sidebar_position: 95
---

# Tool Search

When you have many MCP servers or non-core plugin tools attached to a
session, their JSON schemas can consume a substantial fraction of the
context window on every turn — even when only a few of them are relevant
to what the user actually asked for.

**Tool Search** is Intellect' progressive-disclosure layer for that
problem. When activated, MCP and plugin tools are replaced in the
model-visible tools array by three bridge tools, and the model loads each
specific tool's schema on demand.

:::info Built-in Intellect tools never defer
The tools that make up Intellect' core capability set (`terminal`,
`read_file`, `write_file`, `patch`, `search_files`, `todo`, `memory`,
`browser_*`, `web_search`, `web_extract`, `clarify`, `execute_code`,
`delegate_task`, `session_search`, `send_message`, and the rest of
`_INTELLECT_CORE_TOOLS`) are *always* loaded directly. Only MCP tools and
non-core plugin tools are eligible for deferral.
:::

## How it works

When Tool Search activates, the model sees three new tools in place of the
deferred ones:

```
tool_search(queries: [], limit?)   — search the deferred-tool catalog
tool_describe(names: [])           — load full schemas for tools by name
tool_call(name, arguments)         — invoke a deferred tool
```

`tool_search` accepts a *list* of queries searched in parallel against the
same catalog, and `tool_describe` accepts a *list* of names batched into one
call. A bare string is accepted for either list (treated as a one-element
list). A typical interaction looks like:

```
Model: tool_search(["create a github issue", "list pull requests"])
  → { queries: [...], total_available: N,
      results: [ { query: "create a github issue", matches: ["mcp_github_create_issue", ...] }, ... ],
      tools: { "mcp_github_create_issue": { source, source_name, description, required }, ... } }
Model: tool_describe(["mcp_github_create_issue", "mcp_github_list_pull_requests"])
  → { tools: { "mcp_github_create_issue": { parameters: { ... } }, ... }, not_found?: [...] }
Model: tool_call("mcp_github_create_issue", { title: "...", body: "..." })
  → { ok: true, issue_number: 42 }
```

When the model invokes `tool_call`, Intellect **unwraps the bridge** and
dispatches the underlying tool exactly as if the model had called it
directly. Pre-tool-call hooks, guardrails, approval prompts, and
post-tool-call hooks all run against the real tool name — not against
`tool_call`. The activity feed in the CLI and gateway also unwraps so you
see the underlying tool, not the bridge.

Before an unwrapped call dispatches, Intellect runs a **blind-call probe**
(`validate_deferred_call_args`): if the model omitted a schema-`required`
argument, the tool is *not* invoked — the call returns the tool's parameter
schema plus a hint so the model repairs the call in one round-trip, instead
of crashing opaquely deep inside the tool.

## When does it activate?

Activation is **always-on when there is anything to defer**. `enabled: auto`
is simply an alias of `enabled: on` (matching Hermes): as soon as at least
one MCP / non-core plugin tool is present, the bridge is swapped in and the
deferred schemas are removed from the model-visible array.

What scales with your toolset is not the *activation decision* but the
**catalog listing** (below). A pure-core session still pays zero overhead —
with no deferrable tools the assembly is a pass-through.

## Catalog listing (progressive disclosure)

To keep deferred capabilities *discoverable* — the model can't search for a
tool it doesn't know exists — a grouped manifest of the deferred catalog is
embedded in the `tool_search` bridge description, skills-listing style:

```
github tools (44):
- create_issue: Open a new issue in a GitHub repository.
- merge_pull_request: Merge an open pull request.
```

The listing is **budgeted** to `min(listing_max_tokens, threshold_pct% of
context)` and degrades deterministically as the catalog grows:

| Form | What the model sees |
| --- | --- |
| `full` | per-tool `name: short description`, grouped by server |
| `names` | per-tool names only, grouped by server |
| `mixed` | small servers keep per-tool rows; oversized servers collapse to `server (N tools — names not listed)` |
| `groups` | one summary line per server (`server (N tools)`) — search is mandatory for discovery |
| `none` | bare bridge, no listing |

Degradation is **per server**, not global: one giant server (thousands of
flat tools) never costs a small co-attached server its listing. Ordering is
deterministic, so the rendered bytes are stable across turns and keep the
request prefix cacheable. `listing: off` always yields a bare bridge.

## Configuration

```yaml
tools:
  tool_search:
    enabled: auto        # auto (default), on, or off
    threshold_pct: 5     # listing budget percent of context
    listing: auto        # auto (default), on, or off
    listing_max_tokens: 4000
    search_default_limit: 5
    max_search_limit: 25
```

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `auto` | `auto` is an alias of `on` (常开): activate whenever there is at least one deferrable tool; `off` disables entirely. |
| `threshold_pct` | `5` | Listing budget as a percentage of context (capped by `listing_max_tokens`). No longer gates activation. Range 0–100. |
| `listing` | `auto` | Embed the catalog manifest in `tool_search`? `auto`/`on` include it when it fits the budget (degrading as above); `off` always gives a bare bridge. |
| `listing_max_tokens` | `4000` | Absolute cap on the embedded listing, regardless of context size. Range 200–60000. |
| `search_default_limit` | `5` | Matches returned per query when the model calls `tool_search` without a `limit`. |
| `max_search_limit` | `25` | Hard upper bound the model can request per query via `limit`. Range 1–50. |

You can also flip the legacy boolean shape:

```yaml
tools:
  tool_search: true   # equivalent to {enabled: auto}
```

The legacy `enabled: auto` semantics changed in the W15 release: `auto` no
longer threshold-gates activation — it is always-on like `on`. See the
changelog for the migration note.

## When NOT to use it

Tool Search trades a fixed per-turn token cost (the three bridge tool
schemas, ~300 tokens, plus the embedded catalog listing when it fits) and
at least one extra round trip (search → describe → call) for the savings on
the deferred schemas. It's a clear win when you have many tools and use few
per turn; it's overhead when you have few tools total.

The listing budget keeps that overhead bounded — even a 500-tool catalog
embeds well under 4,500 tokens. If you want the smallest possible per-turn
footprint, set `listing: off` (bare bridge, no manifest) or `enabled: off`
to disable the whole layer.

## Trade-offs that don't go away

These come from the prompt-cache integrity invariant — they are inherent
to any progressive-disclosure design, not specific to this implementation:

- **One extra round trip on cold tools.** The first time the model needs
  a deferred tool, it spends one or two extra model calls to find and
  load the schema. The token savings on the static side are real, but a
  portion is paid back at runtime.
- **No cache benefit on deferred schemas.** A loaded `tool_describe`
  result enters the conversation history (so it does get cached on
  subsequent turns) but it never benefits from the system-prompt cache
  prefix.
- **Model-quality dependence.** Tool Search assumes the model can write a
  reasonable search query for the tool it wants. Smaller models do this
  less well; the published Anthropic numbers (49% → 74% on Opus 4 with
  vs. without tool search) show the upside but also that ~26 points of
  accuracy is still retrieval failure.
- **Toolset edits invalidate cache.** Adding or removing a tool mid-
  session changes the bridge tools' descriptions (which include the
  deferred count and the embedded listing) and the catalog, so the prompt
  cache is invalidated. This is the same trade-off as any toolset edit.

## Implementation details

- **Retrieval:** BM25 over tokenized tool name + source label + description
  + parameter names, with an exact-name match scoring infinitely. The
  `mcp__` name prefix is stripped (so the shared `mcp` token can't drown
  ranking with near-zero IDF), and the source label is indexed so a
  service-name query ("linear") reaches that server's tools even when the
  tool names omit the vendor. Falls back to a literal substring match on
  the tool name when BM25 returns no positive-score hits. Optional Snowball
  English stemming unifies plural/verb forms when `snowballstemmer` is
  installed (plain tokenization otherwise).
- **Empty search results** attach an `available_sources` summary + hint so
  a lexical miss is not mistaken for a missing capability.
- **Catalog is stateless across turns.** It rebuilds from the current
  tool-defs list every assembly — no session-keyed `Map`. This avoids
  the class of bug where a stored catalog drifts out of sync with the
  live tool registry.
- **The catalog is scoped to the session's toolsets.** `tool_search`,
  `tool_describe`, and `tool_call` only ever see and invoke tools the
  session was actually granted. A subagent, kanban worker, or gateway
  session restricted to a subset of toolsets cannot use the bridge to
  discover or call a tool outside that subset.
- **Parallel admission is bridge-aware.** The batch planner peels
  `tool_call` to its underlying tool before deciding concurrency, so a
  server opted into `supports_parallel_tool_calls: true` keeps its
  concurrency behind the bridge, and `tool_search`/`tool_describe`
  lookups run in parallel. Malformed bridge calls stay a sequential
  barrier, and a bridged attempt at a core tool never gains concurrency.
- **No JS sandbox.** Intellect uses the simpler "structured tools" mode
  (search / describe / call as plain functions). The JS-sandbox "code
  mode" some other implementations offer is a large surface area; we
  skip it.

## See also

- `tools/tool_search.py` — the implementation
- `tests/tools/test_tool_search.py` and `tests/tools/test_deferral_fixes.py`
  — the regression suites
- The `openclaw-tool-search-report` PDF in the original implementation
  PR for the research that shaped the design
