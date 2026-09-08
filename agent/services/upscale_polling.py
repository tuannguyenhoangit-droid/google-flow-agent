"""Headless polling for Flow video upscales.

Google Flow's upsampler returns workflow descriptors whose logical
``primaryMediaId`` may not appear in ``flow.projectInitialData``. The browser UI
can still resolve completed media through ``media.getMediaUrlRedirect``.

This module exposes a small active poller that treats a successful authenticated
media redirect as the completion signal, avoiding the legacy
``batchCheckAsyncVideoGenerationStatus`` and ``/v1/media/{id}`` paths.
"""

from __future__ import annotations

from urllib.parse import quote

from agent.config import USE_BATCH_RPC
from agent.services.flow_client import get_flow_client

_ALLOWED_MEDIA_URL_PREFIX = "https://flow-content.google/"


def _normalize_workflow(workflow: dict) -> dict | None:
    if not isinstance(workflow, dict):
        return None
    name = workflow.get("name")
    primary_media_id = workflow.get("primary_media_id")
    if not primary_media_id:
        metadata = workflow.get("metadata")
        if isinstance(metadata, dict):
            primary_media_id = metadata.get("primaryMediaId")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(primary_media_id, str) or not primary_media_id:
        return None
    return {"name": name, "primary_media_id": primary_media_id}


def extract_upscale_workflows(result: dict) -> list[dict]:
    if not isinstance(result, dict):
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    workflows = data.get("workflows", []) if isinstance(data, dict) else []
    normalized = []
    for workflow in workflows:
        item = _normalize_workflow(workflow)
        if item:
            normalized.append(item)
    return normalized


def annotate_upscale_polling(result: dict) -> dict:
    workflows = extract_upscale_workflows(result)
    if not workflows:
        return result
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if isinstance(data, dict):
        data["flowkitPolling"] = {
            "mode": "media_redirect",
            "workflows": workflows,
        }
    return result


async def _fetch_media_url(client, media_id: str) -> dict:
    if USE_BATCH_RPC:
        result = await client.get_media(media_id)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        video = data.get("video") if isinstance(data, dict) else None
        candidate = video.get("fifeUrl") if isinstance(video, dict) else None
        return {
            "status": result.get("status", 200),
            "data": {
                "url": candidate,
                "contentType": "video/mp4" if candidate else None,
            },
            "error": result.get("error"),
        }

    url = (
        "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect"
        f"?name={quote(media_id, safe='')}"
    )
    return await client._send(
        "trpc_request",
        {
            "url": url,
            "method": "GET",
            "headers": {"content-type": "application/json"},
            "responseMode": "url",
        },
        timeout=15,
    )


def _parse_media_redirect(response: dict) -> tuple[str | None, str | None, str | None]:
    if not isinstance(response, dict):
        return None, None, "Flow media redirect returned an invalid response"
    status = response.get("status")
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    candidate = data.get("url")
    content_type = data.get("contentType")
    if (
        isinstance(status, int)
        and status < 400
        and isinstance(candidate, str)
        and candidate.startswith(_ALLOWED_MEDIA_URL_PREFIX)
    ):
        return candidate, content_type if isinstance(content_type, str) else None, None
    error = response.get("error")
    if not error and isinstance(status, int) and status >= 400:
        error = f"API_{status}"
    if not error:
        error = "media redirect not ready"
    return None, content_type if isinstance(content_type, str) else None, str(error)


async def check_upscale_status(
    workflows: list[dict],
    include_encoded_video: bool = False,
) -> dict:
    """Poll native Flow Full HD/4K export workflows without buffering the MP4."""
    normalized = []
    for workflow in workflows or []:
        item = _normalize_workflow(workflow)
        if item:
            normalized.append(item)
    if not normalized:
        raise ValueError(
            "Export polling requires workflow descriptors with name and "
            "primary_media_id (or raw Flow metadata.primaryMediaId)"
        )

    client = get_flow_client()
    items = []
    for workflow in normalized:
        media_id = workflow["primary_media_id"]
        response = await _fetch_media_url(client, media_id)
        url, content_type, diagnostic = _parse_media_redirect(response)
        if url:
            media = {
                "media_id": media_id,
                "url": url,
                "encoded_video_available": False,
                "resolved_via": "as29s" if USE_BATCH_RPC else "media.getMediaUrlRedirect",
            }
            if content_type:
                media["content_type"] = content_type
            if include_encoded_video:
                media["encoded_video"] = None
            items.append({
                "name": workflow["name"],
                "primary_media_id": media_id,
                "done": True,
                "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                "error": None,
                "media": media,
            })
            continue

        probe = {}
        if isinstance(response, dict):
            if isinstance(response.get("status"), int):
                probe["http_status"] = response["status"]
            data = response.get("data")
            if isinstance(data, dict) and isinstance(data.get("url"), str):
                probe["resolved_url"] = data["url"]
        if diagnostic:
            probe["diagnostic"] = diagnostic
        item = {
            "name": workflow["name"],
            "primary_media_id": media_id,
            "done": False,
            "status": "PENDING",
            "error": None,
        }
        if probe:
            item["probe"] = probe
        items.append(item)

    all_done = bool(items) and all(item["done"] for item in items)
    return {
        "done": all_done,
        "status": "COMPLETED" if all_done else "PENDING",
        "workflows": items,
    }
