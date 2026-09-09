import json
import logging
import re
from datetime import datetime, timezone

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.config import BASE_DIR, USE_BATCH_RPC, FLOW_PROJECT_ID
from agent.models.project import Project, ProjectCreate, ProjectUpdate
from agent.models.character import Character
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.services.flow_client import get_flow_client
from agent.utils.slugify import slugify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


COMPOSITION_GUIDELINES = {
    "character": (
        "COMPOSITION: Full body shot from head to toe, standing upright and straight (not tilted or leaning). "
        "Centered in frame with balanced composition. Front-facing view, looking directly at camera. "
        "Neutral simple background that doesn't distract from the subject. "
        "Proper proportions and anatomy. Character perfectly vertical, not skewed or rotated."
    ),
    "location": (
        "COMPOSITION: Establishing shot showing the full environment. "
        "Balanced level composition with straight horizon. Clear focal point. "
        "Atmospheric and richly detailed. Show depth and spatial layout."
    ),
    "creature": (
        "COMPOSITION: Full body shot showing the creature's complete form. "
        "Emphasize natural stance (quadrupedal on all fours, bipedal upright, etc.). "
        "Centered with clear view of distinctive features. Neutral background. "
        "Proper scale and proportions relative to body structure."
    ),
    "visual_asset": (
        "COMPOSITION: Clear detailed view showing the asset's complete form. "
        "Appropriate angle to showcase distinctive features and functional elements. "
        "Centered with proper scale reference. Neutral background. "
        "Show key details, materials, and surface textures."
    ),
    "generic_troop": (
        "COMPOSITION: Military/tactical pose showing readiness. "
        "Full or three-quarter body view. Centered composition. "
        "Neutral background. Proper perspective and proportions."
    ),
    "faction": (
        "COMPOSITION: Military/tactical pose showing readiness. "
        "Full or three-quarter body view. Centered composition. "
        "Neutral background. Proper perspective and proportions."
    ),
}


_STYLE_COMPAT_MAP = {
    "3d": "3d_pixar",
    "3D": "3d_pixar",
    "photorealistic": "realistic",
}


def _resolve_material_id(value: str) -> str:
    """Map legacy style strings to material IDs. Returns value unchanged if no mapping."""
    return _STYLE_COMPAT_MAP.get(value, value)


def _build_character_profile(char_name: str, char_desc: str | None, story: str | None,
                              entity_type: str = "character", material_id: str = "3d_pixar") -> dict:
    """Build a rich profile (description + image_prompt) for any reference entity.

    The image_prompt generates a reference image used as mediaId for all
    scene generations. Visual appearance is defined HERE, not in scene prompts.
    Scene prompts should only describe actions/environment/composition.

    story may be None — in that case the description omits story context and
    the image_prompt uses a simpler prefix.
    """
    from agent.materials import get_material
    material = get_material(material_id)
    if not material:
        raise ValueError(f"Unknown material: {material_id}")

    base_desc = char_desc or char_name
    composition = COMPOSITION_GUIDELINES.get(entity_type, COMPOSITION_GUIDELINES["character"])

    if story:
        description = f"{char_name}: {base_desc}. Story context: {story}"
        image_prefix = f"Single reference image of {base_desc}. "
        single_image_note = "ONE single image only, NOT a multi-panel grid or multiple views. "
    else:
        description = base_desc
        image_prefix = f"Reference image of {base_desc}. "
        single_image_note = ""

    style_instruction = material["style_instruction"]
    if material.get("negative_prompt"):
        style_instruction += f" {material['negative_prompt']}"
    lighting = material.get("lighting", "Studio lighting, highly detailed")

    image_prompt = (
        f"{image_prefix}"
        f"{style_instruction} "
        f"{composition} "
        f"{single_image_note}"
        f"{lighting}"
    )

    return {"description": description, "image_prompt": image_prompt}


async def _detect_user_tier(client) -> str:
    """Auto-detect user paygate tier from Flow credits API."""
    try:
        result = await client.get_credits()
        data = result.get("data", result)
        tier = data.get("userPaygateTier", "PAYGATE_TIER_ONE")
        logger.info("Auto-detected user tier: %s", tier)
        return tier
    except Exception as e:
        logger.warning("Failed to detect tier, defaulting to TIER_ONE: %s", e)
        return "PAYGATE_TIER_ONE"


def _read_flow_project_id(flow_result: dict) -> str:
    """Pull the project uuid out of whichever transport answered.

    The batch path answers `{"projectId": …}`; the legacy tRPC path buries it
    under result/data/json/result.
    """
    data = flow_result.get("data") or {}
    if isinstance(data, dict):
        if isinstance(data.get("projectId"), str):
            return data["projectId"]
        try:
            return data["result"]["data"]["json"]["result"]["projectId"]
        except (KeyError, TypeError):
            pass
    logger.error("Unexpected Flow response: %s", flow_result)
    raise HTTPException(502, "Could not read a Flow project id from the response")


def _get_repo() -> SQLiteRepository:
    return SQLiteRepository()


@router.post("", response_model=Project)
async def create(body: ProjectCreate):
    from agent.materials import get_material

    # Step 1: Create project on Google Flow to get the real projectId
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected — cannot create project on Google Flow")

    # Resolve material (support legacy style field + material field)
    material_id = _resolve_material_id(body.material)
    material = get_material(material_id)
    if not material:
        raise HTTPException(400, f"Unknown material: '{material_id}'. Use GET /api/materials to list available materials.")

    # Validate characters before any API calls to avoid orphan projects
    characters_input_raw = body.model_dump(exclude_none=True).get("characters")
    if characters_input_raw:
        slugs = [slugify(c["name"]) for c in characters_input_raw]
        if len(slugs) != len(set(slugs)):
            dupes = [s for s in slugs if slugs.count(s) > 1]
            raise HTTPException(400, f"Duplicate character slugs: {list(set(dupes))}")

    detected_tier = await _detect_user_tier(client)

    # On the batch path a pinned FLOW_PROJECT_ID (or a uuid passed in the request)
    # is reused; with neither, create_project now mints one via jHPbke. The legacy
    # path always mints one.
    flow_project_id = client.flow_project_id(body.flow_project_id) if USE_BATCH_RPC else None
    if flow_project_id:
        logger.info("Flow project reused: %s", flow_project_id)
    else:
        flow_result = await client.create_project(body.name, body.tool_name)
        if flow_result.get("error"):
            raise HTTPException(502, f"Flow API error: {flow_result['error']}")
        flow_project_id = _read_flow_project_id(flow_result)
        logger.info("Flow project created: %s", flow_project_id)

    repo = _get_repo()

    # Reuse is idempotent: a pinned or request-supplied project id may already
    # have a local row. Re-inserting that id violates the primary key
    # (sqlite3.IntegrityError -> 500), so hand back the row we already have. A
    # freshly minted id never matches, so this is a no-op on the create path.
    existing = await repo.get_project(flow_project_id)
    if existing:
        logger.info("Flow project already tracked, reusing: %s", flow_project_id)
        return existing

    # Step 2: Create local project with the Flow-assigned ID and detected tier
    create_data = body.model_dump(exclude_none=True)
    create_data.pop("tool_name", None)
    create_data.pop("flow_project_id", None)
    create_data.pop("style", None)
    characters_input = create_data.pop("characters", None)

    project = await repo.create_project(
        id=flow_project_id,
        name=create_data["name"],
        description=create_data.get("description"),
        story=create_data.get("story"),
        language=create_data.get("language", "en"),
        user_paygate_tier=detected_tier,
        material=material_id,
        allow_music=create_data.get("allow_music", False),
        allow_voice=create_data.get("allow_voice", False),
    )

    # Step 3: Create reference entities (characters, locations, assets) with profiles
    if characters_input:
        for char_input in characters_input:
            etype = char_input.get("entity_type", "character")
            profile = _build_character_profile(
                char_input["name"],
                char_input.get("description"),
                body.story,
                entity_type=etype,
                material_id=material_id,
            )
            description = profile["description"]
            image_prompt = profile["image_prompt"]
            char = await repo.create_character(
                name=char_input["name"],
                slug=slugify(char_input["name"]),
                entity_type=etype,
                description=description,
                image_prompt=image_prompt,
                voice_description=char_input.get("voice_description"),
            )
            await repo.link_character_to_project(flow_project_id, char.id)
            logger.info("%s '%s' created and linked: %s", etype, char_input["name"], char.id)

    return project


@router.get("", response_model=list[Project])
async def list_all(status: str = None):
    repo = _get_repo()
    rows = await repo.list("project", **({} if status is None else {"status": status}))
    return [repo._row_to_project(r) for r in rows]


@router.get("/{pid}", response_model=Project)
async def get(pid: str):
    repo = _get_repo()
    p = await repo.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@router.patch("/{pid}", response_model=Project)
async def update(pid: str, body: ProjectUpdate):
    repo = _get_repo()
    row = await repo.update("project", pid, **body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(404, "Project not found")
    return repo._row_to_project(row)


@router.delete("/{pid}")
async def delete(pid: str):
    """Delete a project locally, and on Flow too when it is safe.

    The project must exist locally before anything is deleted, so a stray uuid
    cannot reach through to Flow — only projects Flow Kit already tracks are
    eligible. Flow is then deleted first (except the pinned FLOW_PROJECT_ID, the
    shared/operator project, which is only ever removed locally) so a remote
    failure leaves the local row in place rather than letting the two drift.
    """
    repo = _get_repo()
    if not await repo.get_project(pid):
        raise HTTPException(404, "Project not found")
    if USE_BATCH_RPC and pid != (FLOW_PROJECT_ID or None):
        client = get_flow_client()
        if not client.connected:
            raise HTTPException(503, "Extension not connected — cannot delete this project on Google Flow")
        result = await client.delete_project(pid)
        if result.get("error"):
            raise HTTPException(502, f"Flow delete failed: {result['error']}")
    await repo.delete_project(pid)
    return {"ok": True}


@router.post("/{pid}/characters/{cid}")
async def link_character(pid: str, cid: str):
    repo = _get_repo()
    if not await repo.link_character_to_project(pid, cid):
        raise HTTPException(400, "Failed to link character")
    return {"ok": True}


@router.delete("/{pid}/characters/{cid}")
async def unlink_character(pid: str, cid: str):
    repo = _get_repo()
    if not await repo.unlink_character_from_project(pid, cid):
        raise HTTPException(404, "Link not found")
    return {"ok": True}


@router.get("/{pid}/characters", response_model=list[Character])
async def get_characters(pid: str):
    repo = _get_repo()
    return await repo.get_project_characters(pid)


@router.get("/{pid}/output-dir")
async def get_output_dir(pid: str):
    """Get or create project output directory with meta.json."""
    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")

    project_name = project.name if hasattr(project, "name") else project["name"]
    slug = slugify(project_name)
    output_dir = BASE_DIR / "output" / slug

    for subdir in ["scenes", "4k", "tts", "narrated", "trimmed", "norm", "thumbnails", "subclips", "review"]:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    videos = await repo.list_videos(pid)
    video = videos[0] if videos else None
    video_id = video.id if video else None
    scene_count = 0
    if video_id:
        scenes = await repo.list_scenes(video_id)
        scene_count = len(scenes) if scenes else 0

    # Orientation lives on the video table, not project
    video_orientation = (getattr(video, "orientation", None) if video else None) or "VERTICAL"

    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "project_id": pid,
        "project_name": project_name,
        "slug": slug,
        "video_id": video_id,
        "orientation": video_orientation,
        "material": getattr(project, "material", None) or (project.get("material") if isinstance(project, dict) else None) or "",
        "scene_count": scene_count,
        "created_at": now,
    }
    meta_path = output_dir / "meta.json"
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        meta["created_at"] = existing.get("created_at", now)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    return {"slug": slug, "path": f"output/{slug}", "meta": meta}


_ASPECT_RATIO_MAP = {
    "LANDSCAPE": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "PORTRAIT": "IMAGE_ASPECT_RATIO_PORTRAIT",
}


class ThumbnailRequest(BaseModel):
    prompt: str
    character_names: list[str] = []
    aspect_ratio: str = "LANDSCAPE"
    output_filename: str = "thumbnail.png"


class ThumbnailResponse(BaseModel):
    success: bool
    media_id: str | None = None
    image_url: str | None = None
    output_path: str | None = None
    prompt: str | None = None
    error: str | None = None


@router.post("/{pid}/generate-thumbnail", response_model=ThumbnailResponse)
async def generate_thumbnail(pid: str, body: ThumbnailRequest):
    """Generate a thumbnail image for a project via Google Flow API (synchronous, no queue)."""
    import logging
    logger = logging.getLogger(__name__)
    from agent.materials import get_material
    from agent.sdk.services.result_handler import parse_result

    logger.info("generate_thumbnail: started for project %s", pid)

    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")

    # Build full prompt: prepend material scene_prefix for style consistency
    material_id = getattr(project, "material", None) or "realistic"
    material = get_material(material_id)
    scene_prefix = material["scene_prefix"] if material and material.get("scene_prefix") else ""
    full_prompt = f"{scene_prefix} {body.prompt}".strip() if scene_prefix else body.prompt

    # Resolve character reference media_ids (error if any named entity is missing media_id)
    character_media_ids = None
    if body.character_names:
        entities = await repo.get_project_characters(pid)
        valid_ids = []
        missing = []
        for entity in entities:
            name = entity["name"] if isinstance(entity, dict) else entity.name
            mid = entity.get("media_id") if isinstance(entity, dict) else getattr(entity, "media_id", None)
            char_slug = (entity.get("slug") if isinstance(entity, dict) else getattr(entity, "slug", None)) or ""
            if not ((char_slug and char_slug in body.character_names) or (name and name in body.character_names)):
                continue
            if mid:
                valid_ids.append(mid)
            else:
                missing.append(name)
        if missing:
            raise HTTPException(400, f"Missing reference images for: {', '.join(missing)}. Generate ref images first.")
        character_media_ids = valid_ids if valid_ids else None

    aspect_ratio = _ASPECT_RATIO_MAP.get(body.aspect_ratio.upper(), "IMAGE_ASPECT_RATIO_LANDSCAPE")
    tier = getattr(project, "user_paygate_tier", "PAYGATE_TIER_TWO") or "PAYGATE_TIER_TWO"

    logger.info("generate_thumbnail: calling generate_images prompt=%s refs=%s", full_prompt[:60], character_media_ids)
    raw = await client.generate_images(
        prompt=full_prompt,
        project_id=pid,
        aspect_ratio=aspect_ratio,
        user_paygate_tier=tier,
        character_media_ids=character_media_ids,
    )
    logger.info("generate_thumbnail: generate_images returned, error=%s", raw.get("error") if isinstance(raw, dict) else "n/a")

    gen_result = parse_result(raw, "GENERATE_IMAGE")
    if not gen_result.success:
        raise HTTPException(502, gen_result.error or "Image generation failed")

    # Download and save to output/{project_name}/thumbnails/{filename}
    project_name = slugify(getattr(project, "name", "project"))
    out_dir = BASE_DIR / "output" / project_name / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / body.output_filename

    if gen_result.url and gen_result.url.startswith("http"):
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(gen_result.url) as resp:
                    if resp.status == 200:
                        output_path.write_bytes(await resp.read())
                    else:
                        raise HTTPException(502, f"Failed to download image: HTTP {resp.status}")
        except aiohttp.ClientError as e:
            raise HTTPException(502, f"Failed to download image: {e}") from e

    return ThumbnailResponse(
        success=True,
        media_id=gen_result.media_id,
        image_url=gen_result.url,
        output_path=str(output_path),
        prompt=full_prompt,
    )
