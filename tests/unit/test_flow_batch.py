"""The batchexecute codec, builders and readers.

These lock down the slots that cost hours to find — see docs/CAPTURE.md and the
comments in flow_batch.py. A payload Flow accepts and then ignores looks exactly
like a payload that worked, so the assertions here are about position, not shape.
"""
import json

import pytest

from agent.services import flow_batch as fb


def envelope(rpcid: str, payload) -> str:
    """A response body as batchexecute serves it: sentinel, then chunks."""
    chunk = json.dumps([["wrb.fr", rpcid, json.dumps(payload)]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


def inner(freq: str):
    """The inner payload back out of an f.req envelope."""
    return json.loads(json.loads(freq)[0][0][1])


class TestEnvelopeCodec:
    def test_build_wraps_inner_as_a_json_string(self):
        freq = fb.build_envelope("rpc1", [1, "two"])
        assert json.loads(freq) == [[["rpc1", '[1,"two"]', None, "generic"]]]

    def test_parse_reads_a_payload_back(self):
        results = fb.parse_envelope(envelope("rpc1", {"a": 1}))
        assert [(r.rpcid, r.data) for r in results] == [("rpc1", {"a": 1})]

    def test_parse_survives_a_truncated_tail(self):
        """A response cut mid-chunk must not cost us the envelopes before it."""
        body = envelope("rpc1", {"a": 1}) + '\n50\n[["wrb.fr","rpc2","[1,2'
        results = fb.parse_envelope(body)
        assert [r.rpcid for r in results] == ["rpc1"]

    def test_parse_tolerates_a_missing_sentinel(self):
        chunk = json.dumps([["wrb.fr", "rpc1", '{"a":1}']])
        assert fb.parse_envelope(chunk)[0].data == {"a": 1}

    def test_empty_body_is_no_results_not_a_crash(self):
        assert fb.parse_envelope("") == []

    def test_error_slot_becomes_an_error_result(self):
        chunk = json.dumps([["wrb.fr", "rpc1", None, None, None, [8]]])
        result = fb.parse_envelope(f")]}}'\n{len(chunk)}\n{chunk}")[0]
        assert not result.ok and result.error == [8]

    def test_first_payload_raises_on_the_error_slot(self):
        chunk = json.dumps([["wrb.fr", "rpc1", None, None, None, [8]]])
        with pytest.raises(fb.RpcError):
            fb.first_payload(f")]}}'\n{len(chunk)}\n{chunk}", "rpc1")

    def test_first_payload_raises_when_the_rpc_is_absent(self):
        with pytest.raises(fb.FlowBatchError):
            fb.first_payload(envelope("other", [1]), "rpc1")


class TestImageRequest:
    PID = "11111111-2222-3333-4444-555555555555"

    def test_aspect_lands_in_slot_4_not_a_variant_count(self):
        """Slot 4 is the aspect ratio. `count=1` only looked right because
        1 means square."""
        item = inner(fb.image_request("a cat", self.PID,
                                      aspect="IMAGE_ASPECT_RATIO_LANDSCAPE"))[1][0]
        assert item[4] == fb.ASPECT_LANDSCAPE

    def test_count_repeats_the_item_under_fresh_seeds(self):
        items = inner(fb.image_request("a cat", self.PID, count=3, seed=100))[1]
        assert len(items) == 3
        assert [i[3] for i in items] == [100, 100 + 9973, 100 + 2 * 9973]

    def test_prompts_give_each_variant_its_own_text(self):
        items = inner(fb.image_request("fallback", self.PID, count=3,
                                       prompts=["one", "two"]))[1]
        assert [i[8][0][0][0] for i in items] == ["one", "two", "fallback"]

    def test_reference_puts_the_media_id_first_and_the_type_flag_fourth(self):
        """The arrangement probing never found: wrong ones are accepted and
        then quietly ignored."""
        item = inner(fb.image_request("a cat", self.PID, ref_media_ids=["mid-1"]))[1][0]
        assert item[2] == [["mid-1", None, None, None, fb.REF_TYPE_IMAGE]]

    def test_no_references_leaves_the_slot_null_rather_than_empty(self):
        assert inner(fb.image_request("a cat", self.PID))[1][0][2] is None

    def test_the_captcha_placeholder_is_present_for_the_extension_to_replace(self):
        assert fb.CAPTCHA_SLOT in fb.image_request("a cat", self.PID)

    def test_the_project_id_rides_in_the_context(self):
        assert inner(fb.image_request("a cat", self.PID))[1][0][7][5] == self.PID

    def test_the_model_is_named_in_slot_5(self):
        item = inner(fb.image_request("a cat", self.PID, model="NARWHAL"))[1][0]
        assert item[5] == "NARWHAL"


class TestVideoRequest:
    PID = "11111111-2222-3333-4444-555555555555"

    def test_video_aspect_does_not_share_the_image_encoding(self):
        """1 is portrait here; for an image 1 is square."""
        payload = inner(fb.video_request("go", self.PID, "mid",
                                         aspect="VIDEO_ASPECT_RATIO_PORTRAIT"))
        assert payload[0][0][2] == fb.VIDEO_ASPECT_PORTRAIT

    def test_an_image_aspect_is_refused_rather_than_rendered_wrong(self):
        with pytest.raises(ValueError):
            fb.video_request("go", self.PID, "mid", aspect=fb.ASPECT_LANDSCAPE)

    def test_the_source_media_id_and_a_full_frame_crop_travel_together(self):
        block = inner(fb.video_request("go", self.PID, "mid-9"))[0][0][4]
        assert block[1] == "mid-9"
        assert block[5] == fb.FULL_FRAME_CROP

    def test_a_hand_reframed_crop_overrides_the_default(self):
        crop = [None, 0.1, 1, 0.9]
        assert inner(fb.video_request("go", self.PID, "mid", crop=crop))[0][0][4][5] == crop


class TestReaders:
    OP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MID = "12345678-1234-1234-1234-1234567890ab"

    def test_images_are_read_out_of_the_url_path(self):
        url = f"https://{fb.MEDIA_HOST}/image/{self.MID}?sig=x"
        images = fb.read_images(["noise", [url, "more"]])
        assert images == [fb.GeneratedImage(media_id=self.MID, url=url)]

    def test_a_repeated_url_is_not_a_second_variant(self):
        url = f"https://{fb.MEDIA_HOST}/image/{self.MID}?sig=x"
        assert len(fb.read_images([url, url])) == 1

    def test_operation_reads_the_id_and_status(self):
        op = fb.read_operation([None, 50, [[self.OP, "proj", "scene", "CAE"]]])
        assert (op.operation_id, op.status, op.done) == (self.OP, "CAE", True)

    def test_the_third_uuid_is_the_scene_and_is_never_taken_as_media(self):
        """Feeding it to the media rpc answers NOT_FOUND forever."""
        record = [self.OP, "proj", "scene-uuid", "CAE"]
        op = fb.read_operation([None, 50, [record]])
        assert "scene-uuid" not in (op.operation_id, op.project_id, op.status)

    def test_a_complaint_is_carried_but_is_not_a_terminal_status(self):
        detail = [None] * 8 + [[fb.OUTCOME_COMPLAINT, [None, "Media not found."]]]
        op = fb.read_operation([None, 50, [[self.OP, "proj", "scene", None, None, detail]]])
        assert op.complained and op.error == "Media not found."
        assert not op.done

    def test_a_healthy_outcome_carries_no_complaint(self):
        detail = [None] * 8 + [[fb.OUTCOME_OK]]
        op = fb.read_operation([None, 50, [[self.OP, "p", "s", "CAE", None, detail]]])
        assert op.error is None

    def test_an_empty_operation_payload_raises(self):
        with pytest.raises(fb.FlowBatchError):
            fb.read_operation([None, 50, []])

    def test_the_media_id_is_found_in_an_unparsable_listing(self):
        """The listing outgrows any response cap; a truncated tail still holds
        the entry we came for."""
        text = f'["{self.OP}",null,null,["title",1,2,null,null,"{self.MID}"],"proj' 
        assert fb.find_media_id_in_text(text, self.OP) == self.MID

    def test_an_absent_operation_reads_as_not_there_yet(self):
        assert fb.find_media_id_in_text("nothing here", self.OP) is None

    def test_the_media_id_is_found_in_a_decoded_listing_too(self):
        payload = [[self.OP, None, None, ["t", 1, None, None, self.MID], "proj"]]
        assert fb.find_media_id(payload, self.OP) == self.MID

    def test_urls_are_split_by_kind(self):
        video = f"https://{fb.MEDIA_HOST}/video/{self.MID}?s=1"
        image = f"https://{fb.MEDIA_HOST}/image/{self.MID}?s=1"
        urls = fb.read_media_urls([image, video], self.MID)
        assert (urls.video, urls.image) == (video, image)

    def test_a_poster_only_record_has_no_video_yet(self):
        """A media id arrives before the clip is fetchable; downloading on the
        id alone saves a still picture."""
        image = f"https://{fb.MEDIA_HOST}/image/{self.MID}?s=1"
        assert fb.read_media_urls([image], self.MID).video is None

    def test_uploaded_media_id_is_the_first_slot(self):
        assert fb.read_uploaded_media_id([[self.MID, "proj", "op", "CAE"]]) == self.MID

    def test_an_upload_with_no_id_raises(self):
        with pytest.raises(fb.FlowBatchError):
            fb.read_uploaded_media_id([[]])


class TestResolvers:
    def test_rest_era_aspect_names_still_work(self):
        assert fb.resolve_aspect("IMAGE_ASPECT_RATIO_PORTRAIT") == fb.ASPECT_PORTRAIT

    def test_an_unknown_aspect_name_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError):
            fb.resolve_aspect("IMAGE_ASPECT_RATIO_CINEMA")

    def test_nicknames_resolve_to_wire_names(self):
        assert fb.resolve_image_model("NANO_BANANA_PRO") == "GEM_PIX_2"
        assert fb.resolve_image_model("NANO_BANANA_2") == "NARWHAL"

    def test_a_wire_name_passes_through(self):
        assert fb.resolve_image_model("NARWHAL") == "NARWHAL"

    def test_an_unknown_image_model_coerces_to_the_default(self):
        assert fb.resolve_image_model("SOMETHING_ELSE") == fb.IMAGE_MODEL

    @pytest.mark.parametrize("legacy,expected", [
        ("veo_3_1_i2v_s_fast_ultra_relaxed", "veo_3_1_i2v_s_fast_ultra"),
        ("veo_3_1_i2v_s_fast_portrait", fb.VIDEO_MODEL),
        ("veo_3_1_i2v_s_fast_fl", fb.VIDEO_MODEL),
        ("veo_3_1_r2v_fast_landscape_ultra_relaxed", "veo_3_1_i2v_s_fast_ultra"),
        ("veo_3_1_i2v_lite", "veo_3_1_i2v_lite"),
        (None, fb.VIDEO_MODEL),
    ])
    def test_rest_era_video_keys_fold_onto_accepted_names(self, legacy, expected):
        """Aspect and chaining are their own slots now; the suffixed names are
        rejected outright, so only the tier/quality intent survives."""
        assert fb.resolve_video_model(legacy) == expected

    def test_every_resolved_video_model_is_one_flow_accepts(self):
        for tier in ("PAYGATE_TIER_ONE", "PAYGATE_TIER_TWO"):
            for gen in ("frame_2_video", "start_end_frame_2_video", "reference_frame_2_video"):
                for aspect in ("VIDEO_ASPECT_RATIO_PORTRAIT", "VIDEO_ASPECT_RATIO_LANDSCAPE"):
                    from agent.config import VIDEO_MODELS
                    key = VIDEO_MODELS.get(tier, {}).get(gen, {}).get(aspect)
                    assert fb.resolve_video_model(key) in fb.VIDEO_MODELS
