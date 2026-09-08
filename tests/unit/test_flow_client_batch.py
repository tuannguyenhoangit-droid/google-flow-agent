"""The batch path's answers, in the shapes the rest of the pipeline reads.

Everything downstream of FlowClient — the worker's parsers, the operation
poller, the scene writers — was written against the old REST responses. These
tests hold the adapter to that contract, so a transport swap stays invisible.
"""
import json

import pytest

from agent.services import flow_batch as fb
from agent.services.flow_client import FlowClient
from agent.worker._parsing import _extract_media_id, _extract_output_url, _is_error

PROJECT = "11111111-2222-3333-4444-555555555555"
MEDIA = "12345678-1234-1234-1234-1234567890ab"
OPERATION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
IMAGE_URL = f"https://{fb.MEDIA_HOST}/image/{MEDIA}?sig=x"
VIDEO_URL = f"https://{fb.MEDIA_HOST}/video/{MEDIA}?sig=x"


def envelope(rpcid: str, payload) -> str:
    chunk = json.dumps([["wrb.fr", rpcid, json.dumps(payload)]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


@pytest.fixture
def client(monkeypatch):
    """A FlowClient whose transport replays canned RPC responses.

    `calls` records what each rpc was asked, so a test can assert on the
    envelope as well as on what came back.
    """
    import agent.services.flow_client as module
    monkeypatch.setattr(module, "USE_BATCH_RPC", True)
    monkeypatch.setattr(module, "FLOW_PROJECT_ID", PROJECT)
    monkeypatch.setattr(module, "FLOW_ALLOW_DEGRADED", False)

    c = FlowClient()
    c.responses = {}
    c.calls = []

    async def fake_batch_rpc(rpcid, freq, captcha_action=None, match=None, timeout=300):
        c.calls.append({"rpcid": rpcid, "freq": freq,
                        "captcha": captcha_action, "match": match})
        canned = c.responses.get(rpcid, {"data": ""})
        return canned(match) if callable(canned) else canned

    c.batch_rpc = fake_batch_rpc
    return c


class TestGenerateImages:
    async def test_answers_in_the_shape_the_media_parser_reads(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        result = await client.generate_images("a cat", PROJECT)

        assert not _is_error(result)
        assert _extract_media_id(result, "GENERATE_IMAGE") == MEDIA
        assert _extract_output_url(result, "GENERATE_IMAGE") == IMAGE_URL

    async def test_asks_for_a_captcha(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.generate_images("a cat", PROJECT)
        assert client.calls[0]["captcha"] == fb.CAPTCHA_IMAGE

    async def test_character_refs_ride_in_the_reference_slot(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.generate_images("a cat", PROJECT, character_media_ids=["ref-a", "ref-b"])

        item = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])[1][0]
        assert item[2] == [["ref-a", None, None, None, fb.REF_TYPE_IMAGE],
                           ["ref-b", None, None, None, fb.REF_TYPE_IMAGE]]

    async def test_explicit_model_and_count_dispatch_as_ui_style_rpcs(self, client, monkeypatch):
        import agent.services.flow_client as module
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
        client.responses[fb.RPC_GEN_IMAGE] = {
            "data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])
        }
        result = await client.generate_images(
            "a cat", PROJECT, image_model="HARBOR_SEAL", count=2, seed=100,
        )
        assert len(client.calls) == 2
        items = [json.loads(json.loads(call["freq"])[0][0][1])[1] for call in client.calls]
        assert [len(group) for group in items] == [1, 1]
        assert [group[0][5] for group in items] == ["HARBOR_SEAL", "HARBOR_SEAL"]
        assert [group[0][3] for group in items] == [100, 100 + 9973]
        assert all(call["captcha"] == fb.CAPTCHA_IMAGE for call in client.calls)
        assert sleeps == [module.IMAGE_UI_SUBMIT_OFFSETS_S[1]]
        assert len(result["data"]["media"]) == 2

    async def test_count_four_uses_captured_ui_launch_offsets(self, client, monkeypatch):
        import agent.services.flow_client as module
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
        client.responses[fb.RPC_GEN_IMAGE] = {
            "data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])
        }
        result = await client.generate_images("a cat", PROJECT, count=4)
        assert len(client.calls) == 4
        assert sleeps == list(module.IMAGE_UI_SUBMIT_OFFSETS_S[1:4])
        assert len(result["data"]["media"]) == 4

    async def test_rpc_error_8_retries_once_after_cooldown(self, client, monkeypatch):
        import agent.services.flow_client as module

        attempts = 0
        sleeps = []

        async def fake_payload(rpcid, freq, captcha_action=None, timeout=300):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise fb.RpcError(fb.RPC_GEN_IMAGE, [8])
            return [[IMAGE_URL]]

        async def fake_sleep(delay):
            sleeps.append(delay)

        client._batch_payload = fake_payload
        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

        result = await client.generate_images("a cat", PROJECT, count=1)
        assert not _is_error(result)
        assert attempts == 2
        assert sleeps == [module.IMAGE_TRANSIENT_RETRY_DELAY_S]

    async def test_non_transient_rpc_error_is_not_retried(self, client, monkeypatch):
        import agent.services.flow_client as module

        attempts = 0
        sleeps = []

        async def fake_payload(rpcid, freq, captcha_action=None, timeout=300):
            nonlocal attempts
            attempts += 1
            raise fb.RpcError(fb.RPC_GEN_IMAGE, [5])

        async def fake_sleep(delay):
            sleeps.append(delay)

        client._batch_payload = fake_payload
        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

        result = await client.generate_images("a cat", PROJECT, count=1)
        assert _is_error(result)
        assert attempts == 1
        assert sleeps == []

    async def test_partial_batch_keeps_successes_and_reports_failed_variants(self, client, monkeypatch):
        import agent.services.flow_client as module

        async def fake_sleep(_delay):
            return None

        async def fake_payload(rpcid, freq, captcha_action=None, timeout=300):
            item = json.loads(json.loads(freq)[0][0][1])[1][0]
            if item[3] == 100 + 9973:
                raise fb.RpcError(fb.RPC_GEN_IMAGE, [5])
            return [[IMAGE_URL]]

        client._batch_payload = fake_payload
        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

        result = await client.generate_images("a cat", PROJECT, count=2, seed=100)
        assert not _is_error(result)
        data = result["data"]
        assert len(data["media"]) == 1
        assert data["requested_count"] == 2
        assert data["generated_count"] == 1
        assert data["complete"] is False
        assert data["failed_variants"][0]["index"] == 2
        assert "[5]" in data["failed_variants"][0]["error"]

    async def test_future_wire_model_is_not_silently_replaced(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.generate_images("a cat", PROJECT, image_model="FUTURE_BANANA_3")
        item = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])[1][0]
        assert item[5] == "FUTURE_BANANA_3"

    async def test_a_project_less_call_falls_back_to_the_pinned_project(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.generate_images("a cat", "0")

        item = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])[1][0]
        assert item[7][5] == PROJECT

    async def test_no_url_back_is_an_error_not_a_silent_success(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[]])}
        assert _is_error(await client.generate_images("a cat", PROJECT))

    async def test_a_transport_error_becomes_an_error_result(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"error": "CAPTCHA_FAILED: NO_FLOW_TAB"}
        result = await client.generate_images("a cat", PROJECT)
        assert _is_error(result) and "NO_FLOW_TAB" in result["error"]

    async def test_no_project_anywhere_is_a_named_failure(self, client, monkeypatch):
        import agent.services.flow_client as module
        monkeypatch.setattr(module, "FLOW_PROJECT_ID", "")
        result = await client.generate_images("a cat", "0")
        assert "NO_FLOW_PROJECT" in result["error"]


class TestEditImage:
    async def test_source_is_base_image_and_extra_inputs_are_references(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.edit_image("redraw", "src-1", PROJECT, character_media_ids=["ref-a"])

        item = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])[1][0]
        assert item[2] == [
            ["src-1", None, None, None, fb.BASE_TYPE_IMAGE],
            ["ref-a", None, None, None, fb.REF_TYPE_IMAGE],
        ]

    async def test_source_is_not_repeated_when_also_supplied_as_reference(self, client):
        client.responses[fb.RPC_GEN_IMAGE] = {"data": envelope(fb.RPC_GEN_IMAGE, [[IMAGE_URL]])}
        await client.edit_image("redraw", "src-1", PROJECT, character_media_ids=["src-1", "ref-a"])

        item = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])[1][0]
        assert [ref[0] for ref in item[2]] == ["src-1", "ref-a"]
        assert [ref[4] for ref in item[2]] == [fb.BASE_TYPE_IMAGE, fb.REF_TYPE_IMAGE]


class TestUpscaleImage:
    async def test_2k_upscale_uses_sprcad_and_returns_encoded_image(self, client):
        encoded = "A" * 200
        client.responses[fb.RPC_UPSCALE_IMAGE] = {
            "data": envelope(fb.RPC_UPSCALE_IMAGE, [["media-record"], encoded])
        }
        result = await client.upscale_image(MEDIA, PROJECT, "2K")
        assert result["data"]["encodedImage"] == encoded
        assert result["data"]["resolution"] == "2K"
        assert client.calls[0]["rpcid"] == fb.RPC_UPSCALE_IMAGE
        assert client.calls[0]["captcha"] == fb.CAPTCHA_IMAGE
        payload = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])
        assert payload[0] == MEDIA
        assert payload[1] == 1


class TestGenerateVideo:
    def _submitted(self, client):
        return {"data": envelope(fb.RPC_GEN_VIDEO, [None, 50, [[OPERATION, PROJECT, "scene", None]]])}

    async def test_returns_an_operation_the_poller_can_carry(self, client):
        client.responses[fb.RPC_GEN_VIDEO] = self._submitted(client)
        result = await client.generate_video("mid", "go", PROJECT, "scene-1")

        ops = result["data"]["operations"]
        assert ops[0]["operation"]["name"] == OPERATION
        assert ops[0]["status"] == "MEDIA_GENERATION_STATUS_PENDING"

    async def test_remembers_which_project_to_look_the_media_up_in(self, client):
        client.responses[fb.RPC_GEN_VIDEO] = self._submitted(client)
        await client.generate_video("mid", "go", PROJECT, "scene-1")
        assert client._operation_projects[OPERATION] == PROJECT

    async def test_chaining_fails_loudly_rather_than_dropping_the_end_frame(self, client):
        result = await client.generate_video("mid", "go", PROJECT, "scene-1",
                                             end_image_media_id="end-mid")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]
        assert not client.calls, "nothing should have been sent"

    async def test_degraded_mode_runs_i2v_off_the_start_frame(self, client, monkeypatch):
        import agent.services.flow_client as module
        monkeypatch.setattr(module, "FLOW_ALLOW_DEGRADED", True)
        client.responses[fb.RPC_GEN_VIDEO] = self._submitted(client)

        result = await client.generate_video("start-mid", "go", PROJECT, "scene-1",
                                             end_image_media_id="end-mid")
        assert not _is_error(result)
        payload = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])
        assert payload[0][0][4][1] == "start-mid"

    async def test_r2v_fails_loudly_by_default(self, client):
        result = await client.generate_video_from_references(["a", "b"], "go", PROJECT, "s")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]

    async def test_degraded_r2v_uses_the_first_reference_as_the_start_frame(self, client, monkeypatch):
        import agent.services.flow_client as module
        monkeypatch.setattr(module, "FLOW_ALLOW_DEGRADED", True)
        client.responses[fb.RPC_GEN_VIDEO] = self._submitted(client)

        await client.generate_video_from_references(["ref-a", "ref-b"], "go", PROJECT, "s")
        payload = json.loads(json.loads(client.calls[0]["freq"])[0][0][1])
        assert payload[0][0][4][1] == "ref-a"

    async def test_upscale_is_unported_and_has_no_fallback(self, client, monkeypatch):
        import agent.services.flow_client as module
        monkeypatch.setattr(module, "FLOW_ALLOW_DEGRADED", True)
        result = await client.upscale_video(MEDIA, "scene-1")
        assert "UNSUPPORTED_ON_BATCH_API" in result["error"]


class TestCheckVideoStatus:
    def _poll(self, status=None, complaint=None):
        detail = None
        if complaint:
            detail = [None] * 8 + [[fb.OUTCOME_COMPLAINT, [None, complaint]]]
        record = [OPERATION, PROJECT, "scene", status, None, detail]
        return {"data": envelope(fb.RPC_OPERATION, [None, 50, [record]])}

    def _listing(self, found=True):
        text = (f'["{OPERATION}",null,null,["t",1,2,null,null,"{MEDIA}"]' if found else "")
        return lambda match: {"data": text}

    async def _status(self, client):
        result = await client.check_video_status([{"operation": {"name": OPERATION}}])
        return result["data"]["operations"][0]

    async def test_successful_once_a_video_url_exists(self, client):
        client.responses[fb.RPC_OPERATION] = self._poll(status="CAE")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}

        op = await self._status(client)
        assert op["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        assert _extract_media_id({"data": {"operations": [op]}}, "GENERATE_VIDEO") == MEDIA
        assert _extract_output_url({"data": {"operations": [op]}}, "GENERATE_VIDEO") == VIDEO_URL

    async def test_a_media_id_with_only_a_poster_is_still_pending(self, client):
        """Downloading on the id alone would save a still picture."""
        client.responses[fb.RPC_OPERATION] = self._poll(status="CAE")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [IMAGE_URL])}

        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_PENDING"

    async def test_a_complaint_is_carried_but_does_not_fail_the_job(self, client):
        """Jobs report "Media not found." and still deliver a finished clip."""
        client.responses[fb.RPC_OPERATION] = self._poll(complaint="Media not found.")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing(found=False)

        op = await self._status(client)
        assert op["status"] == "MEDIA_GENERATION_STATUS_PENDING"
        assert op["complaint"] == "Media not found."

    async def test_the_listing_decides_even_when_the_poll_never_says_done(self, client):
        """The poll can sit at no status at all on a job that finished, so the
        listing is consulted on a schedule rather than only on the poll's say-so."""
        client.responses[fb.RPC_OPERATION] = self._poll(status=None)
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}

        await self._status(client)
        await self._status(client)
        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"

    async def test_the_listing_is_asked_for_a_window_not_the_whole_thing(self, client):
        client.responses[fb.RPC_OPERATION] = self._poll(status="CAE")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing(found=False)

        await self._status(client)
        listing = next(c for c in client.calls if c["rpcid"] == fb.RPC_PROJECT_MEDIA)
        assert listing["match"] == OPERATION

    async def test_an_unreadable_poll_still_consults_the_listing(self, client):
        """Old operations decay to a bare id but stay in the listing."""
        client.responses[fb.RPC_OPERATION] = {"error": "boom"}
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}

        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"

    async def test_a_quiet_poll_does_not_pay_for_the_listing_every_round(self, client):
        """The listing is a 17 MB call; a poll with nothing to report skips it."""
        client.responses[fb.RPC_OPERATION] = self._poll(status=None)
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}

        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_PENDING"
        assert not [c for c in client.calls if c["rpcid"] == fb.RPC_PROJECT_MEDIA]

        await self._status(client)
        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"

    async def test_a_known_media_id_is_not_looked_up_again(self, client):
        """Once the listing has answered, later rounds go straight to the media."""
        client.responses[fb.RPC_OPERATION] = self._poll(status="CAE")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [IMAGE_URL])}

        await self._status(client)          # poster only — still pending
        client.calls.clear()
        await self._status(client)
        assert not [c for c in client.calls if c["rpcid"] == fb.RPC_PROJECT_MEDIA]
        assert not [c for c in client.calls if c["rpcid"] == fb.RPC_OPERATION]

    async def test_a_finished_operation_stays_finished_when_re_polled(self, client):
        """A batch re-polls its finished operations alongside its pending ones."""
        client.responses[fb.RPC_OPERATION] = self._poll(status="CAE")
        client.responses[fb.RPC_PROJECT_MEDIA] = self._listing()
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}

        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        assert (await self._status(client))["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"

    async def test_a_nameless_operation_fails_instead_of_polling_forever(self, client):
        result = await client.check_video_status([{"operation": {}}])
        assert result["data"]["operations"][0]["status"] == "MEDIA_GENERATION_STATUS_FAILED"


class TestMediaAndUpload:
    async def test_get_media_reports_the_signed_urls(self, client):
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL, IMAGE_URL])}
        result = await client.get_media(MEDIA)
        assert result["status"] == 200
        assert result["data"]["video"]["fifeUrl"] == VIDEO_URL

    async def test_a_media_id_with_no_urls_reads_as_404(self, client):
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [])}
        assert (await client.get_media(MEDIA))["status"] == 404

    async def test_validate_media_id_follows_the_status(self, client):
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL])}
        assert await client.validate_media_id(MEDIA) is True
        client.responses[fb.RPC_MEDIA] = {"data": envelope(fb.RPC_MEDIA, [])}
        assert await client.validate_media_id(MEDIA) is False

    async def test_upload_returns_the_media_id_the_callers_look_for(self, client):
        client.responses[fb.RPC_UPLOAD_IMAGE] = {
            "data": envelope(fb.RPC_UPLOAD_IMAGE, [[MEDIA, PROJECT, OPERATION, "CAE"]])
        }
        result = await client.upload_image("Ym9keQ==", project_id=PROJECT)
        assert result["_mediaId"] == MEDIA
        assert result["data"]["media"]["name"] == MEDIA

    async def test_upload_carries_a_captcha_like_a_generate(self, client):
        client.responses[fb.RPC_UPLOAD_IMAGE] = {
            "data": envelope(fb.RPC_UPLOAD_IMAGE, [[MEDIA, PROJECT, OPERATION, "CAE"]])
        }
        await client.upload_image("Ym9keQ==", project_id=PROJECT)
        assert client.calls[0]["captcha"] == fb.CAPTCHA_IMAGE


class TestProjectAndCredits:
    async def test_create_project_hands_back_the_pinned_one(self, client):
        result = await client.create_project("My Film")
        assert result["data"]["projectId"] == PROJECT

    async def test_create_project_without_a_pin_explains_itself(self, client, monkeypatch):
        import agent.services.flow_client as module
        monkeypatch.setattr(module, "FLOW_PROJECT_ID", "")
        result = await client.create_project("My Film")
        assert "NO_FLOW_PROJECT" in result["error"]
        assert "FLOW_PROJECT_ID" in result["error"]

    async def test_credits_answers_the_configured_tier_rather_than_guessing(self, client):
        result = await client.get_credits()
        assert result["data"]["userPaygateTier"]
        assert not client.calls, "there is no credits rpc to call"


class TestRefreshProjectUrls:
    """Re-signing every stored media id — what `/fk-refresh-urls` runs."""

    @pytest.fixture
    def db(self, monkeypatch):
        """A stand-in for the crud layer, recording what got written."""
        from agent.db import crud

        state = {
            "videos": [{"id": "vid-1"}],
            "scenes": [{
                "id": "scene-1",
                "vertical_image_media_id": MEDIA,
                "vertical_video_media_id": "22222222-2222-2222-2222-222222222222",
                "horizontal_image_media_id": "CAMSnot-a-uuid",
            }],
            "characters": [{"id": "char-1", "media_id": "33333333-3333-3333-3333-333333333333"}],
            "writes": [],
        }

        async def list_videos(pid): return state["videos"]
        async def list_scenes(vid): return state["scenes"]
        async def get_project_characters(pid): return state["characters"]
        async def update_scene(sid, **kw): state["writes"].append(("scene", sid, kw))
        async def update_character(cid, **kw): state["writes"].append(("character", cid, kw))

        for name, fn in [("list_videos", list_videos), ("list_scenes", list_scenes),
                         ("get_project_characters", get_project_characters),
                         ("update_scene", update_scene), ("update_character", update_character)]:
            monkeypatch.setattr(crud, name, fn)
        return state

    async def test_writes_a_fresh_url_into_each_field_that_holds_the_id(self, client, db):
        def media(match):
            return {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL, IMAGE_URL])}
        client.responses[fb.RPC_MEDIA] = media

        result = await client.refresh_project_urls(PROJECT)

        assert result["found"] == 3, "the CAMS id is not a media id and is skipped"
        assert result["refreshed"] == 3
        written = {(table, tuple(kw)[0]) for table, _, kw in db["writes"]}
        assert written == {
            ("scene", "vertical_image_url"),
            ("scene", "vertical_video_url"),
            ("character", "reference_image_url"),
        }

    async def test_an_image_field_takes_the_image_url_not_the_video_one(self, client, db):
        client.responses[fb.RPC_MEDIA] = lambda m: {
            "data": envelope(fb.RPC_MEDIA, [VIDEO_URL, IMAGE_URL])}

        await client.refresh_project_urls(PROJECT)
        by_field = {tuple(kw)[0]: tuple(kw.values())[0] for _, _, kw in db["writes"]}
        assert by_field["vertical_image_url"] == IMAGE_URL
        assert by_field["vertical_video_url"] == VIDEO_URL

    async def test_one_dead_media_id_does_not_sink_the_rest(self, client, db):
        seen = []

        def media(match):
            seen.append(1)
            if len(seen) == 1:
                return {"error": "NOT_FOUND"}
            return {"data": envelope(fb.RPC_MEDIA, [VIDEO_URL, IMAGE_URL])}
        client.responses[fb.RPC_MEDIA] = media

        result = await client.refresh_project_urls(PROJECT)
        assert result["found"] == 3 and result["refreshed"] == 2
