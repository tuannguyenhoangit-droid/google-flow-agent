# Migrated Image API

Flow's September 2026 frontend exposes image generation through the
`flow.google.com` batchexecute transport. This document covers the image
capabilities currently wired by Flow Kit.

## Generate images

`POST /api/flow/generate-image`

```json
{
  "prompt": "A red paper boat on a calm pond",
  "project_id": "<flow-project-uuid>",
  "image_model": "HARBOR_SEAL",
  "aspect_ratio": "16:9",
  "count": 2,
  "seed": 12345,
  "reference_media_ids": []
}
```

Current Flow UI model ids observed on the migrated frontend:

- `GEM_PIX_2` — Nano Banana Pro
- `NARWHAL` — Nano Banana 2
- `HARBOR_SEAL` — Nano Banana 2 Lite

Friendly aliases from `models.json` continue to work. Flow Kit also passes a
syntactically valid future wire model id through unchanged instead of silently
coercing it to the default, so a newly exposed model can be selected before the
next Flow Kit release once its id is known.

`count` accepts 1-4, matching the Flow UI. Flow itself implements x2/x3/x4
as independent single-image `ogiZ0b` RPCs, each with a single-use reCAPTCHA;
it also staggers x4 launches at roughly 0.0 / 0.5 / 1.5 / 2.5 seconds. FlowKit
mirrors that behavior instead of sending an unofficial multi-item burst inside
one RPC. Each variant keeps its own seed; when `seed` is supplied, later
variants use a deterministic stride.

FlowKit treats only image RPC `[8]` as transient. It waits for the entire first
variant wave to settle, cools down for 34 seconds, then retries only failed `[8]`
variants once with fresh request UUIDs and the same seed. This is a FlowKit
resilience policy; live UI capture did not show the same automatic retry. If a
multi-image request still has a failed variant after retry, already successful
images are preserved and the response reports `complete=false`,
`generated_count`, and `failed_variants` instead of discarding the whole batch.

## Aspect ratios

All five current image ratios are supported, using either the friendly ratio or
wire enum:

| Ratio | Wire name |
|---|---|
| `1:1` | `IMAGE_ASPECT_RATIO_SQUARE` |
| `9:16` | `IMAGE_ASPECT_RATIO_PORTRAIT` |
| `16:9` | `IMAGE_ASPECT_RATIO_LANDSCAPE` |
| `3:4` | `IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR` |
| `4:3` | `IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE` |

The older Flow Kit spelling `IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE` remains an
alias for compatibility.

## Edit an image

`POST /api/flow/edit-image`

The source image is sent as Flow's `BASE_IMAGE` input (wire type 2). Additional
`reference_media_ids` remain reference inputs (wire type 1). This fixes the old
batch-path behavior where the source itself was only a generic reference and
therefore conditioned a fresh generation instead of performing a true edit.

```json
{
  "prompt": "Make the paper boat blue",
  "source_media_id": "<media-id>",
  "project_id": "<flow-project-uuid>",
  "image_model": "GEM_PIX_2",
  "aspect_ratio": "16:9",
  "count": 1
}
```

## Export / upscale image

`POST /api/flow/export-image`

```json
{
  "media_id": "<generated-media-id>",
  "project_id": "<flow-project-uuid>",
  "quality": "2k"
}
```

The migrated frontend uses RPC `SPrCad` (`FlowService.UpsampleImage`). The call
is synchronous and returns the encoded JPEG, which the HTTP endpoint returns as
a downloadable image.

- `2k` — standard high-resolution download; live verified
- `4k` — same RPC with target code 2; availability is account/plan-gated

Live verification on the current Flow frontend produced a 2752x1536 JPEG from a
1376x768 source.
