# Langfuse Observability Plugin

This plugin ships bundled with Intellect but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

```bash
pip install langfuse
intellect plugins enable observability/langfuse
```

Or check the box in the interactive `intellect plugins` UI.

## Required credentials

Set these in `~/.intellect/.env`:

```bash
intellect_LANGFUSE_PUBLIC_KEY=pk-lf-...
intellect_LANGFUSE_SECRET_KEY=sk-lf-...
intellect_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
intellect plugins list                 # observability/langfuse should show "enabled"
intellect chat -q "hello"              # then check Langfuse for a "Intellect turn" trace
```

## Optional tuning

```bash
intellect_LANGFUSE_ENV=production       # environment tag
intellect_LANGFUSE_RELEASE=v1.0.0       # release tag
intellect_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
intellect_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
intellect_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
intellect plugins disable observability/langfuse
```
