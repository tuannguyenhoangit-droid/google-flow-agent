"""Model configuration API — view and update video/image/upscale model keys."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter

from agent import config

router = APIRouter(prefix="/api/models", tags=["models"])
logger = logging.getLogger(__name__)

_MODELS_FILE = Path(__file__).parent.parent / "models.json"


def _read_models() -> dict:
    with open(_MODELS_FILE) as f:
        return json.load(f)


def _write_models(data: dict):
    with open(_MODELS_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _reload_config(data: dict):
    """Hot-reload model keys into the running config module."""
    config.VIDEO_MODELS.clear()
    config.VIDEO_MODELS.update(data["video_models"])
    config.UPSCALE_MODELS.clear()
    config.UPSCALE_MODELS.update(data["upscale_models"])
    config.IMAGE_MODELS.clear()
    config.IMAGE_MODELS.update(data["image_models"])
    config.DEFAULT_IMAGE_MODEL = data.get("default_image_model", "NANO_BANANA_PRO")


@router.get("")
async def get_models():
    """Return current model configuration."""
    return _read_models()


@router.patch("")
async def patch_models(body: dict):
    """Update model keys. Merges provided keys into existing config.

    Example body to change video model for TIER_TWO i2v portrait:
    {
      "video_models": {
        "PAYGATE_TIER_TWO": {
          "frame_2_video": {
            "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_fast_portrait_ultra"
          }
        }
      }
    }
    """
    current = _read_models()

    if "default_image_model" in body:
        current["default_image_model"] = body["default_image_model"]

    # Deep merge: only update keys that are provided
    for section in ("video_models", "image_models", "upscale_models"):
        if section not in body:
            continue
        if section == "upscale_models" or section == "image_models":
            # Flat dict — direct merge
            current[section].update(body[section])
        else:
            # Nested dict — merge per tier, per gen_type
            for tier, gen_types in body[section].items():
                if tier not in current[section]:
                    current[section][tier] = {}
                for gen_type, ratios in gen_types.items():
                    if gen_type not in current[section][tier]:
                        current[section][tier][gen_type] = {}
                    current[section][tier][gen_type].update(ratios)

    _write_models(current)
    _reload_config(current)
    logger.info("Models updated and hot-reloaded: %s", list(body.keys()))

    return {"status": "updated", "models": current}
