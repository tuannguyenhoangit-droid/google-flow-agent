from pydantic import BaseModel, Field, model_validator
from typing import Optional
from agent.models.enums import ProjectStatus, PaygateTier, EntityType


class CharacterInput(BaseModel):
    """Reference entity stub provided at project creation time."""
    name: str
    entity_type: EntityType = "character"
    description: Optional[str] = None
    voice_description: Optional[str] = None  # max ~30 words, characters/creatures only


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    story: Optional[str] = None
    language: str = "en"
    user_paygate_tier: PaygateTier = "PAYGATE_TIER_ONE"
    tool_name: str = "PINHOLE"
    # Flow stopped letting clients create projects in the September 2026
    # migration, so a project is made once in the Flow UI and its uuid supplied
    # here. Falls back to FLOW_PROJECT_ID when omitted.
    flow_project_id: Optional[str] = None
    material: str = Field("realistic", pattern=r"^[a-z0-9][a-z0-9_]{1,63}$")  # material ID from GET /api/materials
    style: Optional[str] = None  # deprecated: use material instead; "3D"→"3d_pixar", "photorealistic"→"realistic"
    allow_music: bool = False  # when True, skip "no background music" suffix in video prompts
    allow_voice: bool = False  # when True, keep character dialogue in video audio (suppress only music/narration)
    characters: Optional[list[CharacterInput]] = None

    @model_validator(mode="before")
    @classmethod
    def map_style_to_material(cls, data):
        if isinstance(data, dict):
            style = data.get("style")
            # Only map if style is provided AND material is not explicitly set
            if style and "material" not in data:
                compat_map = {"3D": "3d_pixar", "3d": "3d_pixar", "photorealistic": "realistic"}
                data["material"] = compat_map.get(style, style.lower().replace(" ", "_"))
        return data


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    story: Optional[str] = None
    thumbnail_url: Optional[str] = None
    language: Optional[str] = None
    status: Optional[ProjectStatus] = None
    user_paygate_tier: Optional[PaygateTier] = None
    narrator_voice: Optional[str] = None
    narrator_ref_audio: Optional[str] = None
    material: Optional[str] = None
    allow_music: Optional[bool] = None
    allow_voice: Optional[bool] = None


class Project(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    story: Optional[str] = None
    thumbnail_url: Optional[str] = None
    language: str = "en"
    status: str = "ACTIVE"
    user_paygate_tier: str = "PAYGATE_TIER_ONE"
    material: Optional[str] = None
    allow_music: bool = False
    allow_voice: bool = False
    narrator_voice: Optional[str] = None
    narrator_ref_audio: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
