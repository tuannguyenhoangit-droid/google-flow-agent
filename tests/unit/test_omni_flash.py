"""Unit tests for Gemini Omni Flash Flow submissions and workflow polling.

Omni speaks the pre-migration transports — the REST endpoints on aisandbox-pa
and the labs.google tRPC snapshot it polls through — so the wire contracts
asserted here are legacy-path contracts and the module is pinned to that path
for the file. What happens on the batch path is one test at the bottom.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.services.omni_flash as omni_flash
from agent.services.omni_flash import (
    OMNI_FLASH_MAX_REFERENCE_IMAGES,
    _fetch_media_url,
    _fetch_project_initial_data,
    _load_model_key,
    check_omni_flash_status,
    extract_omni_workflows,
    generate_omni_flash_first_frame_video,
    generate_omni_flash_first_last_video,
    generate_omni_flash_video,
)


@pytest.fixture(autouse=True)
def legacy_transport(monkeypatch):
    """Omni is only reachable on the pre-migration path; assert it there."""
    monkeypatch.setattr(omni_flash, "USE_BATCH_RPC", False)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (4, "abra_r2v_4s"),
        (6, "abra_r2v_6s"),
        (8, "abra_r2v_8s"),
        (10, "abra_r2v_10s"),
    ],
)
def test_omni_reference_duration_model_keys(duration, expected):
    assert _load_model_key(duration) == expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (4, "abra_i2v_4s"),
        (6, "abra_i2v_6s"),
        (8, "abra_i2v_8s"),
        (10, "abra_i2v_10s"),
    ],
)
def test_omni_first_frame_model_keys(duration, expected):
    assert _load_model_key(duration, mode="frame_to_video") == expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (4, "abra_i2v_4s"),
        (6, "abra_i2v_6s"),
        (8, "abra_i2v_8s"),
        (10, "abra_i2v_10s"),
    ],
)
def test_omni_first_last_model_keys_are_independently_configured(duration, expected):
    assert _load_model_key(duration, mode="start_end_frame_to_video") == expected


def test_invalid_duration_fails_before_submit():
    with pytest.raises(ValueError, match="duration 5s is unsupported"):
        _load_model_key(5)


def test_extract_omni_workflows_uses_primary_media_id():
    result = {
        "status": 200,
        "data": {
            "operations": [
                {
                    "operation": {"name": "operation-looking-handle"},
                    "status": "MEDIA_GENERATION_STATUS_PENDING",
                }
            ],
            "workflows": [
                {
                    "name": "workflow-1",
                    "metadata": {"primaryMediaId": "media-1"},
                }
            ],
        },
    }
    assert extract_omni_workflows(result) == [
        {"name": "workflow-1", "primary_media_id": "media-1"}
    ]


def _mock_submit_client():
    client = MagicMock()
    client._client_context.return_value = {
        "projectId": "project-1",
        "tool": "PINHOLE",
        "userPaygateTier": "PAYGATE_TIER_ONE",
        "recaptchaContext": {
            "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            "token": "",
        },
        "sessionId": ";old",
    }
    client._build_url.side_effect = lambda name: f"https://example.test/{name}"
    client._send = AsyncMock(
        return_value={
            "status": 200,
            "data": {
                "operations": [
                    {
                        "operation": {"name": "op-1"},
                        "status": "MEDIA_GENERATION_STATUS_PENDING",
                    }
                ],
                "workflows": [
                    {
                        "name": "workflow-1",
                        "metadata": {"primaryMediaId": "media-1"},
                    }
                ],
            },
        }
    )
    return client


@pytest.mark.asyncio
async def test_submit_builds_flow_omni_first_frame_request_and_poll_descriptor():
    client = _mock_submit_client()

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await generate_omni_flash_first_frame_video(
            start_image_media_id="start-1",
            prompt="Camera slowly pushes in",
            project_id="project-1",
            scene_id="scene-1",
            duration_s=10,
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            user_paygate_tier="PAYGATE_TIER_ONE",
            seed=321,
        )

    client._build_url.assert_called_once_with("generate_video")
    method, params = client._send.await_args.args[:2]
    assert method == "api_request"
    assert params["captchaAction"] == "VIDEO_GENERATION"

    body = params["body"]
    assert body["useV2ModelConfig"] is True
    assert set(body["mediaGenerationContext"]) == {"batchId"}
    request = body["requests"][0]
    assert request["videoModelKey"] == "abra_i2v_10s"
    assert request["startImage"] == {"mediaId": "start-1"}
    assert "endImage" not in request
    assert request["seed"] == 321
    assert result["data"]["flowkitPolling"]["mode"] == "project_media"
    assert result["data"]["flowkitPolling"]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_submit_builds_flow_omni_first_last_request():
    client = _mock_submit_client()

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await generate_omni_flash_first_last_video(
            start_image_media_id="start-1",
            end_image_media_id="end-1",
            prompt="Move naturally from the first composition to the final composition",
            project_id="project-1",
            scene_id="scene-1",
            duration_s=8,
            aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT",
            user_paygate_tier="PAYGATE_TIER_ONE",
            seed=456,
        )

    client._build_url.assert_called_once_with("generate_video_start_end")
    method, params = client._send.await_args.args[:2]
    assert method == "api_request"
    body = params["body"]
    request = body["requests"][0]
    assert request["videoModelKey"] == "abra_i2v_8s"
    assert request["startImage"] == {"mediaId": "start-1"}
    assert request["endImage"] == {"mediaId": "end-1"}
    assert request["aspectRatio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"
    assert request["seed"] == 456
    assert result["data"]["flowkitPolling"]["workflows"] == [
        {
            "name": "workflow-1",
            "primary_media_id": "media-1",
            "project_id": "project-1",
        }
    ]


@pytest.mark.asyncio
async def test_first_frame_rejects_missing_start_before_submit():
    with pytest.raises(ValueError, match="requires start_image_media_id"):
        await generate_omni_flash_first_frame_video(
            start_image_media_id="",
            prompt="test",
            project_id="p",
        )


@pytest.mark.asyncio
async def test_first_last_rejects_missing_end_before_submit():
    with pytest.raises(ValueError, match="non-empty end_image_media_id"):
        await generate_omni_flash_first_last_video(
            start_image_media_id="start",
            end_image_media_id="",
            prompt="test",
            project_id="p",
        )


@pytest.mark.asyncio
async def test_submit_builds_flow_omni_r2v_request_and_poll_descriptor():
    client = _mock_submit_client()

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await generate_omni_flash_video(
            reference_media_ids=["ref-1", "ref-2"],
            prompt="Two friends talking in a cafe",
            project_id="project-1",
            scene_id="scene-1",
            duration_s=10,
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            user_paygate_tier="PAYGATE_TIER_ONE",
            seed=123,
        )

    assert result["status"] == 200
    assert result["data"]["flowkitPolling"] == {
        "mode": "project_media",
        "project_id": "project-1",
        "workflows": [
            {
                "name": "workflow-1",
                "primary_media_id": "media-1",
                "project_id": "project-1",
            }
        ],
    }
    client._build_url.assert_called_once_with("generate_video_references")
    client._send.assert_awaited_once()

    method, params = client._send.await_args.args[:2]
    assert method == "api_request"
    assert params["captchaAction"] == "VIDEO_GENERATION"

    body = params["body"]
    assert body["useV2ModelConfig"] is True
    assert body["mediaGenerationContext"]["audioFailurePreference"] == "BLOCK_SILENCED_VIDEOS"
    assert body["clientContext"]["projectId"] == "project-1"

    request = body["requests"][0]
    assert request["videoModelKey"] == "abra_r2v_10s"
    assert request["aspectRatio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    assert request["seed"] == 123
    assert request["metadata"] == {"sceneId": "scene-1"}
    assert request["referenceImages"] == [
        {"mediaId": "ref-1", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"},
        {"mediaId": "ref-2", "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"},
    ]


def _project_response(generation_status="MEDIA_GENERATION_STATUS_PENDING", include_media=True):
    media = []
    if include_media:
        media.append(
            {
                "name": "media-1",
                "projectId": "project-1",
                "workflowId": "workflow-1",
                "mediaMetadata": {
                    "mediaStatus": {"mediaGenerationStatus": generation_status}
                },
                "video": {"generatedVideo": {"model": "abra_r2v_4s"}},
            }
        )
    return {
        "status": 200,
        "data": {
            "result": {
                "data": {
                    "json": {
                        "projectContents": {
                            "workflows": [
                                {
                                    "name": "workflow-1",
                                    "projectId": "project-1",
                                    "metadata": {"primaryMediaId": "media-1"},
                                }
                            ],
                            "media": media,
                        }
                    }
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_project_poll_fetch_matches_live_flow_trpc_contract():
    client = MagicMock()
    client._send = AsyncMock(return_value={"status": 200, "data": {}})

    await _fetch_project_initial_data(client, "project-1")

    client._send.assert_awaited_once_with(
        "trpc_request",
        {
            "url": (
                "https://labs.google/fx/api/trpc/flow.projectInitialData?input="
                "%7B%22json%22%3A%7B%22projectId%22%3A%22project-1%22%7D%7D"
            ),
            "method": "GET",
            "headers": {"content-type": "application/json"},
        },
        timeout=15,
    )


@pytest.mark.asyncio
async def test_media_redirect_fetch_requests_url_only_mode():
    client = MagicMock()
    client._send = AsyncMock(return_value={"status": 200, "data": {}})

    await _fetch_media_url(client, "media-1")

    client._send.assert_awaited_once_with(
        "trpc_request",
        {
            "url": (
                "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect"
                "?name=media-1"
            ),
            "method": "GET",
            "headers": {"content-type": "application/json"},
            "responseMode": "url",
        },
        timeout=15,
    )


@pytest.mark.asyncio
async def test_omni_poll_pending_uses_project_snapshot_not_legacy_transports():
    client = MagicMock()
    client._send = AsyncMock(return_value=_project_response())
    client.get_media = AsyncMock(side_effect=AssertionError("legacy get_media forbidden"))
    client.check_video_status = AsyncMock(side_effect=AssertionError("operation poll forbidden"))

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await check_omni_flash_status(
            [
                {
                    "name": "workflow-1",
                    "primary_media_id": "media-1",
                    "project_id": "project-1",
                }
            ]
        )

    client.get_media.assert_not_awaited()
    client.check_video_status.assert_not_awaited()
    client._send.assert_awaited_once()
    assert result["done"] is False
    assert result["status"] == "PENDING"
    assert result["workflows"][0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_omni_poll_completed_returns_signed_url_without_buffering_video():
    client = MagicMock()
    client._send = AsyncMock(
        side_effect=[
            _project_response("MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            {
                "status": 200,
                "data": {
                    "url": "https://flow-content.google/video/media-1?Signature=test"
                },
            },
        ]
    )

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await check_omni_flash_status(
            [{"name": "workflow-1", "primary_media_id": "media-1"}],
            project_id="project-1",
        )

    assert result["done"] is True
    assert result["status"] == "COMPLETED"
    item = result["workflows"][0]
    assert item["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
    assert item["media"]["media_id"] == "media-1"
    assert item["media"]["url"].startswith("https://flow-content.google/video/")
    assert item["media"]["encoded_video_available"] is False
    assert "encoded_video" not in item["media"]


@pytest.mark.asyncio
async def test_omni_poll_missing_media_is_pending():
    client = MagicMock()
    client._send = AsyncMock(return_value=_project_response(include_media=False))

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        result = await check_omni_flash_status(
            [{"name": "workflow-1", "primary_media_id": "media-1"}],
            project_id="project-1",
        )

    assert result["done"] is False
    assert result["workflows"][0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_omni_poll_requires_project_id_for_legacy_descriptors():
    with pytest.raises(ValueError, match="requires project_id"):
        await check_omni_flash_status(
            [{"name": "workflow-1", "primary_media_id": "media-1"}]
        )


@pytest.mark.asyncio
async def test_submit_accepts_seven_references():
    client = MagicMock()
    client._client_context.return_value = {"projectId": "p"}
    client._build_url.return_value = "https://example.test/omni"
    client._send = AsyncMock(return_value={"status": 200, "data": {}})
    refs = [f"ref-{i}" for i in range(OMNI_FLASH_MAX_REFERENCE_IMAGES)]

    with patch("agent.services.omni_flash.get_flow_client", return_value=client):
        await generate_omni_flash_video(
            reference_media_ids=refs,
            prompt="test",
            project_id="p",
            duration_s=4,
        )

    request = client._send.await_args.args[1]["body"]["requests"][0]
    assert len(request["referenceImages"]) == 7


@pytest.mark.asyncio
async def test_submit_rejects_more_than_seven_references():
    refs = [f"ref-{i}" for i in range(OMNI_FLASH_MAX_REFERENCE_IMAGES + 1)]

    with pytest.raises(ValueError, match="at most 7 reference images"):
        await generate_omni_flash_video(
            reference_media_ids=refs,
            prompt="test",
            project_id="p",
            duration_s=8,
        )


@pytest.mark.asyncio
async def test_submit_rejects_empty_reference_set():
    with pytest.raises(ValueError, match="requires at least one reference image"):
        await generate_omni_flash_video(
            reference_media_ids=[],
            prompt="test",
            project_id="p",
            duration_s=8,
        )


class TestBatchPathIsRefusedRatherThanAttempted:
    """Flow stopped minting the bearer these endpoints need, and no Omni
    payload has been captured off the new frontend. Saying so beats a 401
    five retries deep."""

    @pytest.fixture(autouse=True)
    def batch_transport(self, monkeypatch):
        monkeypatch.setattr(omni_flash, "USE_BATCH_RPC", True)

    @pytest.fixture
    def client(self):
        with patch("agent.services.omni_flash.get_flow_client") as factory:
            stub = MagicMock()
            stub._send = AsyncMock()
            factory.return_value = stub
            yield stub

    async def test_first_frame_names_the_gap_and_sends_nothing(self, client):
        result = await generate_omni_flash_first_frame_video(
            start_image_media_id="mid", prompt="go", project_id="pid")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]
        client._send.assert_not_called()

    async def test_first_last_names_the_gap_and_sends_nothing(self, client):
        result = await generate_omni_flash_first_last_video(
            start_image_media_id="a", end_image_media_id="b",
            prompt="go", project_id="pid")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]
        client._send.assert_not_called()

    async def test_reference_to_video_names_the_gap_and_sends_nothing(self, client):
        result = await generate_omni_flash_video(
            reference_media_ids=["a"], prompt="go", project_id="pid")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]
        client._send.assert_not_called()

    async def test_polling_names_the_gap_and_sends_nothing(self, client):
        result = await check_omni_flash_status(
            [{"name": "wf", "primary_media_id": "mid", "project_id": "pid"}])
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]
        client._send.assert_not_called()

    async def test_the_message_points_at_both_ways_out(self, client):
        result = await generate_omni_flash_video(
            reference_media_ids=["a"], prompt="go", project_id="pid")
        assert "docs/CAPTURE.md" in result["error"]
        assert "USE_BATCH_RPC=0" in result["error"]
