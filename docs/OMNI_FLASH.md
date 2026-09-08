# Gemini Omni Flash: integration guide for agents

FlowKit exposes Gemini Omni Flash video generation through the authenticated Google Flow session in its persistent Chrome profile. An integrating service talks only to the FlowKit REST API; it must not call Google Flow endpoints or the extension WebSocket directly.

## Prerequisites and base URL

Before submitting work, both checks must pass:

```bash
curl -fsS "$FLOWKIT_BASE_URL/health"
curl -fsS "$FLOWKIT_BASE_URL/api/flow/status"
```

Expected state on the migrated Flow transport:

```json
{"status":"ok","extension_connected":true}
{"connected":true,"transport":"batch"}
```

Use `http://127.0.0.1:8100` when the caller runs on the FlowKit host. For a remote integration, set `FLOWKIT_BASE_URL` to the protected HTTPS reverse-proxy URL and allow only the required source IPs or private network. Do not expose Chrome, VNC/noVNC, the extension WebSocket, or port 8100 publicly.

## Supported modes

On `flow.google.com`, Omni **text-to-video** is migrated and live-verified. The older frame/reference implementations still use the pre-migration REST transport and remain fail-fast while `USE_BATCH_RPC=1`.

| Mode | Batch status | Endpoint | Internal model family |
|---|---|---|---|
| Text to video | **supported** | `POST /api/flow/generate-video-omni-text` | `abra_t2v_<duration>s` |
| First frame to video | not yet ported | `POST /api/flow/generate-video` | `abra_i2v_<duration>s` (legacy only) |
| First + Last frame to video | not yet ported | `POST /api/flow/generate-video` | `abra_i2v_<duration>s` (legacy only) |
| References to video | not yet ported | `POST /api/flow/generate-video-omni` | `abra_r2v_<duration>s` (legacy only) |

Text-to-video supports `4`, `6`, `8`, and `10` seconds, with portrait and landscape aspect ratios. The migrated `YhhmEf` wire was live-verified with `abra_t2v_4s`; the downloaded result was exactly 4.000 seconds at 1280×720/24 fps. Completed media resolves through the migrated `as29s` media lookup.

## End-to-end integration flow

1. Check `/health` and `/api/flow/status`.
2. Submit `POST /api/flow/generate-video-omni-text` with prompt, project ID, duration and aspect ratio.
3. Persist the returned `flowkitPolling` object.
4. Poll `/api/flow/check-omni-status` every 10–20 seconds with its `project_id` and `workflows`.
5. On `COMPLETED`, immediately download `workflows[].media.url`; the signed URL is short-lived.

Example:

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/generate-video-omni-text" \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "A small red paper boat gently drifts across a calm pond",
    "project_id": "FLOW_PROJECT_ID",
    "duration_s": 4,
    "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE"
  }'
```

Do not feed Omni workflow names to the legacy Veo operation poller.

## Supplying images

This section applies to the legacy frame/reference Omni modes, which are not yet ported to the migrated batch transport.

`POST /api/flow/upload-image` is not a multipart upload endpoint. Its `file_path` is an absolute path on the **FlowKit server**, not on the calling server.

For a remote integration, first stage the file on the FlowKit host using an authenticated transfer such as SFTP/SCP, a private shared volume, or a separately secured upload service. Use a unique per-job directory, validate file size/type, and make the file readable by the FlowKit service account. Then call:

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/upload-image" \
  -H 'Content-Type: application/json' \
  -d '{
    "file_path": "/var/lib/flowkit/input/JOB_ID/start.jpg",
    "project_id": "FLOW_PROJECT_ID",
    "file_name": "start.jpg"
  }'
```

Response:

```json
{"media_id":"FLOW_MEDIA_ID","raw":{}}
```

Use the returned `media_id` in generation requests. Never pass a caller-local path such as `/tmp/image.jpg` unless that exact file also exists on the FlowKit server.

## Prompts

The input images define identity, appearance, objects, and composition. The prompt should primarily describe motion, camera behavior, timing, and audio/dialogue. Avoid restating a person's detailed appearance when reference images already provide it.

Example:

```text
The woman turns naturally toward the camera, smiles, then raises one leg into the final pose. Subtle handheld camera movement, realistic cloth and hair motion, stable face and body proportions.
```

For longer clips, timed instructions are useful, for example: `0-3s: ...; 3-6s: ...; 6-8s: ...`.

## First frame to video

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/generate-video" \
  -H 'Content-Type: application/json' \
  -d '{
    "model_family": "omni_flash",
    "start_image_media_id": "START_MEDIA_ID",
    "prompt": "The subject looks toward the camera and smiles; subtle cinematic push-in, natural motion.",
    "project_id": "FLOW_PROJECT_ID",
    "scene_id": "JOB_ID",
    "duration_s": 4,
    "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "user_paygate_tier": "PAYGATE_TIER_ONE"
  }'
```

This uses `batchAsyncGenerateVideoStartImage`.

## First + Last frame to video

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/generate-video" \
  -H 'Content-Type: application/json' \
  -d '{
    "model_family": "omni_flash",
    "start_image_media_id": "START_MEDIA_ID",
    "end_image_media_id": "END_MEDIA_ID",
    "prompt": "The subject moves naturally from the first pose to the final pose; stable identity and smooth realistic motion.",
    "project_id": "FLOW_PROJECT_ID",
    "scene_id": "JOB_ID",
    "duration_s": 4,
    "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "user_paygate_tier": "PAYGATE_TIER_ONE"
  }'
```

This uses `batchAsyncGenerateVideoStartAndEndImage` and sends both `startImage` and `endImage`.

## References to video

Use 1-7 media IDs. Reference images act as components/identity/style guidance; they are not treated as fixed first and last frames.

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/generate-video-omni" \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_media_ids": ["REFERENCE_MEDIA_ID_1", "REFERENCE_MEDIA_ID_2"],
    "prompt": "Cinematic handheld shot with natural character motion and consistent referenced subjects.",
    "project_id": "FLOW_PROJECT_ID",
    "scene_id": "JOB_ID",
    "duration_s": 4,
    "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "user_paygate_tier": "PAYGATE_TIER_ONE"
  }'
```

The compatible generic endpoint is `POST /api/flow/generate-video-refs` with the same fields plus `"model_family":"omni_flash"`.

## Submit response and polling

Persist the entire normalized polling descriptor returned by submit:

```json
{
  "flowkitPolling": {
    "mode": "project_media",
    "project_id": "FLOW_PROJECT_ID",
    "workflows": [
      {
        "name": "WORKFLOW_NAME",
        "primary_media_id": "PRIMARY_MEDIA_ID",
        "project_id": "FLOW_PROJECT_ID"
      }
    ]
  }
}
```

Poll using those values without transforming workflow names into operation handles:

```bash
curl -fsS -X POST "$FLOWKIT_BASE_URL/api/flow/check-omni-status" \
  -H 'Content-Type: application/json' \
  -d '{
    "project_id": "FLOW_PROJECT_ID",
    "workflows": [
      {
        "name": "WORKFLOW_NAME",
        "primary_media_id": "PRIMARY_MEDIA_ID",
        "project_id": "FLOW_PROJECT_ID"
      }
    ],
    "include_encoded_video": false
  }'
```

The generic `POST /api/flow/check-status` endpoint also accepts the same `workflows` and automatically selects Omni project polling.

Pending response:

```json
{
  "project_id": "FLOW_PROJECT_ID",
  "done": false,
  "status": "PENDING",
  "workflows": [{"done":false,"status":"PENDING"}]
}
```

Successful response:

```json
{
  "project_id": "FLOW_PROJECT_ID",
  "done": true,
  "status": "COMPLETED",
  "workflows": [
    {
      "done": true,
      "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
      "media": {
        "media_id": "PRIMARY_MEDIA_ID",
        "url": "https://flow-content.google/...",
        "encoded_video_available": false
      }
    }
  ]
}
```

Keep `include_encoded_video` set to `false`. FlowKit resolves the signed download URL without buffering the MP4 through Chrome or the extension bridge.

## Retry and failure policy

- HTTP `400`: request/contract error. Do not retry unchanged input.
- HTTP `503`: Chrome extension is disconnected. Pause submission and alert or retry health checks with bounded backoff.
- HTTP `502`: Flow/bridge failure. Retry a small bounded number of times with exponential backoff; preserve the original workflow descriptor.
- Poll result `PENDING`: poll again after 10-20 seconds. Do not submit the generation again.
- Poll result `FAILED`: stop polling and surface the workflow error.
- `COMPLETED` with a null URL or `url_error`: poll again to obtain a fresh signed URL; do not regenerate the video.

Submission is credit-consuming and is not guaranteed to be idempotent. Never blindly retry a timed-out submit unless the integration can determine that no workflow was created.

## Model configuration

Mappings live in `agent/models.json`:

```json
{
  "omni_flash_models": {
    "frame_to_video": {
      "4": "abra_i2v_4s",
      "6": "abra_i2v_6s",
      "8": "abra_i2v_8s",
      "10": "abra_i2v_10s"
    },
    "start_end_frame_to_video": {
      "4": "abra_i2v_4s",
      "6": "abra_i2v_6s",
      "8": "abra_i2v_8s",
      "10": "abra_i2v_10s"
    },
    "reference_to_video": {
      "4": "abra_r2v_4s",
      "6": "abra_r2v_6s",
      "8": "abra_r2v_8s",
      "10": "abra_r2v_10s"
    }
  }
}
```

The mappings can be changed through `PATCH /api/models` if Google rotates internal keys. Treat configured credit-cost estimates as informational only: Google can change pricing, so use `GET /api/flow/credits` and the submit response's `remainingCredits` where available.

## Minimal agent checklist

- Use the `/api/flow/...` paths exactly.
- Select Omni explicitly with `model_family: "omni_flash"` on shared endpoints.
- Upload inputs once and reuse returned Flow media IDs.
- Persist `project_id`, workflow `name`, and `primary_media_id` before polling.
- Poll workflows through project media status, never through legacy Veo operations.
- Download signed output URLs immediately into durable project storage.
- Do not log Google auth data, extension messages, or complete signed URLs.
- Use a stable `scene_id`/job ID for traceability.
- Start with 4 seconds for smoke tests to limit credit usage.

Omni Flash is an unofficial integration over Google Flow's internal interfaces. Endpoints, model keys, and response shapes may change when Flow changes; integrations should fail visibly and retain job metadata for diagnosis.
