"""SDK OperationService — executes media generation operations directly.

Each method receives loaded data (scene/character dicts), calls FlowClient,
parses results, updates the DB, and returns a result dict for processor
status tracking.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from typing import TYPE_CHECKING, Optional


def _build_continuation_prompt(base_prompt: str) -> str:
    """Build a transformation-focused prompt for CONTINUATION scene images.

    When editing from a parent image, the default prompt just describes the
    child scene statically — the edit API preserves the parent's composition.
    This helper prepends transformation instructions so the AI actually
    changes camera angle, location, and setup.
    """
    return (
        f"Transform this image into a completely different moment. "
        f"Move the camera to a new angle, position, and composition. "
        f"Change the surrounding environment and visual setup. "
        f"{base_prompt}"
    )


def _char_matches(c: dict, name_set: set) -> bool:
    """Check if a character matches any name in the set by slug OR display name."""
    slug = c.get("slug") or ""
    name = c.get("name", "")
    return (slug and slug in name_set) or (name and name in name_set)

import aiohttp

from agent.db import crud
from agent.config import USE_BATCH_RPC, VIDEO_POLL_INTERVAL, VIDEO_POLL_TIMEOUT
from agent.utils.paths import scene_4k_path
from agent.utils.slugify import slugify
from agent.worker._parsing import (
    _is_error,
    _is_uuid,
    _extract_uuid_from_url,
    _extract_media_id,
    _extract_output_url,
)

if TYPE_CHECKING:
    from agent.services.flow_client import FlowClient
    from agent.sdk.persistence.base import Repository

logger = logging.getLogger(__name__)

# Entity types that need landscape (wide) reference images
_LANDSCAPE_ENTITY_TYPES = {"location"}


def _reference_aspect_ratio(entity_type: str) -> str:
    """Pick aspect ratio based on entity type."""
    if entity_type in _LANDSCAPE_ENTITY_TYPES:
        return "IMAGE_ASPECT_RATIO_LANDSCAPE"
    return "IMAGE_ASPECT_RATIO_PORTRAIT"


def _save_raw_bytes(
    operations: list[dict], scene_id: str, project_slug: str, display_order: int
) -> str | None:
    """If operations contain rawBytes (inline 4K video), save to disk and return path."""
    for op in operations:
        raw_b64 = op.get("rawBytes")
        if not raw_b64:
            continue
        # Guard against extremely large payloads (>500MB base64 ≈ ~685M chars)
        if len(raw_b64) > 685_000_000:
            logger.warning("rawBytes too large (%d chars), skipping", len(raw_b64))
            continue
        try:
            video_data = base64.b64decode(raw_b64)
            path = scene_4k_path(project_slug, display_order, scene_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(video_data)
            logger.info("Saved rawBytes 4K video: %s (%d bytes)", path, len(video_data))
            return str(path)
        except Exception as e:
            logger.warning("Failed to save rawBytes: %s", e)
    return None


def _extract_operations(result: dict) -> list[dict]:
    """Extract operations list from video gen / upscale submit response.

    Supports two response schemas:
    - OLD (Lite/Fast/Ultra): {"data": {"operations": [{"operation": {"name": ...}, "status": ...}]}}
    - NEW (Low Priority — veo_3_1_*_low_priority, *_ultra_relaxed):
        {"data": {"workflows": [{"name": "...", "metadata": {"primaryMediaId": "..."}}],
                  "media": [{"name": "...", ...}]}}
    """
    data = result.get("data", result)
    ops = data.get("operations", [])
    if ops:
        for op in ops:
            op_name = op.get("operation", {}).get("name")
            if not op_name:
                logger.warning("Operation missing name: %s", op)
        return ops

    # NEW schema: workflows + media → synthesize operation entries
    workflows = data.get("workflows", [])
    media_list = data.get("media", [])
    if not workflows or not media_list:
        return []

    media_by_id = {m.get("name"): m for m in media_list if m.get("name")}
    synthesized = []
    for wf in workflows:
        wf_name = wf.get("name", "")
        meta = wf.get("metadata", {})
        primary_media_id = meta.get("primaryMediaId", "")
        if not wf_name or not primary_media_id:
            continue
        synthesized.append({
            "operation": {
                "name": wf_name,
                "metadata": {"video": {"mediaId": primary_media_id}},
            },
            "status": "MEDIA_GENERATION_STATUS_PENDING",
            "_workflow_mode": True,
            "_primary_media_id": primary_media_id,
        })
    if synthesized:
        logger.info("Detected workflow-schema response: %d workflow(s) → synthesized", len(synthesized))
    return synthesized


async def _poll_workflows(
    client: FlowClient,
    operations: list[dict],
    timeout: int,
) -> dict:
    """Poll workflow-mode operations (Low Priority). Flow returns MP4 binary
    inline as base64 in `video.encodedVideo` — decode and save to disk, then
    synthesize an OLD-schema success response with a file:// URL.

    The response shape is:
      {"name": "<media_id>", "video": {"encodedVideo": "<base64 MP4>", ...}}

    Detection logic:
    - "ready" = response is a dict with keys {"name","video"} where video.encodedVideo
      starts with AAAAI... (MP4 ftyp header in base64)
    - "still gen" = response missing video block, or encodedVideo missing/empty
    """
    import base64
    import os as _os

    poll_interval = VIDEO_POLL_INTERVAL
    elapsed = 0
    completed = {}  # media_id → local_path

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        for op in operations:
            mid = op.get("_primary_media_id", "")
            if not mid or mid in completed:
                continue
            media_resp = await client.get_media(mid)
            status = media_resp.get("status")
            if status != 200:
                logger.debug("Workflow media %s not ready (status=%s)", mid[:8], status)
                continue

            # Direct top-level (not wrapped in `data`)
            payload = media_resp.get("data", media_resp) if isinstance(media_resp.get("data"), dict) and "video" in media_resp.get("data", {}) else media_resp
            video_block = payload.get("video", {}) if isinstance(payload, dict) else {}
            encoded = video_block.get("encodedVideo", "") if isinstance(video_block, dict) else ""

            if not encoded:
                continue
            try:
                binary = base64.b64decode(encoded)
            except Exception as e:
                logger.warning("Workflow media %s: failed to decode encodedVideo: %s", mid[:8], e)
                continue
            # Validate MP4 magic: real video starts with `ftyp` box at bytes 4-8.
            # While generating, Flow returns metadata payload (~1-2KB) — skip until real MP4.
            is_mp4 = len(binary) >= 12 and binary[4:8] == b"ftyp"
            if not is_mp4:
                logger.debug("Workflow media %s still generating (got %d bytes, not MP4)",
                             mid[:8], len(binary))
                continue
            out_dir = "output/_workflow_videos"
            _os.makedirs(out_dir, exist_ok=True)
            out_path = f"{out_dir}/{mid}.mp4"
            with open(out_path, "wb") as f:
                f.write(binary)
            completed[mid] = {"path": out_path, "size": len(binary)}
            logger.info("Workflow media %s ready: saved %d bytes → %s",
                        mid[:8], len(binary), out_path)

        if len(completed) == len(operations):
            synth_ops = []
            for op in operations:
                mid = op.get("_primary_media_id", "")
                wf_name = op.get("operation", {}).get("name", "")
                local = completed.get(mid, {}).get("path", "")
                # Use file:// so downstream sees a URL-shaped string
                local_url = f"file://{_os.path.abspath(local)}" if local else ""
                synth_ops.append({
                    "operation": {
                        "name": wf_name,
                        "metadata": {"video": {"mediaId": mid, "fifeUrl": local_url}},
                    },
                    "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                })
            logger.info("All %d workflow(s) completed after %ds", len(operations), elapsed)
            return {"data": {"operations": synth_ops}}

    logger.warning("Workflow polling timed out after %ds. Done=%d/%d",
                   timeout, len(completed), len(operations))
    return {"error": f"Workflow polling timeout after {timeout}s"}


async def _poll_operations(
    client: FlowClient,
    operations: list[dict],
    timeout: int = VIDEO_POLL_TIMEOUT,
) -> dict:
    """Poll until all operations complete or timeout.

    Two polling paths:
    - OLD schema → check_video_status(operations)
    - Workflow mode (Low Priority) → poll get_media(primaryMediaId) until ready
    """
    if not operations:
        return {"error": "No operations to poll"}

    # Workflow-mode polling: poll media endpoint for each primaryMediaId
    if all(op.get("_workflow_mode") for op in operations):
        return await _poll_workflows(client, operations, timeout)

    poll_interval = VIDEO_POLL_INTERVAL
    elapsed = 0
    current_ops = operations
    # The batch path attaches the operation's own grumble ("Media not found.")
    # to a still-pending round. It is a diagnostic, not a verdict — finished
    # jobs report it too — so it is only worth quoting if we time out.
    last_complaint = None

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        status_result = await client.check_video_status(current_ops)
        if _is_error(status_result):
            logger.warning("Status poll error: %s", status_result.get("error"))
            continue

        data = status_result.get("data", status_result)
        ops = data.get("operations", [])
        if not ops:
            continue

        current_ops = ops
        all_done = True
        has_error = False
        error_msg = ""

        for op in ops:
            if op.get("complaint"):
                last_complaint = op["complaint"]
            status = op.get("status", "")
            if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
                continue
            elif status == "MEDIA_GENERATION_STATUS_FAILED":
                op_name = op.get('operation', {}).get('name', '?')
                # Log full operation for debugging failure reason
                import json as _json
                logger.error("Operation FAILED: name=%s full=%s", op_name, _json.dumps(op)[:1000])
                error_msg = f"Operation failed: {op_name}"
                has_error = True
                break
            else:
                all_done = False

        if has_error:
            return {"error": error_msg}
        if all_done:
            logger.info("All %d operations completed after %ds", len(ops), elapsed)
            return {"data": data}

        done_count = sum(1 for o in ops if o.get("status") == "MEDIA_GENERATION_STATUS_SUCCESSFUL")
        logger.debug("Poll %ds/%ds: %d/%d done", elapsed, timeout, done_count, len(ops))

    detail = f": {last_complaint}" if last_complaint else ""
    return {"error": f"Polling timeout after {timeout}s{detail}"}


class OperationService:
    """Executes media generation operations using FlowClient + Repository.

    Each method calls FlowClient directly, parses the response, updates
    the database, and returns a result dict (with 'data' or 'error').
    """

    def __init__(self, flow_client: FlowClient, repo: Repository):
        self._client = flow_client
        self._repo = repo

    # ------------------------------------------------------------------
    # Scene image operations
    # ------------------------------------------------------------------

    async def generate_scene_image(self, scene: dict, orientation: str) -> dict:
        """Generate a scene image with reference imageInputs."""
        project = await crud.get_project(scene.get("_project_id", "0"))
        aspect = "IMAGE_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "IMAGE_ASPECT_RATIO_LANDSCAPE"
        prompt = scene.get("image_prompt") or scene.get("prompt", "")
        # CONTINUATION scenes: enrich prompt with transformation context
        if scene.get("parent_scene_id") and not scene.get("image_prompt"):
            prompt = _build_continuation_prompt(prompt)
        tier = project.get("user_paygate_tier", "PAYGATE_TIER_TWO") if project else "PAYGATE_TIER_TWO"
        pid = scene.get("_project_id", "0")

        # Resolve character reference media_ids
        char_media_ids = None
        char_names_raw = scene.get("character_names")
        if char_names_raw and pid:
            if isinstance(char_names_raw, str):
                try:
                    char_names_raw = json.loads(char_names_raw)
                except json.JSONDecodeError:
                    char_names_raw = []
            if not isinstance(char_names_raw, list):
                char_names_raw = []
            if char_names_raw:
                project_chars = await crud.get_project_characters(pid)
                valid_ids = []
                missing_refs = []
                char_names_set = set(char_names_raw)
                for c in project_chars:
                    if not _char_matches(c, char_names_set):
                        continue
                    mid = c.get("media_id")
                    if mid:
                        valid_ids.append(mid)
                    else:
                        missing_refs.append(c.get("slug") or c["name"])

                if missing_refs:
                    return {"error": f"Waiting for reference images: {', '.join(missing_refs)}"}

                char_media_ids = valid_ids if valid_ids else None
                if char_media_ids:
                    logger.info("Scene %s: using %d reference images",
                                scene.get("id", "?")[:8], len(char_media_ids))

        return await self._client.generate_images(
            prompt=prompt, project_id=pid, aspect_ratio=aspect,
            user_paygate_tier=tier, character_media_ids=char_media_ids,
        )

    async def edit_scene_image(self, scene: dict, orientation: str,
                               source_media_id: str | None = None) -> dict:
        """Edit an existing scene image using IMAGE_INPUT_TYPE_BASE_IMAGE.

        Resolves character refs from scene's character_names and passes them
        as IMAGE_INPUT_TYPE_REFERENCE after the base image. Order:
        [base_image, char_A, char_B, ...] — helps Google Flow detect characters.
        """
        project = await crud.get_project(scene.get("_project_id", "0"))
        aspect = "IMAGE_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "IMAGE_ASPECT_RATIO_LANDSCAPE"
        tier = project.get("user_paygate_tier", "PAYGATE_TIER_ONE") if project else "PAYGATE_TIER_ONE"
        pid = scene.get("_project_id", "0")

        src = source_media_id
        orient_prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        # CONTINUATION scenes always edit from parent's image (that's the
        # whole point of chaining).  Only non-chain scenes edit their own.
        if not src and scene.get("parent_scene_id"):
            parent = await crud.get_scene(scene["parent_scene_id"])
            if parent:
                src = parent.get(f"{orient_prefix}_image_media_id")
        if not src:
            src = scene.get(f"{orient_prefix}_image_media_id")
        if not src:
            return {"error": "No source image to edit — generate a scene image first"}

        edit_prompt = scene.get("image_prompt") or scene.get("prompt", "")
        # CONTINUATION scenes: enrich prompt with transformation context
        if scene.get("parent_scene_id") and not scene.get("image_prompt"):
            edit_prompt = _build_continuation_prompt(edit_prompt)

        # Resolve character reference media_ids for edit consistency
        char_media_ids = None
        char_names_raw = scene.get("character_names")
        if char_names_raw and pid:
            if isinstance(char_names_raw, str):
                try:
                    char_names_raw = json.loads(char_names_raw)
                except json.JSONDecodeError:
                    char_names_raw = []
            if isinstance(char_names_raw, list) and char_names_raw:
                project_chars = await crud.get_project_characters(pid)
                valid_ids = []
                char_names_set = set(char_names_raw)
                for c in project_chars:
                    if _char_matches(c, char_names_set) and c.get("media_id"):
                        valid_ids.append(c["media_id"])
                char_media_ids = valid_ids if valid_ids else None

        return await self._client.edit_image(
            prompt=edit_prompt, source_media_id=src,
            project_id=pid, aspect_ratio=aspect,
            user_paygate_tier=tier,
            character_media_ids=char_media_ids,
        )

    # ------------------------------------------------------------------
    # Video operations
    # ------------------------------------------------------------------

    async def generate_scene_video(self, scene: dict, orientation: str,
                                   request_id: str = "") -> dict:
        """Generate video from a scene image (i2v). Submits + polls."""
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        image_media_id = scene.get(f"{prefix}_image_media_id")
        if not image_media_id:
            return {"error": f"No {prefix} image media_id for scene"}

        project = await crud.get_project(scene.get("_project_id", "0"))
        aspect = "VIDEO_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "VIDEO_ASPECT_RATIO_LANDSCAPE"
        tier = project.get("user_paygate_tier", "PAYGATE_TIER_TWO") if project else "PAYGATE_TIER_TWO"
        pid = scene.get("_project_id", "0")
        end_id = scene.get(f"{prefix}_end_scene_media_id")

        # Chain scenes with end_image: prefer transition_prompt (describes motion between frames)
        if end_id and scene.get("transition_prompt"):
            base_prompt = scene["transition_prompt"]
        else:
            base_prompt = scene.get("video_prompt") or scene.get("prompt", "")
        prompt = await _build_video_prompt(base_prompt, scene, pid)

        # Check if already submitted (op_name saved from previous attempt)
        # OLD schema (Lite/Fast/Ultra): op_name is "models/.../operations/..." → re-poll via check_video_status
        # NEW schema (Low Priority workflow): op_name is bare UUID → cannot recover (need primary_media_id
        # which isn't persisted yet); fall through and resubmit (Low Priority is free, duplicate is OK)
        existing_op = None
        if request_id:
            req_row = await crud.get_request(request_id)
            existing_op = req_row.get("request_id") if req_row else None

        # A bare uuid means different things on the two transports. On the
        # legacy path it is a Low Priority workflow name that cannot be
        # re-polled, so the retry resubmits. On the batch path it is the
        # operation id, and looking it up in the project listing is exactly
        # what the status poll does — resubmitting there would abandon a
        # running render and pay for a second one.
        bare_uuid = bool(existing_op and len(existing_op) == 36 and existing_op.count("-") == 4)
        looks_like_workflow_uuid = bare_uuid and not USE_BATCH_RPC
        if existing_op and not looks_like_workflow_uuid:
            logger.info("Video gen already submitted (op=%s), re-polling", existing_op[:30])
            operations = [{"operation": {"name": existing_op}, "status": "MEDIA_GENERATION_STATUS_PENDING"}]
            return await _poll_operations(self._client, operations)
        # else: workflow UUID — fall through and resubmit fresh

        submit_result = await self._client.generate_video(
            start_image_media_id=image_media_id,
            prompt=prompt,
            project_id=pid,
            scene_id=scene.get("id", ""),
            aspect_ratio=aspect,
            end_image_media_id=end_id,
            user_paygate_tier=tier,
        )

        if _is_error(submit_result):
            logger.error("[DEBUG] Video gen submit_result IS_ERROR: %s", str(submit_result)[:2000])
            return submit_result

        operations = _extract_operations(submit_result)
        if not operations:
            logger.error("[DEBUG] Video gen NO_OPERATIONS submit_result: %s", str(submit_result)[:2000])
            return {"error": "Video gen returned no operations"}

        op_name = operations[0].get("operation", {}).get("name", "")
        if request_id:
            await crud.update_request(request_id, request_id=op_name)

        status = operations[0].get("status", "")
        if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
            logger.info("Video gen completed immediately")
            return submit_result
        if status == "MEDIA_GENERATION_STATUS_FAILED":
            return {"error": "Video generation failed immediately"}

        logger.info("Video gen submitted, polling %d operations...", len(operations))
        return await _poll_operations(self._client, operations)

    async def generate_scene_video_refs(self, scene: dict, orientation: str,
                                        request_id: str = "") -> dict:
        """Generate video from reference images (r2v). Submits + polls.

        R2V uses any entity images (characters, visual_assets, locations) plus
        scene images as IMAGE_USAGE_TYPE_ASSET references — not just character
        face refs.  Collect all matching entity media_ids and optionally include
        the scene's end_scene image.
        """
        project = await crud.get_project(scene.get("_project_id", "0"))
        aspect = "VIDEO_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "VIDEO_ASPECT_RATIO_LANDSCAPE"
        tier = project.get("user_paygate_tier", "PAYGATE_TIER_TWO") if project else "PAYGATE_TIER_TWO"
        pid = scene.get("_project_id", "0")
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        end_id = scene.get(f"{prefix}_end_scene_media_id")

        # Chain scenes with end_image: prefer transition_prompt
        if end_id and scene.get("transition_prompt"):
            base_prompt = scene["transition_prompt"]
        else:
            base_prompt = scene.get("video_prompt") or scene.get("prompt", "")
        prompt = await _build_video_prompt(base_prompt, scene, pid)

        char_names_raw = scene.get("character_names")
        if isinstance(char_names_raw, str):
            try:
                char_names_raw = json.loads(char_names_raw)
            except json.JSONDecodeError:
                char_names_raw = []

        if not pid:
            return {"error": "No project_id for r2v video generation"}

        # Collect up to 3 reference images (API max).  Priority order:
        #   1. end_scene_media_id — chain continuity target (end frame)
        #   2. visual_asset entities — primary objects (vehicles, props)
        #   3. character entities — main character consistency
        # Location entities are excluded — they add generic backgrounds
        # that can re-introduce unwanted visual elements (e.g. buildings
        # removed from a scene).
        _R2V_MAX_REFS = 3
        _R2V_ENTITY_PRIORITY = ("visual_asset", "character")
        ref_ids: list[str] = []
        seen: set[str] = set()

        # 1. end_scene image (highest priority for chain scenes)
        if end_id and end_id not in seen:
            ref_ids.append(end_id)
            seen.add(end_id)

        # 2-3. Entities by priority: visual_asset first, then character
        if char_names_raw and len(ref_ids) < _R2V_MAX_REFS:
            project_entities = await crud.get_project_characters(pid)
            char_names_set = set(char_names_raw)
            for etype in _R2V_ENTITY_PRIORITY:
                for c in project_entities:
                    if len(ref_ids) >= _R2V_MAX_REFS:
                        break
                    if not _char_matches(c, char_names_set):
                        continue
                    if c.get("entity_type") != etype:
                        continue
                    mid = c.get("media_id")
                    if mid and mid not in seen:
                        ref_ids.append(mid)
                        seen.add(mid)

        if not ref_ids:
            return {"error": "No valid reference media_ids for r2v"}

        # Check if already submitted (op_name saved from previous attempt)
        existing_op = None
        if request_id:
            req_row = await crud.get_request(request_id)
            existing_op = req_row.get("request_id") if req_row else None

        if existing_op:
            logger.info("R2V already submitted (op=%s), re-polling", existing_op[:30])
            operations = [{"operation": {"name": existing_op}, "status": "MEDIA_GENERATION_STATUS_PENDING"}]
            return await _poll_operations(self._client, operations)

        submit_result = await self._client.generate_video_from_references(
            reference_media_ids=ref_ids,
            prompt=prompt,
            project_id=pid,
            scene_id=scene.get("id", ""),
            aspect_ratio=aspect,
            user_paygate_tier=tier,
        )

        if _is_error(submit_result):
            return submit_result

        operations = _extract_operations(submit_result)
        if not operations:
            return {"error": "R2V returned no operations"}

        op_name = operations[0].get("operation", {}).get("name", "")
        if request_id:
            await crud.update_request(request_id, request_id=op_name)

        status = operations[0].get("status", "")
        if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
            logger.info("R2V completed immediately")
            return submit_result
        if status == "MEDIA_GENERATION_STATUS_FAILED":
            return {"error": "R2V failed immediately"}

        logger.info("R2V submitted with %d refs, polling %d operations...", len(ref_ids), len(operations))
        return await _poll_operations(self._client, operations)

    async def upscale_scene_video(self, scene: dict, orientation: str,
                                  request_id: str = "") -> dict:
        """Upscale a completed scene video. Submits + polls.

        If a previous attempt already submitted (op_name saved in DB), skip
        submit and just re-poll — avoids duplicate API calls on retry.
        """
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        video_media_id = scene.get(f"{prefix}_video_media_id")
        if not video_media_id:
            return {"error": f"No {prefix} video media_id for scene"}

        aspect = "VIDEO_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "VIDEO_ASPECT_RATIO_LANDSCAPE"

        # Check if already submitted (op_name saved from previous attempt)
        existing_op = None
        if request_id:
            req_row = await crud.get_request(request_id)
            existing_op = req_row.get("request_id") if req_row else None

        if existing_op:
            # Already submitted — just re-poll
            logger.info("Upscale already submitted (op=%s), re-polling", existing_op[:30])
            operations = [{"operation": {"name": existing_op}, "status": "MEDIA_GENERATION_STATUS_PENDING"}]
            return await _poll_operations(self._client, operations, timeout=300)

        submit_result = await self._client.upscale_video(
            media_id=video_media_id,
            scene_id=scene.get("id", ""),
            aspect_ratio=aspect,
        )

        if _is_error(submit_result):
            return submit_result

        operations = _extract_operations(submit_result)
        if not operations:
            return {"error": "Upscale returned no operations"}

        # Check for inline rawBytes (4K video data returned directly)
        project = await crud.get_project(scene.get("_project_id", "0"))
        project_slug = slugify(project.get("name", "unnamed")) if project else slugify(scene.get("_project_id", "unnamed"))
        display_order = scene.get("display_order", 0)
        raw_path = _save_raw_bytes(operations, scene.get("id", ""), project_slug, display_order)
        if raw_path:
            logger.info("Upscale returned inline 4K video, saved to %s", raw_path)
            # Inject saved path into result for downstream parsing
            if not operations[0].get("operation"):
                operations[0]["operation"] = {}
            operations[0]["operation"].setdefault("metadata", {}).setdefault("video", {})["fifeUrl"] = raw_path
            operations[0]["status"] = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            return {"data": {"operations": operations}}

        op_name = operations[0].get("operation", {}).get("name", "")
        if request_id:
            await crud.update_request(request_id, request_id=op_name)

        status = operations[0].get("status", "")
        if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
            logger.info("Upscale completed immediately")
            return submit_result
        if status == "MEDIA_GENERATION_STATUS_FAILED":
            return {"error": "Upscale failed immediately"}

        logger.info("Upscale submitted, polling %d operations...", len(operations))
        poll_result = await _poll_operations(self._client, operations, timeout=300)

        # Check poll result for rawBytes too
        poll_data = poll_result.get("data", poll_result)
        poll_ops = poll_data.get("operations", [])
        if poll_ops:
            raw_path = _save_raw_bytes(poll_ops, scene.get("id", ""), project_slug, display_order)
            if raw_path:
                logger.info("Poll returned inline 4K video, saved to %s", raw_path)
                poll_ops[0].setdefault("operation", {}).setdefault("metadata", {}).setdefault("video", {})["fifeUrl"] = raw_path

        return poll_result

    # ------------------------------------------------------------------
    # Reference image operations
    # ------------------------------------------------------------------

    async def generate_reference_image(self, char: dict, project_id: str) -> dict:
        """Generate a reference image for a character/entity.

        Handles fast-path (image exists, just upload) and normal path (generate + upload).
        Updates character DB record with media_id and reference_image_url.
        Returns result dict.
        """
        entity_type = char.get("entity_type", "character")
        pid = project_id

        # Fast path: image already generated, just need upload for UUID
        existing_url = char.get("reference_image_url")
        if existing_url and not char.get("media_id"):
            logger.info("%s '%s' already has image, retrying upload only (saving credits)",
                        entity_type, char["name"])
            upload_mid = await _upload_character_image(self._client, {
                "name": char["name"],
                "reference_image_url": existing_url,
            }, pid)

            if upload_mid:
                await crud.update_character(char["id"], media_id=upload_mid)
                logger.info("%s '%s' upload retry succeeded: media_id=%s",
                            entity_type, char["name"], upload_mid[:30])
                return {"data": {"media": [{"name": upload_mid}]}}

            uuid_from_url = _extract_uuid_from_url(existing_url)
            if uuid_from_url:
                await crud.update_character(char["id"], media_id=uuid_from_url)
                logger.info("%s '%s' extracted UUID from URL: media_id=%s",
                            entity_type, char["name"], uuid_from_url)
                return {"data": {"media": [{"name": uuid_from_url}]}}

            return {"error": f"Upload retry failed for {char['name']} — image exists but cannot get UUID media_id"}

        # Normal path: generate image from scratch
        prompt = char.get("image_prompt") or f"Character reference: {char['name']}. {char.get('description', '')}"

        project = await crud.get_project(pid) if pid != "0" else None
        tier = project.get("user_paygate_tier", "PAYGATE_TIER_TWO") if project else "PAYGATE_TIER_TWO"
        aspect = _reference_aspect_ratio(entity_type)

        result = await self._client.generate_images(
            prompt=prompt, project_id=pid, aspect_ratio=aspect,
            user_paygate_tier=tier,
        )

        if not _is_error(result):
            output_url = _extract_output_url(result, "GENERATE_IMAGE")

            if output_url:
                direct_mid = _extract_media_id(result, "GENERATE_IMAGE")
                if direct_mid and _is_uuid(direct_mid):
                    await crud.update_character(char["id"], media_id=direct_mid, reference_image_url=output_url)
                    logger.info("%s '%s' ref image ready (no upload needed, %s): media_id=%s",
                                entity_type, char["name"], aspect.split("_")[-1].lower(), direct_mid)
                    return result

                upload_mid = await _upload_character_image(self._client, {
                    "name": char["name"],
                    "reference_image_url": output_url,
                }, pid)

                if upload_mid:
                    await crud.update_character(char["id"], media_id=upload_mid, reference_image_url=output_url)
                    logger.info("%s '%s' ref image uploaded (%s): media_id=%s",
                                entity_type, char["name"], aspect.split("_")[-1].lower(),
                                upload_mid[:30] if upload_mid else "?")
                else:
                    await crud.update_character(char["id"], reference_image_url=output_url)
                    uuid_from_url = _extract_uuid_from_url(output_url)
                    if uuid_from_url:
                        await crud.update_character(char["id"], media_id=uuid_from_url)
                        logger.info("%s '%s' extracted UUID from URL fallback: media_id=%s",
                                    entity_type, char["name"], uuid_from_url)
                        return {"data": {"media": [{"name": uuid_from_url}]}}
                    logger.warning("%s '%s' upload failed, no media_id stored — will retry",
                                   entity_type, char["name"])
                    return {"error": f"Upload failed for {char['name']} — image generated but could not get UUID media_id"}

        return result

    # ------------------------------------------------------------------
    # Queue-based wrappers (create request in DB for processor pickup)
    # ------------------------------------------------------------------

    async def _resolve_queue_orientation(self, video_id: str, orientation: str | None) -> str:
        """Resolve orientation for queue methods: explicit > video table > VERTICAL."""
        if orientation:
            return orientation
        video = await crud.get_video(video_id)
        if video and video.get("orientation"):
            return video["orientation"]
        return "VERTICAL"

    async def queue_scene_image(self, scene_id: str, project_id: str,
                                video_id: str, orientation: str | None = None) -> str:
        """Queue a GENERATE_IMAGE request. Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="GENERATE_IMAGE", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
        )
        return row["id"]

    async def queue_edit_scene_image(self, scene_id: str, project_id: str,
                                     video_id: str, orientation: str | None = None,
                                     edit_prompt: str | None = None,
                                     source_media_id: str | None = None) -> str:
        """Queue an EDIT_IMAGE request. Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="EDIT_IMAGE", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
            edit_prompt=edit_prompt, source_media_id=source_media_id,
        )
        return row["id"]

    async def queue_scene_video(self, scene_id: str, project_id: str,
                                video_id: str, orientation: str | None = None) -> str:
        """Queue a GENERATE_VIDEO request. Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="GENERATE_VIDEO", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
        )
        return row["id"]

    async def queue_scene_video_refs(self, scene_id: str, project_id: str,
                                     video_id: str, orientation: str | None = None) -> str:
        """Queue a GENERATE_VIDEO_REFS request. Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="GENERATE_VIDEO_REFS", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
        )
        return row["id"]

    async def queue_upscale_video(self, scene_id: str, project_id: str,
                                  video_id: str, orientation: str | None = None) -> str:
        """Queue an UPSCALE_VIDEO request. Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="UPSCALE_VIDEO", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
        )
        return row["id"]

    async def queue_reference_image(self, character_id: str, project_id: str) -> str:
        """Queue a GENERATE_CHARACTER_IMAGE request. Returns request id."""
        row = await crud.create_request(
            req_type="GENERATE_CHARACTER_IMAGE",
            character_id=character_id, project_id=project_id,
        )
        return row["id"]

    async def queue_regenerate_scene_image(self, scene_id: str, project_id: str,
                                           video_id: str, orientation: str | None = None) -> str:
        """Queue a REGENERATE_IMAGE request (bypasses skip check). Returns request id."""
        orientation = await self._resolve_queue_orientation(video_id, orientation)
        row = await crud.create_request(
            req_type="REGENERATE_IMAGE", orientation=orientation,
            scene_id=scene_id, project_id=project_id, video_id=video_id,
        )
        return row["id"]

    async def queue_regenerate_character_image(self, character_id: str, project_id: str) -> str:
        """Queue a REGENERATE_CHARACTER_IMAGE request (clears existing, regenerates). Returns request id."""
        row = await crud.create_request(
            req_type="REGENERATE_CHARACTER_IMAGE",
            character_id=character_id, project_id=project_id,
        )
        return row["id"]

    # Alias used by Character.generate_image()
    async def generate_character_image(self, character_id: str, project_id: str) -> str:
        return await self.queue_reference_image(character_id, project_id)

    async def queue_edit_character_image(self, character_id: str, project_id: str,
                                         edit_prompt: str | None = None,
                                         source_media_id: str | None = None) -> str:
        """Queue an EDIT_CHARACTER_IMAGE request. Returns request id."""
        row = await crud.create_request(
            req_type="EDIT_CHARACTER_IMAGE",
            character_id=character_id, project_id=project_id,
            edit_prompt=edit_prompt, source_media_id=source_media_id,
        )
        return row["id"]

    # Alias used by Character.edit_image()
    async def edit_character_image(self, character_id: str, project_id: str,
                                   edit_prompt: str | None = None,
                                   source_media_id: str | None = None) -> str:
        return await self.queue_edit_character_image(
            character_id, project_id, edit_prompt=edit_prompt,
            source_media_id=source_media_id,
        )


# ------------------------------------------------------------------
# Prompt building (module-level for reuse)
# ------------------------------------------------------------------

async def _build_video_prompt(base_prompt: str, scene: dict, project_id: str | None) -> str:
    """Enhance video prompt with Veo 3 audio instructions and negative prompt."""
    parts = [base_prompt.strip()]

    # Only append voice context when video_prompt contains dialogue (verb-based detection)
    dialogue_verbs = ("says", "whispers", "shouts", "asks", "replies", "murmurs", "exclaims", "gasps", "laughs", "mutters")
    prompt_lower = base_prompt.lower()
    has_dialogue = any(verb in prompt_lower for verb in dialogue_verbs)
    if project_id and has_dialogue:
        char_names_raw = scene.get("character_names")
        if isinstance(char_names_raw, str):
            try:
                char_names_raw = json.loads(char_names_raw)
            except json.JSONDecodeError:
                char_names_raw = []
        if isinstance(char_names_raw, list) and char_names_raw:
            project_chars = await crud.get_project_characters(project_id)
            char_names_set = set(char_names_raw)
            voices = []
            for c in project_chars:
                if _char_matches(c, char_names_set) and c.get("voice_description"):
                    voices.append(f"{c['name']}: {c['voice_description']}")
            if voices:
                parts.append("Character voices: " + ". ".join(voices) + ".")

    # Check project-level audio flags — Veo 3 Audio label format
    allow_music = False
    allow_voice = False
    if project_id:
        project = await crud.get_project(project_id)
        if project:
            if project.get("allow_music"):
                allow_music = True
            if project.get("allow_voice"):
                allow_voice = True

    if not allow_music:
        # Only append if prompt doesn't already have Audio:/Music: labels
        if "audio:" not in prompt_lower and "music:" not in prompt_lower:
            if allow_voice:
                parts.append("Audio: no background music. Keep character dialogue and natural ambient sounds.")
            else:
                parts.append("Audio: natural ambient sounds only, no background music, no narration, no voiceover.")

    # Veo 3 negative prompt — always append unless already present
    if "negative:" not in prompt_lower:
        parts.append("Negative: subtitles, captions, watermark, text on screen, logo, blurry faces, distorted hands.")

    return " ".join(parts)


async def _upload_character_image(client: FlowClient, char: dict, project_id: str) -> str | None:
    """Download character reference image and upload to Google Flow to get media_id."""
    ref_url = char.get("reference_image_url")
    if not ref_url:
        return None

    try:
        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ssl_ctx = ssl.create_default_context()
        async with aiohttp.ClientSession() as session:
            async with session.get(ref_url, ssl=ssl_ctx) as resp:
                if resp.status != 200:
                    logger.error("Failed to download character image: HTTP %d", resp.status)
                    return None
                image_bytes = await resp.read()
                content_type = resp.headers.get("content-type", "image/jpeg")

        if "png" in content_type:
            mime = "image/png"
        elif "gif" in content_type:
            mime = "image/gif"
        else:
            mime = "image/jpeg"

        ext = mime.split("/")[-1]
        file_name = f"{char.get('name', 'character')}.{ext}"

        encoded = base64.b64encode(image_bytes).decode("utf-8")
        result = await client.upload_image(
            encoded, mime_type=mime, project_id=project_id, file_name=file_name,
        )

        if result.get("_mediaId"):
            return result["_mediaId"]

        data = result.get("data", {})
        if isinstance(data, dict):
            media = data.get("media", {})
            if isinstance(media, dict) and media.get("name"):
                return media["name"]

        return None
    except Exception as e:
        logger.exception("Failed to upload character image: %s", e)
        return None


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_ops: Optional[OperationService] = None


def init_operations(flow_client: FlowClient, repo: Repository) -> OperationService:
    """Initialize the module-level OperationService singleton."""
    global _ops
    _ops = OperationService(flow_client=flow_client, repo=repo)
    return _ops


def get_operations() -> OperationService:
    """Return the initialized OperationService singleton."""
    if _ops is None:
        raise RuntimeError(
            "OperationService not initialized — call init_operations(flow_client, repo) first"
        )
    return _ops
