"""OmniVoice TTS service with local and remote backends."""
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.parse
import wave
from pathlib import Path
from typing import Optional

import httpx

from agent.config import (
    TTS_MODEL,
    TTS_REMOTE_MAX_BYTES,
    TTS_REMOTE_TOKEN,
    TTS_REMOTE_TIMEOUT_S,
    TTS_REMOTE_URL,
    TTS_SAMPLE_RATE,
    TTS_BACKEND,
)

logger = logging.getLogger(__name__)

# The launcher starts FlowKit with its venv interpreter. Keep the override for deployments that
# intentionally use a separate OmniVoice environment, but never require an executable named
# python3.10 to be present on PATH.
PYTHON_BIN = os.environ.get("TTS_PYTHON_BIN", sys.executable)

# Inline script template for local subprocess generation.
_TTS_SCRIPT = """
import sys, json, torch, torchaudio

args = json.loads(sys.argv[1])
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(args["model"], device_map="cpu", dtype=torch.float32)

kwargs = {"text": args["text"]}
if args.get("ref_audio") and args.get("ref_text"):
    kwargs["ref_audio"] = args["ref_audio"]
    kwargs["ref_text"] = args["ref_text"]
elif args.get("instruct"):
    kwargs["instruct"] = args["instruct"]
if args.get("speed") and args["speed"] != 1.0:
    kwargs["speed"] = args["speed"]

audio = model.generate(**kwargs)
torchaudio.save(args["output"], audio[0], args["sample_rate"])
print(json.dumps({"ok": True, "path": args["output"]}))
"""

# Batch script — loads model once for local generation of multiple texts.
_TTS_BATCH_SCRIPT = """
import sys, json, torch, torchaudio
from pathlib import Path

args = json.loads(sys.argv[1])
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(args["model"], device_map="cpu", dtype=torch.float32)

results = []
for item in args["items"]:
    try:
        kwargs = {"text": item["text"]}
        if args.get("ref_audio") and args.get("ref_text"):
            kwargs["ref_audio"] = args["ref_audio"]
            kwargs["ref_text"] = args["ref_text"]
        elif args.get("instruct"):
            kwargs["instruct"] = args["instruct"]
        if args.get("speed") and args["speed"] != 1.0:
            kwargs["speed"] = args["speed"]

        audio = model.generate(**kwargs)
        Path(item["output"]).parent.mkdir(parents=True, exist_ok=True)
        torchaudio.save(item["output"], audio[0], args["sample_rate"])

        info = torchaudio.info(item["output"])
        duration = info.num_frames / info.sample_rate
        results.append({"id": item["id"], "ok": True, "path": item["output"], "duration": duration})
    except Exception as e:
        results.append({"id": item["id"], "ok": False, "error": str(e)})

print(json.dumps(results))
"""


def _remote_endpoint() -> str:
    if TTS_BACKEND != "remote":
        raise RuntimeError(f"Unsupported TTS_BACKEND: {TTS_BACKEND!r}")
    if not TTS_REMOTE_URL:
        raise RuntimeError("TTS_REMOTE_URL is required when TTS_BACKEND=remote")
    parsed = urllib.parse.urlparse(TTS_REMOTE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("TTS_REMOTE_URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("TTS_REMOTE_URL must not contain credentials, query, or fragment")
    root = TTS_REMOTE_URL.rstrip("/")
    return root if root.endswith("/v1/tts") else f"{root}/v1/tts"


def _validate_wav_bytes(audio_bytes: bytes) -> None:
    if len(audio_bytes) < 12 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise RuntimeError("Remote TTS returned a non-WAVE response")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            frame_count = wav.getnframes()
            frame_width = wav.getnchannels() * wav.getsampwidth()
            if frame_count <= 0 or wav.getframerate() <= 0 or frame_width <= 0:
                raise RuntimeError("Remote TTS returned an empty WAV")
            if len(wav.readframes(frame_count)) != frame_count * frame_width:
                raise RuntimeError("Remote TTS returned a truncated WAV")
    except (wave.Error, EOFError) as error:
        raise RuntimeError("Remote TTS returned an invalid WAV") from error


def _write_wav_atomically(output_path: str, audio_bytes: bytes) -> None:
    _validate_wav_bytes(audio_bytes)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(audio_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_limited(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as source:
        return source.read(max_bytes + 1)


async def _read_reference_audio(ref_audio: str) -> tuple[str, bytes]:
    reference = Path(ref_audio)
    if not reference.is_file():
        raise RuntimeError("Reference audio file does not exist")
    try:
        audio_bytes = await asyncio.to_thread(_read_limited, reference, TTS_REMOTE_MAX_BYTES)
    except OSError as error:
        raise RuntimeError("Reference audio could not be read") from error
    if len(audio_bytes) > TTS_REMOTE_MAX_BYTES:
        raise RuntimeError("Reference audio exceeds the remote TTS size limit")
    _validate_wav_bytes(audio_bytes)
    return reference.name, audio_bytes


async def _generate_remote(
    text: str,
    output_path: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
) -> str:
    data = {"text": text, "speed": str(speed)}
    if instruct:
        data["instruct"] = instruct
    if ref_text:
        data["ref_text"] = ref_text

    files = None
    # OmniVoice cloning requires both fields. Preserve local compatibility when a caller supplies
    # only ref_audio by following the instruct/generic path rather than leaking a local path.
    if ref_audio and ref_text:
        filename, audio_bytes = await _read_reference_audio(ref_audio)
        files = {"ref_audio": (filename, audio_bytes, "audio/wav")}

    headers = {}
    if TTS_REMOTE_TOKEN:
        headers["authorization"] = f"Bearer {TTS_REMOTE_TOKEN}"

    timeout = httpx.Timeout(TTS_REMOTE_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                _remote_endpoint(),
                data=data,
                files=files,
                headers=headers,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise RuntimeError(f"Remote TTS failed with HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type != "audio/wav":
                    raise RuntimeError("Remote TTS returned an unexpected content type")
                audio = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(audio) + len(chunk) > TTS_REMOTE_MAX_BYTES:
                        raise RuntimeError("Remote TTS response exceeds the size limit")
                    audio.extend(chunk)
    except httpx.HTTPError as error:
        raise RuntimeError("Remote TTS request failed") from error

    _write_wav_atomically(output_path, bytes(audio))
    logger.info("Remote TTS saved to %s", output_path)
    return output_path


async def generate_speech(
    text: str,
    output_path: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
) -> str:
    """Generate speech and return a local WAV path."""
    if TTS_BACKEND == "remote":
        return await _generate_remote(text, output_path, instruct, ref_audio, ref_text, speed)
    if TTS_BACKEND != "local":
        raise RuntimeError(f"Unsupported TTS_BACKEND: {TTS_BACKEND!r}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    args = {
        "model": TTS_MODEL,
        "text": text,
        "output": output_path,
        "sample_rate": TTS_SAMPLE_RATE,
        "speed": speed,
    }
    if instruct:
        args["instruct"] = instruct
    if ref_audio:
        args["ref_audio"] = ref_audio
    if ref_text:
        args["ref_text"] = ref_text

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_tts_subprocess, args)
    if not result.get("ok"):
        raise RuntimeError(f"TTS failed: {result.get('error', 'unknown')}")

    logger.info("TTS saved to %s", output_path)
    return output_path


def _run_tts_subprocess(args: dict) -> dict:
    """Run one local TTS subprocess."""
    proc = subprocess.run(
        [PYTHON_BIN, "-c", _TTS_SCRIPT, json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr[-500:] if proc.stderr else "unknown error"}
    try:
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": proc.stdout[-200:] + proc.stderr[-200:]}


def _wav_duration_seconds(path: str) -> float:
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / wav.getframerate()


async def generate_video_narration(
    scenes: list[dict],
    output_dir: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
) -> list[dict]:
    """Generate narration WAVs for scenes with narrator_text."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if TTS_BACKEND not in {"local", "remote"}:
        raise RuntimeError(f"Unsupported TTS_BACKEND: {TTS_BACKEND!r}")

    items = []
    scene_map = {}
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            continue

        wav_path = str(out_dir / f"scene_{display_order:03d}_{scene_id}.wav")
        if Path(wav_path).exists() and Path(wav_path).stat().st_size > 1024:
            logger.info("Skipping scene %03d (WAV exists: %s)", display_order, wav_path)
            scene_map[scene_id] = {
                "display_order": display_order,
                "narrator_text": narrator_text,
                "skipped": True,
                "wav_path": wav_path,
            }
            continue
        items.append({"id": scene_id, "text": narrator_text, "output": wav_path})
        scene_map[scene_id] = {"display_order": display_order, "narrator_text": narrator_text}

    batch_results = {}
    if items and TTS_BACKEND == "remote":
        # Keep one request per scene so failures remain attributable and the remote service's
        # single warm model is not driven concurrently.
        for item in items:
            try:
                await generate_speech(
                    text=item["text"],
                    output_path=item["output"],
                    instruct=instruct,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    speed=speed,
                )
                batch_results[item["id"]] = {
                    "id": item["id"],
                    "ok": True,
                    "path": item["output"],
                    "duration": _wav_duration_seconds(item["output"]),
                }
            except Exception as error:
                batch_results[item["id"]] = {"id": item["id"], "ok": False, "error": str(error)}
    elif items:
        args = {
            "model": TTS_MODEL,
            "sample_rate": TTS_SAMPLE_RATE,
            "speed": speed,
            "items": items,
        }
        if instruct:
            args["instruct"] = instruct
        if ref_audio:
            args["ref_audio"] = ref_audio
        if ref_text:
            args["ref_text"] = ref_text

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _run_batch_subprocess, args)
        for result in raw:
            batch_results[result["id"]] = result

    results = []
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": None,
                "audio_path": None,
                "duration": None,
                "status": "SKIPPED",
                "error": None,
            })
            continue

        scene_result = scene_map.get(scene_id, {})
        if scene_result.get("skipped"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": scene_result["wav_path"],
                "duration": None,
                "status": "COMPLETED",
                "error": None,
            })
            continue

        batch_result = batch_results.get(scene_id, {})
        if batch_result.get("ok"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": batch_result.get("path"),
                "duration": batch_result.get("duration"),
                "status": "COMPLETED",
                "error": None,
            })
        else:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": None,
                "duration": None,
                "status": "FAILED",
                "error": batch_result.get("error", "not processed"),
            })

    return results


def _run_batch_subprocess(args: dict) -> list[dict]:
    """Run local batch TTS subprocess. Model loads once."""
    timeout = 180 + len(args.get("items", [])) * 45
    proc = subprocess.run(
        [PYTHON_BIN, "-c", _TTS_BATCH_SCRIPT, json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        error = proc.stderr[-500:] if proc.stderr else "unknown"
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
    try:
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        error = proc.stdout[-200:] + proc.stderr[-200:]
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
