---
sidebar_position: 7
title: Pets
---

# Pets

Desktop pets (V1): profile-scoped pet store fed by the petdex.dev
gallery, a `pets` CLI, and a unicode terminal renderer. Pure display —
zero impact on prompts, toolsets, or prompt caching.

## Commands

```bash
intellect pets list              # installed pets
intellect pets install <slug>    # install from the petdex gallery
intellect pets select <slug>     # select + enable display.pet
intellect pets doctor            # config/store/manifest health
```

Selection writes `display.pet.{slug,enabled}` in config.yaml.

## V1 boundaries

- Rendering uses the unicode half-block truecolor fallback (deterministic
  procedural pet). Sprite-sheet (webp) decoding, the kitty/iTerm2
  graphics protocols, and the TUI petSprite component are deferred.
- The gallery manifest is fetched from petdex.dev only (host-pinned
  against SSRF; foreign asset hosts are rejected). When the network is
  unreachable the manifest degrades to your installed pets
  (本地清单降级).
- A doctor check (`intellect pets doctor`) validates the config, store,
  and manifest state.
