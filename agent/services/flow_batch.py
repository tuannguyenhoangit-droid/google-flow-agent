"""Flow's batchexecute API — the transport Flow moved to in September 2026.

The old world was a REST call to ``aisandbox-pa.googleapis.com`` carrying a
``Bearer ya29.…`` the extension sniffed off the page. That token no longer
exists: the rewritten frontend on ``flow.google.com`` signs every call with the
session cookie plus a per-page ``at`` token, against a single batchexecute
endpoint. Nothing can be replayed from outside the browser — a generate call
also carries a **single-use** reCAPTCHA token, and a replayed one comes back
``PUBLIC_ERROR_UNUSUAL_ACTIVITY``.

So this module never touches the network. It builds request envelopes and reads
responses; issuing the request is the job of the Chrome extension, which runs
the payload inside the Flow tab (see ``FlowClient.batch_rpc``).

The wire format is Google's usual batchexecute:

    f.req = [[[rpcid, "<inner payload as a JSON string>", null, "generic"]]]

and the response is a ``)]}'`` sentinel followed by length-prefixed chunks of
``["wrb.fr", rpcid, "<payload as a JSON string>", …]`` envelopes.

Ported from postforge/bridges/flowgen/flow_batch.py — see its MIGRATION.md for
the traps behind each of the comments below.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

BATCH_PATH = "/_/AiSandboxAngularFrontend/data/batchexecute"
MEDIA_HOST = "flow-content.google"

RPC_GEN_IMAGE = "ogiZ0b"
RPC_GEN_VIDEO = "eb1hJf"
RPC_OPERATION = "jwpduf"
RPC_PROJECT_MEDIA = "Zzl0ze"
RPC_MEDIA = "as29s"
RPC_UPLOAD_IMAGE = "maseQ"

CAPTCHA_IMAGE = "IMAGE_GENERATION"
CAPTCHA_VIDEO = "VIDEO_GENERATION"

#: The extension substitutes a freshly minted reCAPTCHA token for this marker.
#: It has to be a placeholder rather than a real token because the mint has to
#: happen in the page, moments before the request leaves.
CAPTCHA_SLOT = "__CAPTCHA__"

#: Wire names this path accepts. Everything else is rejected outright by Flow.
#: ``GEM_PIX_2`` is Nano Banana Pro, ``NARWHAL`` is Banana 2. Flow Kit uses Pro
#: by default (see agent/models.json), which is also what the new path defaults
#: to; a caller that wants Banana 2 has to name it.
IMAGE_MODELS = {"GEM_PIX_2", "NARWHAL"}
IMAGE_MODEL = "GEM_PIX_2"

#: The nicknames models.json speaks, resolved to wire names.
IMAGE_MODEL_BY_NICKNAME = {"NANO_BANANA_PRO": "GEM_PIX_2", "NANO_BANANA_2": "NARWHAL"}

#: Image aspect ratios, measured by generating one of each and reading the
#: JPEG header. This slot was mistaken for a variant count at first — 1 means
#: square, which is why a `count=1` request looked like it was working.
ASPECT_SQUARE = 1           # 1024x1024
ASPECT_PORTRAIT = 2         # 768x1376  (9:16)
ASPECT_LANDSCAPE = 3        # 1376x768  (16:9)
ASPECT_PORTRAIT_4_3 = 4     # 896x1200  (3:4)
ASPECT_LANDSCAPE_4_3 = 5    # 1200x896  (4:3)

#: The names the REST payload used, so callers can keep speaking them.
ASPECT_BY_NAME = {
    "IMAGE_ASPECT_RATIO_SQUARE": ASPECT_SQUARE,
    "IMAGE_ASPECT_RATIO_PORTRAIT": ASPECT_PORTRAIT,
    "IMAGE_ASPECT_RATIO_LANDSCAPE": ASPECT_LANDSCAPE,
    "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE": ASPECT_PORTRAIT_4_3,
    "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": ASPECT_LANDSCAPE_4_3,
}

#: Video models this path accepts. The REST-era map was keyed by
#: [tier][quality][aspect] and carried `…_portrait` / `…_fl` / `…_relaxed`
#: variants; those are gone — aspect is its own slot now, and the suffixed
#: names are rejected.
VIDEO_MODEL = "veo_3_1_i2v_lite_low_priority"
VIDEO_MODELS = {
    "veo_3_1_i2v_lite_low_priority",
    "veo_3_1_i2v_lite",
    "veo_3_1_i2v_s_fast_ultra",
}

#: Video aspect, and note it does NOT share the image encoding: here 1 is
#: portrait, where for an image 1 is square. Measured by rendering one of each
#: from the same portrait still — 720x1280 against 1280x720.
VIDEO_ASPECT_PORTRAIT = 1
VIDEO_ASPECT_LANDSCAPE = 2

VIDEO_ASPECT_BY_NAME = {
    "VIDEO_ASPECT_RATIO_PORTRAIT": VIDEO_ASPECT_PORTRAIT,
    "VIDEO_ASPECT_RATIO_LANDSCAPE": VIDEO_ASPECT_LANDSCAPE,
}

#: `CAE` is the operation's terminal state. Anything else means still working.
STATUS_DONE = "CAE"

#: Outcome codes seen in the operation's status block. Code 4 carries a
#: message like "Media not found." — but it is NOT a verdict: jobs that report
#: it still finish, and the finished media shows up in the project listing
#: seconds later. Treat it as something to quote on a timeout, never as a
#: reason to stop waiting.
OUTCOME_OK = 3
OUTCOME_COMPLAINT = 4

#: Surface id the web client stamps on every call. Constant in every capture.
SURFACE_ID = 22

#: Crop box on the reference image, verbatim from the UI when nothing was
#: reframed by hand: a hair inside the edges, spanning 128/129 of the frame.
FULL_FRAME_CROP = [None, 0.0038759689922481244, 1, 0.9961240310077519]

#: A reference image, as the UI sends it: the media id FIRST and a type flag
#: four slots later. Probing never found this — the id sat in the wrong
#: position, the payload was accepted, and the picture quietly ignored it.
REF_TYPE_IMAGE = 1


class RpcError(RuntimeError):
    """A batchexecute envelope came back with an error slot instead of data."""

    def __init__(self, rpcid: str, detail: Any):
        super().__init__(f"{rpcid} failed: {detail!r}")
        self.rpcid = rpcid
        self.detail = detail


class FlowBatchError(RuntimeError):
    """The call succeeded but the payload did not hold what we came for."""


@dataclass(frozen=True)
class RpcResult:
    rpcid: str
    data: Any
    error: Any = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class GeneratedImage:
    media_id: str
    url: str


@dataclass(frozen=True)
class Operation:
    operation_id: str
    project_id: Optional[str]
    status: Optional[str]
    error: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def complained(self) -> bool:
        """The poll grumbled. Observed to be survivable — check the listing."""
        return self.error is not None


@dataclass(frozen=True)
class MediaUrls:
    media_id: str
    video: Optional[str] = None
    image: Optional[str] = None


# ── model / aspect resolvers ─────────────────────────────────────────────────

def resolve_image_model(key: Optional[str]) -> str:
    """Nickname or wire name in, wire name out; anything unknown coerces."""
    if isinstance(key, str):
        if key in IMAGE_MODEL_BY_NICKNAME:
            return IMAGE_MODEL_BY_NICKNAME[key]
        if key in IMAGE_MODELS:
            return key
    return IMAGE_MODEL


def resolve_video_model(key: Optional[str]) -> str:
    """Map a REST-era model key onto one the batch path accepts.

    The old keys encoded tier, quality, aspect and chaining in the name
    (``veo_3_1_i2v_s_fast_ultra_relaxed``, ``…_portrait``, ``…_fl``). Aspect
    and chaining are their own slots now and the suffixed names are rejected,
    so the tier/quality intent is all that survives: anything that asked for
    "ultra" gets the ultra model, anything else lands on the lite default.
    """
    if isinstance(key, str):
        if key in VIDEO_MODELS:
            return key
        if "ultra" in key:
            return "veo_3_1_i2v_s_fast_ultra"
        if "lite_low_priority" in key:
            return "veo_3_1_i2v_lite_low_priority"
        if "lite" in key:
            return "veo_3_1_i2v_lite"
    return VIDEO_MODEL


def resolve_aspect(aspect: Any) -> int:
    """Take either the wire value or the REST-era name."""
    if isinstance(aspect, int):
        return aspect
    try:
        return ASPECT_BY_NAME[aspect]
    except KeyError:
        raise ValueError(
            f"unknown aspect {aspect!r} — use one of {sorted(ASPECT_BY_NAME)} or 1-5"
        ) from None


def resolve_video_aspect(aspect: Any) -> int:
    if isinstance(aspect, int):
        if aspect not in (VIDEO_ASPECT_PORTRAIT, VIDEO_ASPECT_LANDSCAPE):
            # 3 is a perfectly good IMAGE aspect and a meaningless video one
            raise ValueError(f"video aspect must be 1 or 2, got {aspect}")
        return aspect
    try:
        return VIDEO_ASPECT_BY_NAME[aspect]
    except KeyError:
        raise ValueError(
            f"unknown video aspect {aspect!r} — use one of "
            f"{sorted(VIDEO_ASPECT_BY_NAME)} or 1-2"
        ) from None


# ── envelope codec ───────────────────────────────────────────────────────────

def build_envelope(rpcid: str, inner: Any) -> str:
    """Wrap an inner payload as the ``f.req`` string batchexecute expects."""
    return json.dumps(
        [[[rpcid, json.dumps(inner, separators=(",", ":"), ensure_ascii=False), None, "generic"]]],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def parse_envelope(text: str) -> list[RpcResult]:
    """Unwrap the `)]}'` sentinel and the length-prefixed chunks.

    The chunk lengths count characters, but a payload can disagree with them by
    a byte or two once escapes are involved, so the JSON is decoded by scanning
    rather than by trusting the prefix.
    """
    if not text:
        return []
    body = text.split("\n", 1)[1] if text.startswith(")]}'") else text
    decoder = json.JSONDecoder()
    results: list[RpcResult] = []
    index = 0
    while index < len(body):
        start = body.find("[", index)
        if start == -1:
            break
        try:
            chunk, consumed = decoder.raw_decode(body[start:])
        except json.JSONDecodeError:
            # step past this `[` and keep scanning — a chunk boundary landing
            # mid-token should not cost us the envelopes that follow it
            index = start + 1
            continue
        index = start + consumed
        for entry in chunk if isinstance(chunk, list) else []:
            if not isinstance(entry, list) or not entry or entry[0] != "wrb.fr":
                continue
            rpcid = entry[1] if len(entry) > 1 else "?"
            payload = entry[2] if len(entry) > 2 else None
            if payload is None:
                # index 5 is the error slot; it is `[5]`-style codes, not text
                results.append(RpcResult(rpcid, None, entry[5] if len(entry) > 5 else True))
                continue
            results.append(
                RpcResult(rpcid, json.loads(payload) if isinstance(payload, str) else payload)
            )
    return results


def first_payload(text: str, rpcid: str) -> Any:
    """The payload of the first matching envelope, or raise what went wrong."""
    results = parse_envelope(text)
    for result in results:
        if result.rpcid != rpcid:
            continue
        if not result.ok:
            raise RpcError(rpcid, result.error)
        return result.data
    raise FlowBatchError(f"no {rpcid} envelope in response ({len(results)} others)")


# ── request builders ─────────────────────────────────────────────────────────

def _client_uuid() -> str:
    """Client-side request ids. The UI sends them upper-case; match it."""
    return str(uuid.uuid4()).upper()


def _context(project_id: str) -> list:
    """The surface/project/captcha envelope every generate call repeats."""
    return [None, SURFACE_ID, None, None, None, project_id, None, None, None, None,
            [CAPTCHA_SLOT, 1]]


def _reference(media_id: str) -> list:
    return [media_id, None, None, None, REF_TYPE_IMAGE]


def image_request(prompt: str, project_id: str, count: int = 1,
                  aspect: Any = ASPECT_SQUARE, seed: Optional[int] = None,
                  prompts: Optional[list[str]] = None,
                  model: str = IMAGE_MODEL,
                  ref_media_ids: Optional[list[str]] = None) -> str:
    """One request item per variant, exactly as the REST payload did it.

    There is no "how many" field: Flow returns one image per item in the list,
    so `count` replicates the item under fresh seeds. `ref_media_ids` conditions
    the result on images already in the project — this is what keeps a character
    the same person from beat to beat.
    """
    ratio = resolve_aspect(aspect)
    base = seed if seed is not None else random.randint(1, 10**9)
    items = []
    for index in range(max(1, count)):
        text = prompts[index] if prompts and index < len(prompts) else prompt
        refs = [_reference(mid) for mid in (ref_media_ids or [])] or None
        items.append([None, None, refs, base + index * 9973, ratio, model, None,
                      _context(project_id), [[[text]]], None, None, None,
                      _client_uuid(), _client_uuid()])
    return build_envelope(RPC_GEN_IMAGE, [None, items, 1, _context(project_id),
                                          [_client_uuid()]])


def video_request(prompt: str, project_id: str, source_media_id: str,
                  crop: Optional[list] = None,
                  aspect: Any = VIDEO_ASPECT_LANDSCAPE,
                  model: str = VIDEO_MODEL) -> str:
    inner = [
        [[[None, None, [[[prompt]]]], model, resolve_video_aspect(aspect), None,
          [None, source_media_id, None, None, None,
           FULL_FRAME_CROP if crop is None else crop],
          [None, None, None, None, _client_uuid(), _client_uuid()]]],
        _context(project_id),
        [_client_uuid(), 2],
    ]
    return build_envelope(RPC_GEN_VIDEO, inner)


def upload_request(image_b64: str, project_id: str, mime_type: str = "image/jpeg",
                   file_name: str = "upload.jpg") -> str:
    """Put a local image into the project so it can be used as a reference.

    The bytes ride inside the RPC as plain base64 — no data: prefix, no separate
    upload endpoint — and the call carries a captcha like a generate does.
    """
    return build_envelope(RPC_UPLOAD_IMAGE, [
        _context(project_id), image_b64, mime_type, 1, None, None, None, None,
        file_name, None, _client_uuid(), _client_uuid(),
    ])


def operation_request(operation_id: str) -> str:
    return build_envelope(RPC_OPERATION, [None, None, [[operation_id]]])


def project_media_request(project_id: str) -> str:
    return build_envelope(RPC_PROJECT_MEDIA, [f"projects/{project_id}", None, None, None, [1]])


def media_request(media_id: str) -> str:
    return build_envelope(RPC_MEDIA, [media_id])


# ── response readers ─────────────────────────────────────────────────────────

def _walk_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def _walk_lists(node: Any):
    if isinstance(node, list):
        yield node
        for item in node:
            yield from _walk_lists(item)


def read_images(payload: Any) -> list[GeneratedImage]:
    """Signed CDN urls come back inline on the image call — one per variant.

    The media id is read out of the url path rather than from a fixed index:
    the url is the thing we actually need, and pairing them at the source keeps
    a reshuffled response from mismatching ids to pictures.
    """
    images: list[GeneratedImage] = []
    seen: set[str] = set()
    for text in _walk_strings(payload):
        if MEDIA_HOST + "/image/" not in text:
            continue
        media_id = text.split("/image/", 1)[1].split("?", 1)[0]
        if media_id in seen:
            continue
        seen.add(media_id)
        images.append(GeneratedImage(media_id=media_id, url=text))
    return images


def read_uploaded_media_id(payload: Any) -> str:
    """`[[mediaId, projectId, operationId, "CAE", …]]` — the id is the handle a
    later generate passes as a reference."""
    record = payload[0] if isinstance(payload, list) and payload else None
    media_id = record[0] if isinstance(record, list) and record else None
    if not isinstance(media_id, str) or not media_id:
        raise FlowBatchError("upload response carried no media id")
    return media_id


def read_operation(payload: Any) -> Operation:
    """`[null, 50, [[opId, projectId, sceneId, status, …]]]`.

    Note the third uuid is the **scene**, not the media. Reading it as a media
    id is what made every `as29s` lookup answer NOT_FOUND.
    """
    records = payload[2] if isinstance(payload, list) and len(payload) > 2 else None
    record = records[0] if isinstance(records, list) and records else None
    if not isinstance(record, list) or not record:
        raise FlowBatchError("operation payload carried no record")
    return Operation(
        operation_id=record[0],
        project_id=record[1] if len(record) > 1 else None,
        status=record[3] if len(record) > 3 else None,
        error=read_operation_error(record),
    )


def read_operation_error(record: list) -> Optional[str]:
    """The complaint attached to this operation, if it carries one.

    It hides in the detail block's status slot as
    ``[4, [null, "Media not found."], ["Media not found."]]``. Measured
    behaviour: an operation can report exactly that and still deliver a
    finished 8-second clip, so this is a diagnostic string and nothing more.
    """
    detail = record[5] if len(record) > 5 else None
    if not isinstance(detail, list) or len(detail) <= 8:
        return None
    block = detail[8]
    if not isinstance(block, list) or not block or block[0] != OUTCOME_COMPLAINT:
        return None
    for text in _walk_strings(block):
        return text
    return "operation failed without a message"


def find_media_id(payload: Any, operation_id: str) -> Optional[str]:
    """Look an operation up in the project listing and take its media id.

    Entries look like
    ``[opId, null, null, [title, created, null, null, mediaId, clientUuid, done], projectId]``.
    """
    for node in _walk_lists(payload):
        if len(node) < 4 or node[0] != operation_id:
            continue
        detail = node[3]
        if isinstance(detail, list) and len(detail) > 4 and isinstance(detail[4], str):
            return detail[4]
    return None


#: The media slot in a listing entry, matched straight off the wire: a title,
#: a timestamp pair, two nulls, then the media id. Escaped or not, both forms
#: appear depending on whether the text has been through a JSON decode.
_MEDIA_SLOT = re.compile(r'null,null,\\?"([0-9a-fA-F-]{36})\\?"')


def find_media_id_in_text(text: str, operation_id: str) -> Optional[str]:
    """Same lookup as :func:`find_media_id`, but on an unparsed listing.

    The project listing has no page size that shrinks it and grows with every
    generation, so it will outrun whatever response cap is in place — and a
    truncated tail cannot be JSON-decoded even though the entry we want is
    sitting in it intact. Scanning the text finds it anyway.
    """
    start = text.find(operation_id)
    if start == -1:
        return None
    match = _MEDIA_SLOT.search(text, start, start + 800)
    return match.group(1) if match else None


def read_media_urls(payload: Any, media_id: str) -> MediaUrls:
    video = image = None
    for text in _walk_strings(payload):
        if not text.startswith("https://"):
            continue
        if MEDIA_HOST + "/video/" in text and video is None:
            video = text
        elif MEDIA_HOST + "/image/" in text and image is None:
            image = text
    return MediaUrls(media_id=media_id, video=video, image=image)
