# fk-change-model — View & Change Video/Image Model Keys

View or switch the AI models used for video generation, image generation, and upscaling.

Usage:
- `/fk-change-model` — show current model config
- `/fk-change-model list` — show current model config
- `/fk-change-model video <model_key>` — change video model for current tier
- `/fk-change-model image <model_key>` — change image model
- `/fk-change-model upscale <model_key>` — change upscale model

---

## What the model keys mean on the current Flow API

Since Flow moved to `flow.google.com`, aspect ratio is its own payload slot and
the REST-era suffixed names (`…_portrait`, `…_fl`, `…_relaxed`) are **rejected
outright**. `models.json` still stores the old [tier][gen_type][aspect] map for
the legacy path, and `resolve_video_model()` folds whatever it finds onto the
three names the new path accepts:

| Wire name | What it is |
|---|---|
| `veo_3_1_i2v_s_fast_ultra` | anything whose key mentions `ultra` |
| `veo_3_1_i2v_lite` | anything whose key mentions `lite` |
| `veo_3_1_i2v_lite_low_priority` | the default, and anything unrecognised |

So changing a Landscape vs Portrait key has **no effect** on the batch path —
only the quality tier survives the fold. `r2v` and `start_end` keys are moot
there too: both are unported.

Image models are unaffected: `GEM_PIX_2` (Nano Banana Pro) and `NARWHAL`
(Banana 2) are both accepted, and `default_image_model` in `models.json` picks
which nickname is used.

## Step 1: Show Current Models

```bash
curl -s http://127.0.0.1:8100/api/models | python3 -m json.tool
```

Display in a readable table:

### Video Models

| Tier | Gen Type | Landscape | Portrait |
|------|----------|-----------|----------|
| TIER_TWO | i2v (frame_2_video) | `veo_3_1_i2v_s_fast_ultra` | `veo_3_1_i2v_s_fast_portrait_ultra` |
| TIER_TWO | i2v chained (start_end) | `veo_3_1_i2v_s_fast_ultra_fl` | `veo_3_1_i2v_s_fast_portrait_ultra_fl` |
| TIER_TWO | r2v (reference) | `veo_3_0_r2v_fast_ultra` | `veo_3_0_r2v_fast_portrait_ultra` |
| TIER_ONE | i2v (frame_2_video) | `veo_3_1_i2v_s_fast` | `veo_3_1_i2v_s_fast_portrait` |
| TIER_ONE | i2v chained (start_end) | `veo_3_1_i2v_s_fast_fl` | `veo_3_1_i2v_s_fast_portrait_fl` |
| TIER_ONE | r2v (reference) | `veo_3_1_r2v_fast` | `veo_3_1_r2v_fast_portrait` |

### Image Models

| Key | Model |
|-----|-------|
| NANO_BANANA_PRO | `GEM_PIX_2` |
| NANO_BANANA_2 | `NARWHAL` |

### Upscale Models

| Resolution | Model |
|------------|-------|
| 4K | `veo_3_1_upsampler_4k` |
| 1080p | `veo_3_1_upsampler_1080p` |

## Step 2: Quick Select (Interactive)

If no specific model key was provided as argument, present an `AskUserQuestion` selector with presets:

**For video model:**
Use `AskUserQuestion` with options:
- label: "VEO 3.1 Ultra (Recommended)", description: "Default high quality · fast queue · current default"
- label: "VEO 3.1 Lite", description: "Lower quality · fastest · no r2v support"
- label: "VEO 3.1 Low Priority", description: "Same ultra quality · slower queue · saves credits"

After user picks, apply the matching preset from the Quick Switch Presets section below.

**For image model:**
Use `AskUserQuestion` with options:
- label: "GEM_PIX_2 (Recommended)", description: "Gemini Pix 2 · current default"
- label: "NARWHAL", description: "Alternative image model"

## Step 3: Change a Model (Manual)

### Change video model (all orientations for a tier + gen type)

```bash
# Example: switch TIER_TWO i2v to a different model
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{
    "video_models": {
      "PAYGATE_TIER_TWO": {
        "frame_2_video": {
          "VIDEO_ASPECT_RATIO_LANDSCAPE": "new_model_key_landscape",
          "VIDEO_ASPECT_RATIO_PORTRAIT": "new_model_key_portrait"
        }
      }
    }
  }'
```

### Change a single orientation

```bash
# Example: change only portrait video model for TIER_TWO i2v
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{
    "video_models": {
      "PAYGATE_TIER_TWO": {
        "frame_2_video": {
          "VIDEO_ASPECT_RATIO_PORTRAIT": "new_model_key"
        }
      }
    }
  }'
```

### Change image model

```bash
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{
    "image_models": {
      "NANO_BANANA_PRO": "NEW_IMAGE_MODEL"
    }
  }'
```

### Change upscale model

```bash
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{
    "upscale_models": {
      "VIDEO_RESOLUTION_4K": "new_upscaler_model"
    }
  }'
```

## Step 4: Verify

After changing, verify the update took effect:

```bash
curl -s http://127.0.0.1:8100/api/models | python3 -m json.tool
```

Changes are **hot-reloaded** — no server restart needed. The new model keys are used immediately for all subsequent requests.

---

## Known Model Keys

These are model keys observed on Google Flow (may change as Google updates):

### Video (Veo family)
| Key | Description |
|-----|-------------|
| Key | Description | Quality | Speed |
|-----|-------------|---------|-------|
| `veo_3_1_i2v_lite` | **VEO 3.1 Lite** — lightweight, fast | Lower | Fastest |
| `veo_3_1_i2v_s_fast` | Veo 3.1 i2v, TIER_ONE | Standard | Fast |
| `veo_3_1_i2v_s_fast_portrait` | Veo 3.1 i2v portrait, TIER_ONE | Standard | Fast |
| `veo_3_1_i2v_s_fast_ultra` | Veo 3.1 i2v, TIER_TWO (ultra) | High | Fast |
| `veo_3_1_i2v_s_fast_portrait_ultra` | Veo 3.1 i2v portrait, TIER_TWO | High | Fast |
| `veo_3_1_i2v_s_fast_ultra_relaxed` | **VEO 3.1 Low Priority** — same ultra quality, slower queue | High | Slow |
| `veo_3_1_i2v_s_fast_fl` | Veo 3.1 i2v first+last frame, TIER_ONE | Standard | Fast |
| `veo_3_1_i2v_s_fast_portrait_fl` | Veo 3.1 i2v portrait first+last, TIER_ONE | Standard | Fast |
| `veo_3_1_i2v_s_fast_ultra_fl` | Veo 3.1 i2v first+last, TIER_TWO | High | Fast |
| `veo_3_1_i2v_s_fast_portrait_ultra_fl` | Veo 3.1 i2v portrait first+last, TIER_TWO | High | Fast |
| `veo_3_0_r2v_fast_ultra` | Veo 3.0 reference-to-video, TIER_TWO | High | Fast |
| `veo_3_0_r2v_fast_portrait_ultra` | Veo 3.0 r2v portrait, TIER_TWO | High | Fast |
| `veo_3_1_r2v_fast_landscape_ultra_relaxed` | **VEO 3.1 r2v Low Priority** — slower queue | High | Slow |
| `veo_3_1_r2v_fast` | Veo 3.1 r2v, TIER_ONE | Standard | Fast |
| `veo_3_1_r2v_fast_portrait` | Veo 3.1 r2v portrait, TIER_ONE | Standard | Fast |

### Model Compatibility

| Model | i2v | i2v chained (fl) | r2v | Notes |
|-------|-----|-------------------|-----|-------|
| VEO 3.1 Lite | Yes | ? | **No** | No reference-to-video support |
| VEO 3.1 Standard | Yes | Yes | Yes | TIER_ONE default |
| VEO 3.1 Ultra | Yes | Yes | Yes | TIER_TWO default |
| VEO 3.1 Low Priority | Yes | ? | Yes | Same ultra quality, slower queue, uses less credits |

### Quick Switch Presets

**Switch to VEO 3.1 Lite (fast, no r2v):**
```bash
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{"video_models":{"PAYGATE_TIER_TWO":{"frame_2_video":{"VIDEO_ASPECT_RATIO_LANDSCAPE":"veo_3_1_i2v_lite","VIDEO_ASPECT_RATIO_PORTRAIT":"veo_3_1_i2v_lite"}}}}'
```

**Switch to VEO 3.1 Low Priority (ultra quality, slower queue):**
```bash
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{"video_models":{"PAYGATE_TIER_TWO":{"frame_2_video":{"VIDEO_ASPECT_RATIO_LANDSCAPE":"veo_3_1_i2v_s_fast_ultra_relaxed","VIDEO_ASPECT_RATIO_PORTRAIT":"veo_3_1_i2v_s_fast_ultra_relaxed"},"reference_frame_2_video":{"VIDEO_ASPECT_RATIO_LANDSCAPE":"veo_3_1_r2v_fast_landscape_ultra_relaxed","VIDEO_ASPECT_RATIO_PORTRAIT":"veo_3_1_r2v_fast_landscape_ultra_relaxed"}}}}'
```

**Switch back to default VEO 3.1 Ultra:**
```bash
curl -s -X PATCH http://127.0.0.1:8100/api/models \
  -H "Content-Type: application/json" \
  -d '{"video_models":{"PAYGATE_TIER_TWO":{"frame_2_video":{"VIDEO_ASPECT_RATIO_LANDSCAPE":"veo_3_1_i2v_s_fast_ultra","VIDEO_ASPECT_RATIO_PORTRAIT":"veo_3_1_i2v_s_fast_portrait_ultra"},"reference_frame_2_video":{"VIDEO_ASPECT_RATIO_LANDSCAPE":"veo_3_0_r2v_fast_ultra","VIDEO_ASPECT_RATIO_PORTRAIT":"veo_3_0_r2v_fast_portrait_ultra"}}}}'
```

### Image
| Key | Description |
|-----|-------------|
| `GEM_PIX_2` | Gemini Pix 2 (current default) |
| `NARWHAL` | Narwhal model |

### Upscale
| Key | Description |
|-----|-------------|
| `veo_3_1_upsampler_4k` | 4K upscaler |
| `veo_3_1_upsampler_1080p` | 1080p upscaler |

## Notes

- Model keys are **case-sensitive** — use exact strings
- Changes persist to `agent/models.json` and survive server restarts
- Hot-reload updates the in-memory config immediately
- If you set an invalid model key, the API will return errors on the next generation request — revert to a known good key
