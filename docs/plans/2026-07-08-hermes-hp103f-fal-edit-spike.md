# HP-103f — FAL Image Edit Spike

> Date: 2026-07-08 | Status: ✅ implemented

## Endpoints

| Base model (`image_gen.model`) | Edit endpoint | Required inputs |
|-------------------------------|---------------|-----------------|
| `fal-ai/gpt-image-1.5` | `fal-ai/gpt-image-1.5/edit` | `prompt`, `image_urls[]` |
| `fal-ai/gpt-image-2` | `fal-ai/gpt-image-2/edit` | `prompt`, `image_urls[]` |

Optional: `image_size`, `quality`, `input_fidelity`, `output_format`, `num_images`.

## Local file → FAL URL

Use `fal_client.upload_file(local_path)` before submit. Reuses existing
`_submit_fal_request()` (direct FAL_KEY or managed OntoWeb gateway).

## Response

Same as text-to-image: `{ "images": [ { "url": "..." } ] }`.

## Non-goals (Phase 1 parity)

- URL source images (local path only)
- Mask/inpaint (`mask_image_url`)
- FLUX img2img models (only GPT Image edit endpoints in v1)
