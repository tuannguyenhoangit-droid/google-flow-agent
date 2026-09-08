from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.services.upscale_polling as polling
from agent.services.upscale_polling import annotate_upscale_polling, check_upscale_status


MEDIA = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_annotate_upscale_polling_uses_workflow_primary_media_id():
    result = {
        "status": 200,
        "data": {
            "workflows": [
                {"name": "wf-1", "metadata": {"primaryMediaId": MEDIA + "_upsampled"}}
            ]
        },
    }
    annotated = annotate_upscale_polling(result)
    assert annotated["data"]["flowkitPolling"] == {
        "mode": "media_redirect",
        "workflows": [
            {"name": "wf-1", "primary_media_id": MEDIA + "_upsampled"}
        ],
    }


@pytest.mark.asyncio
async def test_batch_poll_resolves_completed_1080_media_through_as29s(monkeypatch):
    monkeypatch.setattr(polling, "USE_BATCH_RPC", True)
    client = MagicMock()
    client.get_media = AsyncMock(return_value={
        "status": 200,
        "data": {
            "video": {
                "fifeUrl": "https://flow-content.google/video/out?Signature=test"
            }
        },
    })
    with patch("agent.services.upscale_polling.get_flow_client", return_value=client):
        result = await check_upscale_status([
            {"name": "wf-1", "primary_media_id": MEDIA + "_upsampled"}
        ])

    assert result["done"] is True
    assert result["status"] == "COMPLETED"
    media = result["workflows"][0]["media"]
    assert media["resolved_via"] == "as29s"
    assert media["url"].startswith("https://flow-content.google/video/")
    client.get_media.assert_awaited_once_with(MEDIA + "_upsampled")


@pytest.mark.asyncio
async def test_batch_poll_stays_pending_until_video_url_exists(monkeypatch):
    monkeypatch.setattr(polling, "USE_BATCH_RPC", True)
    client = MagicMock()
    client.get_media = AsyncMock(return_value={"status": 200, "data": {"video": {}}})
    with patch("agent.services.upscale_polling.get_flow_client", return_value=client):
        result = await check_upscale_status([
            {"name": "wf-1", "primary_media_id": MEDIA + "_upsampled"}
        ])

    assert result["done"] is False
    assert result["status"] == "PENDING"
    assert result["workflows"][0]["status"] == "PENDING"
