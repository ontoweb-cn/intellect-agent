---
sidebar_position: 20
title: Web Search Providers
---

# Web Search Providers

Intellect routes `web_search` / `web_extract` through pluggable providers
(brave-free, ddgs, searxng, exa, parallel, tavily, firecrawl), configured
via `intellect tools` or the `web.*` config keys.

## Keyless (anonymous) search — opt-in

```yaml
web:
  keyless_fallback: false   # default — opt in explicitly
  provider_tier:
    tavily: free            # free (force keyless) / paid / auto
```

**Privacy note (deliberate difference from Hermes):** Hermes enables
keyless fallback by default — user queries flow to anonymous third-party
endpoints without asking. Intellect defaults this **off**: with the
default, your queries never reach an anonymous endpoint. When enabled, a
failed primary search makes exactly ONE anonymous attempt, marked
`rescued_from`, never cached.
