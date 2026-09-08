"""Explicit Full HD / 4K export endpoints for Google Flow videos."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.services.flow_client import get_flow_client
from agent.services.upscale_polling import annotate_upscale_polling, check_upscale_status

router = APIRouter(prefix="/flow", tags=["flow"])


class ExportVideoRequest(BaseModel):
    media_id: str
    scene_id: str = "export"
    quality: Literal["1080p", "4k"] = "1080p"
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    project_id: str | None = None


class CheckExportStatusRequest(BaseModel):
    workflows: list[dict]


@router.post("/export-video")
async def export_video(body: ExportVideoRequest):
    """Start Google's native Full HD/4K export.

    This is the same Flow upsample operation exposed by the UI, presented as an
    export/download-quality choice. 1080p is the default because Omni Flash's
    generated file is normally 720p and Full HD is the expected downloadable
    master.
    """
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    resolution = (
        "VIDEO_RESOLUTION_1080P"
        if body.quality == "1080p"
        else "VIDEO_RESOLUTION_4K"
    )
    result = await client.upscale_video(
        media_id=body.media_id,
        scene_id=body.scene_id,
        aspect_ratio=body.aspect_ratio,
        resolution=resolution,
        project_id=body.project_id,
    )
    if result.get("error") or (
        isinstance(result.get("status"), int) and result["status"] >= 400
    ):
        raise HTTPException(
            result.get("status", 502),
            result.get("error", result.get("data")),
        )

    annotated = annotate_upscale_polling(result)
    data = annotated.get("data", annotated)
    if isinstance(data, dict):
        data["export"] = {
            "quality": body.quality,
            "resolution": resolution,
            "native_flow_export": True,
            "next": "/api/flow/check-export-status",
        }
    return data


@router.post("/check-export-status")
@router.post("/check-upscale-status", include_in_schema=False)
async def check_export_status(body: CheckExportStatusRequest):
    """Return a signed downloadable URL when the native Flow export is ready."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    try:
        result = await check_upscale_status(body.workflows)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if result.get("status") == "COMPLETED":
        result["download_ready"] = True
    else:
        result["download_ready"] = False
    return result
