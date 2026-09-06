Generate videos with automatic scene chaining (start+end frame transitions).

Usage: `/gen-chain-videos <project_id> <video_id>`

This creates smooth transitions between scenes in a chain by using the **NEXT scene's image as the endImage** of the current scene's video, so the last frame of scene N matches the first frame of scene N+1 → seamless concat.

## Before you start: chaining is not on the new Flow API

Flow's `flow.google.com` payload has an aspect slot and a single source-image
slot; the **end-image slot was never captured**, so a start+end frame request
cannot be built. `GENERATE_VIDEO` with an `endImage` fails immediately with
`UNSUPPORTED_ON_BATCH_API` (terminal — it is not retried).

```bash
curl -s http://127.0.0.1:8100/api/flow/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('transport:', s['transport'], '| degraded fallback:', s['allow_degraded'])
"
```

Two honest options — tell the user which one you are taking:

1. **`FLOW_ALLOW_DEGRADED=1`** (restart the agent with it): each scene renders as
   plain i2v off its own start frame. The pipeline completes, but the cut
   between scenes is **not** seamless — the chain invariant below does not hold.
   Prefer `/fk-concat-fit-narrator` with a crossfade to hide the seams.
2. **Restore chaining properly** by capturing the end-image slot off the Flow
   UI — `docs/CAPTURE.md`. This is the only way to get the real behaviour back.

Everything below describes the intended behaviour, which is what option 2
restores.

## How chaining works

For any scene that has a CHILD in the chain (i.e. some other scene with `parent_scene_id == this.id`):

```
Scene N (chain head or middle): startImage = sceneN.image, endImage = sceneN+1.image
                                → video plays FROM sceneN.image TO sceneN+1.image (the next scene's image)

Scene N+1 (next in chain):      startImage = sceneN+1.image, endImage = sceneN+2.image (if N+2 exists)
                                → continues the chain

Last scene in chain (no child): startImage = lastScene.image, NO endImage
                                → plain frame_2_video, no chained transition
```

**Key invariant**: at the cut between two consecutive chain scenes, `sceneN.video.last_frame == sceneN+1.image == sceneN+1.video.first_frame` → **concat is seamless**.

The `endImage` is the **CHILD scene's image** (the next scene's image_media_id), NOT the parent's. Chain heads (ROOT scenes that have a child) DO need an endImage; only chain tails (last scene in chain) and standalone ROOT scenes leave `endImage` null.

## Step 1: Pre-check

```bash
# All scene images must be ready with UUID media_ids
curl -s "http://127.0.0.1:8100/api/scenes?video_id=<VID>"
```

ABORT if any scene is missing `${ori}_image_media_id` (UUID).

## Step 2: Set up end_scene_media_ids for chaining

For each scene that has a CHILD in the chain (i.e. some other scene's `parent_scene_id == this.id`), set its `${ori}_end_scene_media_id` to that **child** scene's `${ori}_image_media_id`:

```bash
curl -X PATCH http://127.0.0.1:8100/api/scenes/<SID> \
  -H "Content-Type: application/json" \
  -d '{"${ori}_end_scene_media_id": "<child_scene_image_media_id>"}'
```

Logic:
1. Sort scenes by `display_order`
2. Build a child map: `parent_id -> child_scene` (scan all scenes, key by `parent_scene_id`)
3. For each scene `S`:
   - If `S` has a child in the map → set `${ori}_end_scene_media_id = child.${ori}_image_media_id`
   - Else (no child = chain tail or standalone ROOT) → leave `${ori}_end_scene_media_id` null

**Affected scenes** (those that need an `end_scene_media_id`): chain heads (ROOT-with-child) AND chain middles (CONTINUATION-with-child). NOT chain tails, NOT standalone ROOTs.

**Common mistake**: setting `end_scene_media_id` to the PARENT's image. That makes scene N's video end at scene N-1's frame — when concatenated forward (N then N+1), the cut between N's last frame and N+1's first frame is hard, defeating the chain. The correct frame at the end of scene N's video is `sceneN+1.image`.

## Step 3: Submit ALL video requests at once

The server handles throttling automatically (max 5 concurrent, 10s cooldown). The worker reads `${ori}_end_scene_media_id` from each scene (set in Step 2) and passes it as `endImage` to the API. This triggers `start_end_frame_2_video` (i2v_fl) instead of plain `frame_2_video` (i2v).

```bash
curl -X POST http://127.0.0.1:8100/api/requests/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {"type": "GENERATE_VIDEO", "scene_id": "<SID1>", "project_id": "<PID>", "video_id": "<VID>", "orientation": "${ORI}"},
      {"type": "GENERATE_VIDEO", "scene_id": "<SID2>", "project_id": "<PID>", "video_id": "<VID>", "orientation": "${ORI}"}
    ]
  }'
```

Build the `requests` array from ALL scenes in display_order. Do NOT manually batch or loop.

Poll aggregate status every 30s until done:

```bash
curl -s "http://127.0.0.1:8100/api/requests/batch-status?video_id=<VID>&type=GENERATE_VIDEO"
# Wait for: "done": true
# If "all_succeeded": false → some failed, check individual failures
```

## Known Limitation: Concat Gap

**Problem:** When concatenating chained videos, the endImage frames of scene N overlap with the startImage frames of scene N+1 — both are the same image. This produces **10-16 static/duplicate frames (~0.4-0.7s)** at each cut point where nothing moves.

```
Scene 1 video: [action...] [endImage = scene2 still] ← 10-16 frames
Scene 2 video: [startImage = scene2 still] [action...] ← 10-16 frames
Concat result:  ...action → [~0.8-1.4s static gap] → action...
```

**Mitigations:**
- **Trim overlap** — use `trim_start` / `trim_end` on scenes to cut the static frames before concat. Typically trim 0.4-0.7s from the end of scene N and/or the start of scene N+1.
- **Don't overuse chaining** — only chain scenes that truly need smooth visual continuity (same location, continuous action). For scene changes (new location, time jump), use ROOT without endImage — a hard cut is more natural.
- **Mix techniques** — alternate between chained (CONTINUATION) and unchained (ROOT) scenes. Hard cuts between different locations feel intentional; gaps between continuous action feel broken.

**When to chain vs not:**

| Situation | Recommendation |
|-----------|---------------|
| Same location, continuous action | CONTINUATION (chain) |
| Location change, time jump | ROOT (hard cut — no gap) |
| Dramatic moment, reaction shot | INSERT (hard cut — intentional) |
| Dream/flashback transition | ROOT or R2V (stylistic break) |

## Step 4: Output

Print table:
| Scene | Order | Chain | endImage from | video_status | Duration |
|-------|-------|-------|---------------|-------------|----------|

Print: "Chained videos ready. Run /fk-concat <VID> to merge."
Remind: "Check concat gaps — trim 0.4-0.7s overlap at chain boundaries if needed."
