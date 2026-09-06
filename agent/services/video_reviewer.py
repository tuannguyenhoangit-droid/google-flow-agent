"""Video review engine — frame extraction + Claude Vision analysis.

Two analysis backends:
  1. Claude CLI subprocess (default) — no API key needed, uses contact sheet
  2. Anthropic SDK (if ANTHROPIC_API_KEY set) — direct API, individual frames
"""
import asyncio
import base64
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path

import ssl

import aiohttp
import certifi

from agent.config import ANTHROPIC_API_KEY, REVIEW_MODEL, REVIEW_FPS_LIGHT, REVIEW_FPS_DEEP, REVIEW_MAX_FRAMES
from agent.db.crud import list_scenes, get_project_characters
from agent.models.review import DimensionScores, SceneReview, SegmentScore, VideoError, VideoReview

logger = logging.getLogger(__name__)


# ─── Scoring helpers ─────────────────────────────────────────

_WEIGHTS = {
    "character_consistency": 0.25,
    "prompt_adherence": 0.20,
    "motion_quality": 0.20,
    "visual_fidelity": 0.15,
    "temporal_coherence": 0.10,
    "composition": 0.10,
}


def _compute_overall(dims: dict) -> float:
    return round(sum(dims[k] * w for k, w in _WEIGHTS.items()), 2)


def _verdict(score: float) -> str:
    if score >= 9.0:
        return "excellent"
    if score >= 7.5:
        return "good"
    if score >= 6.0:
        return "acceptable"
    if score >= 4.0:
        return "poor"
    return "unusable"


def _fix_guide(dims: dict, errors: list) -> str:
    """Generate fix guide based on lowest dimension and critical error patterns."""
    critical_types = set()
    for err in errors:
        if err.severity == "CRITICAL":
            desc = err.description.lower()
            if "drift" in desc or "morph" in desc or "limb" in desc or "breed" in desc:
                critical_types.add("drift")
            if "swap" in desc or "wrong character" in desc:
                critical_types.add("breed_swap")
            if "count" in desc or "number of character" in desc:
                critical_types.add("count")
            if "logo" in desc or "brand" in desc:
                critical_types.add("logo")
            if "role" in desc or "wrong action" in desc:
                critical_types.add("role")
        elif err.severity == "HIGH":
            desc = err.description.lower()
            if "reverse" in desc:
                critical_types.add("reverse")

    if critical_types:
        hints = []
        if "drift" in critical_types:
            hints.append("simplify prompt, add 'steady camera, minimal movement'")
        if "breed_swap" in critical_types:
            hints.append("use stronger color contrast between characters")
        if "count" in critical_types:
            hints.append("make ONE character dominant, others in background")
        if "logo" in critical_types:
            hints.append("add 'no brand logos, no text' to prompt")
        if "role" in critical_types:
            hints.append("rewrite prompt to clarify which character does which action")
        if "reverse" in critical_types:
            hints.append("regenerate video (reverse motion is random, retry may fix)")
        return "REGENERATE_IMAGE then GENERATE_VIDEO: " + "; ".join(hints)

    lowest = min(dims, key=dims.get)
    guides = {
        "character_consistency": "Check character references, consider EDIT_IMAGE with closer framing",
        "prompt_adherence": "Rewrite scene prompt to be more specific, then REGENERATE_IMAGE",
        "motion_quality": "Regenerate video (motion artifacts are random, retry may fix)",
        "visual_fidelity": "Consider UPSCALE_VIDEO or REGENERATE_IMAGE with better lighting",
        "temporal_coherence": "Regenerate video, check scene lighting consistency",
        "composition": "Edit video_prompt camera directions, then regenerate",
    }
    return guides[lowest]


# ─── Frame extraction ─────────────────────────────────────────

_ssl_ctx = ssl.create_default_context(cafile=certifi.where())


class _URLExpiredError(Exception):
    """Raised when a GCS signed URL returns 400 (expired)."""


async def _download_video(url: str, dest: Path) -> None:
    """Download video from URL to local path. Raises _URLExpiredError on 400."""
    conn = aiohttp.TCPConnector(ssl=_ssl_ctx)
    async with aiohttp.ClientSession(connector=conn) as session:
        async with session.get(url) as resp:
            if resp.status == 400:
                raise _URLExpiredError(f"URL expired (400): {url[:80]}")
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)


async def _download_via_get_media(media_id: str, dest: Path) -> None:
    """Re-fetch a clip through the media record when its stored url has expired.

    The two transports answer differently: the batch path hands back a freshly
    signed url to download, the legacy REST path inlined the bytes as base64.
    Try the url first, fall back to the encoded content.
    """
    from agent.services.flow_client import get_flow_client

    client = get_flow_client()
    result = await client.get_media(media_id)
    if result.get("error"):
        raise ValueError(f"get_media failed for {media_id}: {result['error']}")

    data = result.get("data", result)
    if not isinstance(data, dict):
        raise ValueError(f"Unreadable get_media response for {media_id}")

    video = data.get("video") if isinstance(data.get("video"), dict) else {}
    image = data.get("image") if isinstance(data.get("image"), dict) else {}

    url = video.get("fifeUrl") or data.get("fifeUrl")
    if url:
        await _download_video(url, dest)
        logger.info("Downloaded %s via a freshly signed url", media_id[:12])
        return

    encoded = video.get("encodedVideo") or image.get("encodedImage") or data.get("encodedVideo")
    if not encoded:
        raise ValueError(f"No video url or encoded content in get_media response for {media_id}")

    video_bytes = base64.standard_b64decode(encoded)
    with open(dest, "wb") as f:
        f.write(video_bytes)
    logger.info("Downloaded %s via get_media (%d bytes)", media_id[:12], len(video_bytes))


def _extract_frames(video_path: str, fps: float, out_dir: str) -> list:
    """Extract frames as JPEGs using ffmpeg. Returns sorted list of frame paths."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps},scale=640:-1",
        "-q:v", "4",
        f"{out_dir}/frame_%04d.jpg",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr[-500:]}")
    return sorted(Path(out_dir).glob("frame_*.jpg"))


def _frame_to_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def _create_contact_sheet(video_path: str, fps: float, out_dir: str, cols: int = 8) -> tuple[Path, int]:
    """Create a single contact sheet grid image with timestamps. Returns (path, frame_count)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    total_frames = int(duration * fps)
    rows = math.ceil(total_frames / cols)
    output = Path(out_dir) / "contact_sheet.jpg"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", (
            f"fps={fps},"
            f"scale=320:-1,"
            f"drawtext=text='%{{pts\\:hms}}':x=5:y=5:fontsize=14:"
            f"fontcolor=white:borderw=1:bordercolor=black,"
            f"tile={cols}x{rows}"
        ),
        "-q:v", "2", str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Contact sheet failed: {result.stderr[-500:]}")
    return output, total_frames


# ─── Claude Vision analysis ───────────────────────────────────

_VISION_PROMPT = """\
You are an expert AI video quality reviewer analyzing {n_frames} frames at {fps}fps from an 8-second AI-generated video.

SCENE IMAGE PROMPT: {prompt}
SCENE VIDEO PROMPT: {video_prompt}
EXPECTED CHARACTERS: {character_names}

== SCORING DIMENSIONS (0.0-10.0 each) ==
1. character_consistency - Do characters match references? Stable species/breed/limb count/clothing?
2. prompt_adherence - Does video match prompt? Correct characters, actions, roles?
3. motion_quality - Smooth motion? No jitter/reverse motion/teleportation?
4. visual_fidelity - Clear resolution? No artifacts/blur/brand logos?
5. temporal_coherence - Consistent lighting/shadows/background/scale across frames?
6. composition - Framing matches camera directions? Good depth/balance?

== ERROR DETECTION RUBRIC ==
Classify each error by severity tier:

CRITICAL (auto-score affected dimension 0-3):
1. Character Drift -- character morphs mid-video (extra limbs, breed changes, bipedal to quadruped). Very common after 3-4s.
2. Breed Swap -- similar characters get swapped (Doberman to Rottweiler, wrong character in wrong role).
3. Role Reversal -- wrong character performs the action (villain wins instead of hero). ~50% of fight scenes.
4. Brand Logo -- AI generates real brand logos (FENDI, Gucci, Nike, etc.). Legal liability.
5. Character Count -- rendered count differs from requested count.

HIGH (score affected dimension 4-6):
6. Camera Drift -- sudden unwanted zoom, rotation, or angle shift. ~60% of videos after 4s.
7. Object Morph -- held items change shape (envelope becomes clutch, phone becomes tablet).
8. Reverse Motion -- character does action then undoes it (steps forward then back). ~30%.
9. Human Hands -- anthropomorphic/animal characters get human hands or fingers.
10. Scale Break -- characters suddenly giant or tiny relative to environment.

MINOR (score affected dimension 7-8, still acceptable):
11. Prop Count -- small props change count (3 candles become 4).
12. Clothing Detail -- texture shift (matte becomes glossy).
13. Background Blur -- signage becomes garbled text.
14. Accessory Change -- small accessories appear/disappear.

== INSTRUCTIONS ==
- For each error found, include: severity, time_range (e.g. "3s-5s"), and description.
- Identify usable_segments: continuous segments free of CRITICAL or HIGH errors.
- If ANY CRITICAL error is present, character_consistency must be 3.0 or below.

Return ONLY valid JSON (no markdown):
{{
  "dimensions": {{"character_consistency": N, "prompt_adherence": N, "motion_quality": N, "visual_fidelity": N, "temporal_coherence": N, "composition": N}},
  "errors": [
    {{"severity": "CRITICAL|HIGH|MINOR", "time_range": "Xs-Ys", "description": "what happened"}},
    ...
  ],
  "usable_segments": [{{"time_range": "Xs-Ys", "score": N}}, ...]
}}"""


def _parse_character_names(scene: dict) -> list[str]:
    names = scene.get("character_names")
    if not names:
        return []
    try:
        return json.loads(names) if isinstance(names, str) else list(names)
    except (json.JSONDecodeError, TypeError):
        return []


def _build_prompt(n_frames: int, fps: float, scene: dict) -> str:
    return _VISION_PROMPT.format(
        n_frames=n_frames,
        fps=fps,
        prompt=scene.get("prompt") or "",
        video_prompt=scene.get("video_prompt") or "",
        character_names=", ".join(_parse_character_names(scene)) or "none specified",
    )


def _parse_json_response(raw: str) -> dict:
    """Extract JSON from a response that may contain markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    # Also try to find JSON object in free text
    raw = raw.strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        if start >= 0:
            raw = raw[start:]
    return json.loads(raw)


# ─── Backend 1: Claude CLI (default, no API key needed) ──────

async def _analyze_cli(
    contact_sheet: Path,
    n_frames: int,
    fps: float,
    scene: dict,
) -> dict:
    """Analyze contact sheet via claude CLI subprocess."""
    prompt = _build_prompt(n_frames, fps, scene)
    full_prompt = (
        f"Read the image at {contact_sheet}. "
        f"It is a contact sheet of {n_frames} video frames at {fps}fps with timestamps.\n\n"
        f"{prompt}"
    )
    logger.info("Calling claude CLI for vision analysis (%d frames)", n_frames)
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", full_prompt,
        "--allowedTools", "Read",
        "--output-format", "text",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {stderr.decode()[-500:]}")
    return _parse_json_response(stdout.decode())


# ─── Backend 2: Anthropic SDK (when API key is set) ──────────

async def _analyze_sdk(
    frames: list,
    fps: float,
    scene: dict,
    characters: list,
) -> dict:
    """Send individual frames to Claude Vision via Anthropic SDK."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    character_names = _parse_character_names(scene)
    prompt_text = _build_prompt(len(frames), fps, scene)

    content = []
    for char in characters:
        slug = char.get("slug") or ""
        name = char.get("name", "")
        if char.get("reference_image_url") and ((slug and slug in character_names) or (name and name in character_names)):
            content.append({"type": "text", "text": f"Character reference -- {char['name']}:"})
            content.append({"type": "image", "source": {"type": "url", "url": char["reference_image_url"]}})
    if content:
        content.append({"type": "text", "text": "Video frames to analyze:"})

    for frame_path in frames:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": _frame_to_base64(frame_path)},
        })
    content.append({"type": "text", "text": prompt_text})

    response = await client.messages.create(
        model=REVIEW_MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_json_response(response.content[0].text)


# ─── Public API ───────────────────────────────────────────────

async def review_scene_video(
    scene: dict,
    characters: list,
    mode: str = "light",
    orientation: str = "VERTICAL",
    project_id: str = None,
) -> SceneReview:
    """Review a single scene's video via frame extraction + Claude Vision."""
    fps = REVIEW_FPS_DEEP if mode == "deep" else REVIEW_FPS_LIGHT

    orient_prefix = "vertical" if orientation.upper() == "VERTICAL" else "horizontal"
    video_url = scene.get(f"{orient_prefix}_video_url")

    if not video_url:
        raise ValueError(f"No video URL found for scene {scene['id']} ({orientation})")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        video_path = tmp_path / "scene.mp4"

        logger.info("Downloading video for scene %s from %s", scene["id"], video_url[:80])
        try:
            await _download_video(video_url, video_path)
        except (_URLExpiredError, Exception) as e:
            # URL expired or download failed — fall back to get_media API
            media_id = scene.get(f"{orient_prefix}_video_media_id")
            if not media_id:
                raise ValueError(f"No media_id to refresh URL for scene {scene['id']}")
            logger.info("URL download failed for scene %s (%s), fetching via get_media %s",
                        scene["id"], type(e).__name__, media_id[:12])
            await _download_via_get_media(media_id, video_path)

        if ANTHROPIC_API_KEY:
            # SDK path: individual frames
            logger.info("Extracting frames at %sfps (SDK mode)", fps)
            frames = await asyncio.get_event_loop().run_in_executor(
                None, _extract_frames, str(video_path), fps, tmp
            )
            if not frames:
                raise RuntimeError(f"No frames extracted from scene {scene['id']}")
            if len(frames) > REVIEW_MAX_FRAMES:
                step = len(frames) / REVIEW_MAX_FRAMES
                frames = [frames[int(i * step)] for i in range(REVIEW_MAX_FRAMES)]
            n_frames = len(frames)
            logger.info("Analyzing %d frames via Anthropic SDK", n_frames)
            result = await _analyze_sdk(frames, fps, scene, characters)
        else:
            # CLI path: contact sheet (no API key needed)
            logger.info("Creating contact sheet at %sfps (CLI mode)", fps)
            contact_sheet, n_frames = await asyncio.get_event_loop().run_in_executor(
                None, _create_contact_sheet, str(video_path), fps, tmp
            )
            if not contact_sheet.exists():
                raise RuntimeError(f"Contact sheet not created for scene {scene['id']}")
            logger.info("Analyzing %d frames via claude CLI", n_frames)
            result = await _analyze_cli(contact_sheet, n_frames, fps, scene)

    # Parse structured errors with severity
    errors = []
    for e in result.get("errors", []):
        if isinstance(e, dict) and "severity" in e and "time_range" in e and "description" in e:
            errors.append(VideoError(
                severity=e["severity"].upper(),
                time_range=e["time_range"],
                description=e["description"],
            ))
        elif isinstance(e, str):
            # Fallback: plain string from older response format
            errors.append(VideoError(severity="HIGH", time_range="?", description=e))

    has_critical = any(e.severity == "CRITICAL" for e in errors)

    dims_raw = result.get("dimensions", {})
    dims = DimensionScores(
        character_consistency=float(dims_raw.get("character_consistency", 5.0)),
        prompt_adherence=float(dims_raw.get("prompt_adherence", 5.0)),
        motion_quality=float(dims_raw.get("motion_quality", 5.0)),
        visual_fidelity=float(dims_raw.get("visual_fidelity", 5.0)),
        temporal_coherence=float(dims_raw.get("temporal_coherence", 5.0)),
        composition=float(dims_raw.get("composition", 5.0)),
    )

    # Enforce: any CRITICAL error caps character_consistency at 3.0
    if has_critical and dims.character_consistency > 3.0:
        dims = dims.model_copy(update={"character_consistency": 3.0})

    dims_dict = dims.model_dump()
    overall = _compute_overall(dims_dict)

    # Force score cap when critical errors present (verdict must be poor or unusable)
    if has_critical and overall > 5.9:
        overall = 5.9

    usable_segments = [
        SegmentScore(time_range=s["time_range"], score=float(s["score"]))
        for s in result.get("usable_segments", [])
        if isinstance(s, dict) and "time_range" in s and "score" in s
    ]

    return SceneReview(
        scene_id=scene["id"],
        overall_score=overall,
        verdict=_verdict(overall),
        dimensions=dims,
        errors=errors,
        usable_segments=usable_segments,
        fix_guide=_fix_guide(dims_dict, errors),
        frames_analyzed=n_frames,
        fps_used=fps,
        has_critical_errors=has_critical,
    )


async def review_video(
    video_id: str,
    project_id: str,
    mode: str = "light",
    orientation: str = "VERTICAL",
    scene_ids: list[str] | None = None,
) -> VideoReview:
    """Review all scenes (or a subset by scene_ids) in a video."""
    scenes = await list_scenes(video_id)
    if scene_ids:
        id_set = set(scene_ids)
        scenes = [s for s in scenes if s["id"] in id_set]
    characters = await get_project_characters(project_id)

    orient_prefix = "vertical" if orientation.upper() == "VERTICAL" else "horizontal"

    scene_reviews = []
    skipped = 0

    for scene in scenes:
        video_url = scene.get(f"{orient_prefix}_video_url")
        if not video_url:
            logger.info("Skipping scene %s -- no %s video", scene["id"], orientation)
            skipped += 1
            continue

        try:
            review = await review_scene_video(scene, characters, mode=mode, orientation=orientation, project_id=project_id)
            scene_reviews.append(review)
        except Exception as e:
            logger.error("Failed to review scene %s: %s", scene["id"], e)
            skipped += 1

    overall = round(sum(r.overall_score for r in scene_reviews) / len(scene_reviews), 2) if scene_reviews else 0.0

    return VideoReview(
        video_id=video_id,
        project_id=project_id,
        mode=mode,
        orientation=orientation,
        overall_score=overall,
        verdict=_verdict(overall),
        scene_reviews=scene_reviews,
        scenes_reviewed=len(scene_reviews),
        scenes_skipped=skipped,
    )
