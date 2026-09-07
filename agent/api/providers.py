"""CLI provider config API — view/switch which AI CLI backend runs video review."""
import asyncio
import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agent import config
from agent.services.video_reviewer import PROVIDER_BINARIES

router = APIRouter(prefix="/api/providers", tags=["providers"])
logger = logging.getLogger(__name__)
_PROVIDERS_FILE = Path(__file__).parent.parent / "providers.json"


def _read() -> dict:
    with open(_PROVIDERS_FILE) as f:
        return json.load(f)


def _write(data: dict):
    with open(_PROVIDERS_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


async def _probe_version(binary: str) -> tuple:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0, None if proc.returncode == 0 else stderr.decode()[-200:]
    except Exception as e:
        return False, str(e)


@router.get("")
async def get_providers(live: bool = False):
    """Return provider status: installed (on PATH) and, if live=true, version-tested."""
    data = _read()
    statuses = {}
    for name, binary in PROVIDER_BINARIES.items():
        installed = shutil.which(binary) is not None
        tested, error = (await _probe_version(binary)) if (live and installed) else (None, None)
        statuses[name] = {"binary": binary, "installed": installed, "tested": tested, "error": error}
    return {"active": data["active"], "providers": statuses}


@router.patch("")
async def patch_providers(body: dict):
    """Switch the active CLI provider. Body: {"active": "claude"|"agy"|"codex"}."""
    provider = body.get("active")
    if provider not in PROVIDER_BINARIES:
        raise HTTPException(400, f"Unknown provider '{provider}'. Known: {list(PROVIDER_BINARIES)}")
    if not shutil.which(PROVIDER_BINARIES[provider]):
        raise HTTPException(400, f"'{PROVIDER_BINARIES[provider]}' binary not found on PATH — install it first")
    data = _read()
    data["active"] = provider
    _write(data)
    config.CLI_PROVIDERS.clear()
    config.CLI_PROVIDERS.update(data)
    return {"status": "updated", "active": provider}
