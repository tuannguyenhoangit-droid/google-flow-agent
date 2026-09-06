# fk-pipeline — Smart Full-Pipeline Orchestrator

Auto-detect project state and run the correct stages (continuation or full run). Handles batching, parallelism, retries, downloads, TTS, and Telegram notifications.

Usage: `/fk-pipeline [project_id] [orientation] [options]`

Options:
- `--upscale` — include 4K upscale stage. **Unavailable since Flow moved** — no upsampler rpc has been captured on `flow.google.com`, so every upscale fails `UNSUPPORTED_ON_BATCH_API` (terminal, not retried). Warn the user and run without it; the 1080p render is the deliverable. See `docs/CAPTURE.md`.
- `--tts` — include TTS narration stage (parallel with upscale)
- `--download` — auto-download 4K files as upscales complete
- `--concat` — run concat after all stages done
- `--notify` — send Telegram notifications at milestones
- `--interval N` — poll interval in seconds (default: 15)
- `--orientation H|V` — HORIZONTAL or VERTICAL (auto-detected from video.orientation if omitted)

Examples:
- `/fk-pipeline` — detect state and continue most recent project
- `/fk-pipeline --upscale --tts --download --notify` — full run with all options
- `/fk-pipeline <project_id> HORIZONTAL --upscale --download` — explicit project + orientation

---

## When to Use

- Starting a new project from scratch (after scenes are created)
- Resuming a partially-complete project
- Batch-running all remaining stages hands-free
- Want TTS + upscale to run simultaneously
- Want 4K downloads to roll in as each upscale completes

---

## Step 1: Resolve Project and State

### 1a. Fetch project

```bash
# Most recent project
curl -s http://127.0.0.1:8100/api/projects
# Use last item → {id, name}

# Get video + scenes
VID=$(curl -s "http://127.0.0.1:8100/api/videos?project_id=<PID>" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
curl -s "http://127.0.0.1:8100/api/scenes?video_id=$VID" > /tmp/fk_scenes.json
curl -s "http://127.0.0.1:8100/api/projects/<PID>/characters" > /tmp/fk_chars.json
curl -s "http://127.0.0.1:8100/api/projects/<PID>/output-dir" > /tmp/fk_outdir.json
```

### 1b. Derive slug and output dir

```python
import json

outdir_meta = json.load(open('/tmp/fk_outdir.json'))
SLUG = outdir_meta['slug']
OUTDIR = outdir_meta['path']
```

### 1c. State detection

```python
import json, glob as globmod, os

scenes = json.load(open('/tmp/fk_scenes.json'))
chars  = json.load(open('/tmp/fk_chars.json'))
PREFIX = video_orientation.lower()  # from GET /api/videos/{vid} → orientation field; 'horizontal' or 'vertical'
N      = len(scenes)

def pct(done, total):
    return f"{done}/{total}" if total else "0/0"

state = {
    # Stage 0: ref images
    'refs_total':   len(chars),
    'refs_done':    sum(1 for c in chars if c.get('media_id')),
    'refs_missing': [c['id'] for c in chars if not c.get('media_id')],

    # Stage 1: scene images
    'images_total':   N,
    'images_done':    sum(1 for s in scenes if s.get(f'{PREFIX}_image_status') == 'COMPLETED'),
    'images_pending': [s['id'] for s in scenes if s.get(f'{PREFIX}_image_status') != 'COMPLETED'],

    # Stage 2: videos
    'videos_total':   N,
    'videos_done':    sum(1 for s in scenes if s.get(f'{PREFIX}_video_status') == 'COMPLETED'),
    'videos_pending': [s['id'] for s in scenes if s.get(f'{PREFIX}_video_status') != 'COMPLETED'],

    # Stage 2.5: review
    'review_done':    False,  # set True after review passes or max cycles

    # Stage 3: upscale
    'upscale_total':   N,
    'upscale_done':    sum(1 for s in scenes if s.get(f'{PREFIX}_upscale_status') == 'COMPLETED'),
    'upscale_pending': [s['id'] for s in scenes
                        if s.get(f'{PREFIX}_upscale_status') not in ('COMPLETED', 'FAILED')],
    'upscale_failed':  [s['id'] for s in scenes if s.get(f'{PREFIX}_upscale_status') == 'FAILED'],

    # Stage 4: TTS
    'tts_total':  N,
    'tts_done':   len(globmod.glob(f'{OUTDIR}/tts/scene_*.wav')),
    'has_narrator': any(s.get('narrator_text') for s in scenes),

    # Stage 5: downloads
    'dl_total':  N,
    'dl_done':   len(globmod.glob(f'{OUTDIR}/4k/scene_*.mp4')),
}
```

### 1d. Print detected state and plan

```
Detected state for <project_name> [HORIZONTAL]:
  Refs:      5/5  ✓
  Images:    50/50 ✓
  Videos:    50/50 ✓
  Review:    passed (48/50 good, 2 fixed after regen)
  Upscale:   39/50  ← in progress
  TTS:       50/50 ✓
  Downloads: 39/50  ← behind upscale

Plan:
  [ACTIVE]  Continue upscale (11 remaining)
  [ACTIVE]  Download as upscales complete
  [SKIP]    TTS (already done)
  [QUEUED]  Concat after upscale+download complete
```

---

## Step 2: Stage Routing

Use this decision tree to determine which stages to run and in what order.

```python
stages_to_run = []

# Sequential gates — each stage blocks the next
if state['refs_done'] < state['refs_total']:
    stages_to_run.append('REFS')
elif state['images_done'] < state['images_total']:
    stages_to_run.append('IMAGES')
elif state['videos_done'] < state['videos_total']:
    stages_to_run.append('VIDEOS')

# Review (after videos done, before upscale)
if state['videos_done'] == state['videos_total'] and not state['review_done']:
    stages_to_run.append('REVIEW')

# Upscale (after review passes)
if upscale_flag and state['videos_done'] == state['videos_total'] and state['review_done']:
    if state['upscale_done'] < state['upscale_total']:
        stages_to_run.append('UPSCALE')

# TTS — parallel with upscale/videos (independent)
if tts_flag and state['has_narrator'] and state['tts_done'] < state['tts_total']:
    stages_to_run.append('TTS')  # runs concurrently

# Downloads — rolling alongside upscale
if download_flag and state['dl_done'] < state['dl_total']:
    stages_to_run.append('DOWNLOAD')  # runs concurrently with UPSCALE

# Concat — after all downloads + TTS done
if concat_flag:
    stages_to_run.append('CONCAT')  # runs last
```

**Parallelism rules:**
- `REFS` → `IMAGES` → `VIDEOS` → `REVIEW` are **sequential** (each requires previous)
- `UPSCALE` + `TTS` can run **simultaneously** (spawn 2 agents)
- `DOWNLOAD` runs **rolling** alongside UPSCALE (check each poll cycle)
- `CONCAT` runs **after** UPSCALE + DOWNLOAD + TTS all complete

---

## Step 3: Run Each Stage

### Stage 0 — Ref Images

Only run if any entity is missing `media_id`.

```bash
# For each entity missing media_id, submit GENERATE_CHARACTER_IMAGE
# Batch 5 at a time
for CID in <missing_ids>:
  curl -X POST http://127.0.0.1:8100/api/requests \
    -H "Content-Type: application/json" \
    -d '{"type":"GENERATE_CHARACTER_IMAGE","character_id":"<CID>","project_id":"<PID>"}'
```

**Poll until all entities have `media_id`** before moving to Stage 1.

**Retry rule:** If a ref gen fails, resubmit once. If fails again, warn user and skip.

---

### Stage 1 — Scene Images

Only run after all refs have `media_id`.

```bash
# For each scene with image_status != COMPLETED:
curl -X POST http://127.0.0.1:8100/api/requests \
  -H "Content-Type: application/json" \
  -d '{"type":"GENERATE_IMAGE","scene_id":"<SID>","project_id":"<PID>","video_id":"<VID>","orientation":"<ORIENTATION>"}'
```

Batch 5 at a time. Poll every 15s. Submit next batch when current batch completes.

**Failed images:** Resubmit once with `REGENERATE_IMAGE` (forces re-run).

---

### Stage 2 — Scene Videos

Only run after all scene images COMPLETED.

```bash
curl -X POST http://127.0.0.1:8100/api/requests \
  -H "Content-Type: application/json" \
  -d '{"type":"GENERATE_VIDEO","scene_id":"<SID>","project_id":"<PID>","video_id":"<VID>","orientation":"<ORIENTATION>"}'
```

Batch 5. Poll 15s. Each video takes 2-5 min.

---

### Stage 2.5 — Review Videos

Only run after all videos COMPLETED. Uses `/fk-review-video` to catch AI generation errors before upscaling.

```bash
# Run light review on all completed videos
curl -X POST "http://127.0.0.1:8100/api/videos/<VID>/review?project_id=<PID>&mode=light&orientation=<ORIENTATION>"
# Poll until complete
```

**Interpret results:**
- Scenes scoring **7.5+** (good/excellent) → pass, move to upscale
- Scenes scoring **4.0–7.4** (acceptable/poor) → update `video_prompt` based on `fix_guide` + `errors`, then regen video
- Scenes scoring **0–3.9** (unusable) → update `video_prompt` based on errors, regen image first (`REGENERATE_IMAGE`), then regen video

**Fix-and-regen loop (max 2 cycles per scene):**

```python
for cycle in range(2):
    review = run_review(VID, mode='light')
    bad_scenes = [s for s in review if s['total_score'] < 7.5]
    if not bad_scenes:
        break  # all pass

    for scene in bad_scenes:
        # Update video_prompt based on review errors + fix_guide
        # e.g. add "static camera" for camera drift, "no brand logos" for logo errors
        new_prompt = improve_prompt(scene['video_prompt'], scene['errors'], scene['fix_guide'])
        curl_patch(f"/api/scenes/{scene['scene_id']}", {"video_prompt": new_prompt})

        if scene['total_score'] < 4.0:
            # Unusable — regen image first (cascades video)
            submit_request("REGENERATE_IMAGE", scene['scene_id'])
        else:
            # Poor/acceptable — regen video only
            submit_request("GENERATE_VIDEO", scene['scene_id'])

    # Poll until all regens complete, then re-review
```

**After review passes (or max cycles exhausted):**
- Log scenes that still fail with their scores and errors
- Proceed to upscale with all scenes that scored 7.5+
- Report skipped scenes at end

---

### Stage 3 — Upscale (4K)

Only run after review passes (or max review cycles exhausted). TIER_TWO only.

```bash
curl -X POST http://127.0.0.1:8100/api/requests \
  -H "Content-Type: application/json" \
  -d '{"type":"UPSCALE_VIDEO","scene_id":"<SID>","project_id":"<PID>","video_id":"<VID>","orientation":"<ORIENTATION>"}'
```

Batch 5. **Resubmit failed upscales** once automatically.

---

### Stage 4 — TTS Narration (parallel)

Runs in parallel with Stage 2 or 3. Requires `narrator_text` on scenes and a voice template.

```bash
# Check templates
curl -s http://127.0.0.1:8100/api/tts/templates
# Pick template name (e.g. vi_male_narrator)

# Trigger narration for video
curl -X POST http://127.0.0.1:8100/api/videos/<VID>/narrate \
  -H "Content-Type: application/json" \
  -d '{"template":"<template_name>"}'
```

Poll `output/<slug>/tts/` for WAV files as they appear.

**If no template exists:** Pause and instruct user to run `/fk-gen-tts-template` or `/fk-import-voice` first.

---

### Stage 5 — Rolling Downloads (parallel with upscale)

Each poll cycle, check for newly completed upscales not yet downloaded:

```python
newly_completed = [
    s for s in scenes
    if s.get(f'{PREFIX}_upscale_status') == 'COMPLETED'
    and s.get(f'{PREFIX}_upscale_url')
    and not os.path.exists(f"{OUTDIR}/4k/scene_{s['display_order']:03d}_{s['id']}.mp4")
]
```

For each, write URL to temp file and download via curl:

```bash
URL=$(cat /tmp/url_scene_<order>.txt)
curl -s "$URL" -o "output/<slug>/4k/scene_<order:03d>_<scene_id>.mp4"
```

**Important:** Write URL to temp file before passing to curl — avoids shell encoding mangling GCS signatures.

Verify each download:
```bash
ffprobe -v quiet -show_entries format=duration -of csv=p=0 "<file>"
# Must be > 0 seconds
```

---

### Stage 6 — Concat

Run after UPSCALE + DOWNLOAD + TTS all complete. Delegates to `/fk-concat`.

Invoke: `/fk-concat --4k --with-tts` (or appropriate flags based on what was run).

---

## Step 4: Poll Loop

```python
import time

INTERVAL = 15
cycle = 0

while stages_to_run:
    cycle += 1
    print(f"[cycle {cycle}] Polling...")

    # Refresh scene data
    scenes = fetch_scenes(VID)
    state = compute_state(scenes, chars, OUTDIR, PREFIX)

    # Submit next batch for active sequential stage
    active_stage = stages_to_run[0]  # HEAD of sequential chain
    if active_stage in ('REFS','IMAGES','VIDEOS','UPSCALE'):
        submit_next_batch(active_stage, state, PID, VID, ORIENTATION)

    # Check if active stage completed
    if stage_complete(active_stage, state):
        stages_to_run.pop(0)
        print(f"✅ {active_stage} complete")
        if notify_flag:
            send_telegram(f"✅ {active_stage} complete for {project_name}")

    # Parallel: Rolling downloads (every cycle when upscaling)
    if 'DOWNLOAD' in stages_to_run:
        download_newly_completed(scenes, state, OUTDIR, PREFIX, PID=PID, VID=VID)
        if state['dl_done'] >= state['upscale_done']:
            pass  # downloads caught up — keep watching

    # Check if all done
    remaining_sequential = [s for s in stages_to_run if s not in ('TTS','DOWNLOAD','CONCAT')]
    if not remaining_sequential and state['dl_done'] == state['upscale_done']:
        if 'CONCAT' in stages_to_run:
            run_concat()
        break

    time.sleep(INTERVAL)
```

---

## Step 5: Failure Handling

| Failure type | Detection | Action |
|---|---|---|
| Ref image FAILED | `media_id` missing after request COMPLETED | Resubmit `GENERATE_CHARACTER_IMAGE` once |
| Scene image FAILED | `horizontal_image_status == FAILED` | Resubmit `REGENERATE_IMAGE` once |
| Video FAILED | `horizontal_video_status == FAILED` | Resubmit `GENERATE_VIDEO` once |
| Review FAILED (score < 7.5) | `total_score < 7.5` in review results | Update `video_prompt` from `fix_guide` + `errors`, regen video (max 2 cycles) |
| Review UNUSABLE (score < 4.0) | `total_score < 4.0` in review results | Update `video_prompt`, regen image first (`REGENERATE_IMAGE`), then video |
| Upscale FAILED | `horizontal_upscale_status == FAILED` | Resubmit `UPSCALE_VIDEO` once |
| Download 4KB (XML error) | `ffprobe` returns 0s or non-numeric | Re-download (URL still valid for ~8h) |
| Worker stalled | pending > 0, processing = 0 for 2+ min | Print warning; suggest server restart |
| TTS no template | `GET /api/tts/templates` returns empty | Pause; prompt user to create template |

**Max retries:** 2 per scene per stage. After 2 failures, log and skip — report at end.

---

## Step 6: Final Summary

```
Pipeline complete for <project_name>
  Refs:      5/5
  Images:    50/50
  Videos:    50/50
  Review:    passed (48 good, 2 fixed after regen)
  Upscale:   50/50  (1 retry)
  Downloads: 50/50
  TTS:       50/50
  Failed:    0

Ready for: /fk-concat --4k --with-tts
```

Send via Telegram if `--notify`.

---

## Concurrent TTS + Upscale Pattern

When both `--tts` and `--upscale` are requested and videos are done, spawn two parallel tracks:

```
Track A (main agent):   UPSCALE → batch 5 → poll → batch 5 → ...
                        └─ DOWNLOAD: rolling on each poll cycle

Track B (sub-agent):    TTS → POST /api/videos/<VID>/narrate → poll tts/ dir
```

Use the Agent tool with `oh-my-claudecode:executor` for Track B:

```
Agent(
  subagent_type="oh-my-claudecode:executor",
  prompt="Run TTS narration for video <VID> using template <template>.
          Poll output/<slug>/tts/ every 30s until all <N> WAV files appear.
          Report done when complete."
)
```

Main agent handles upscale + downloads. Both finish independently.

---

## Console Output Format

```
[fk-pipeline] Operation Absolute Resolve - Cinematic Edition [HORIZONTAL]
Stage:     UPSCALE (39/50) + DOWNLOAD (39/50) rolling
Queue:     5 pending / 5 processing
TTS:       50/50 ✓ (parallel, done)
Cycle 12 / next poll in 15s...
```

---

## Common Issues

| Issue | Fix |
|-------|-----|
| `extension_connected: false` | Chrome extension disconnected — reload extension |
| Stage stuck at 0/N | Check `/api/requests?status=FAILED` for errors |
| Upscale TIER_ONE error | Account is TIER_ONE — skip `--upscale` |
| Downloads 4KB XML error | Write URL to temp file, re-curl |
| TTS `torch not found` | Set `TTS_PYTHON_BIN=/opt/homebrew/bin/python3.10` in server env |
| Worker stalled | POST http://127.0.0.1:8100/api/worker/restart or restart server |

---

## Next Steps

| After pipeline | Skill |
|---|---|
| Mix TTS into videos | `/fk-concat --with-tts` |
| Add branding watermark | `/fk-brand-logo` |
| Generate YouTube SEO | `/fk-youtube-seo` |
| Upload to YouTube | `/fk-youtube-upload` |
