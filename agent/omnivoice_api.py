"""Standalone warm OmniVoice HTTP service for remote FlowKit TTS."""
from __future__ import annotations

import asyncio
import hmac
import io
import math
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from agent.config import (
    OMNIVOICE_API_HOST,
    OMNIVOICE_API_PORT,
    OMNIVOICE_API_TOKEN,
    OMNIVOICE_MAX_REF_AUDIO_BYTES,
    OMNIVOICE_MAX_REQUEST_BYTES,
    TTS_DEVICE,
    TTS_MODEL,
    TTS_SAMPLE_RATE,
)

_model = None
_inference_lock = asyncio.Lock()


def _validate_wav_bytes(audio_bytes: bytes) -> None:
    if len(audio_bytes) < 12 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise HTTPException(status_code=400, detail="ref_audio must be a WAV file")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            frame_count = wav.getnframes()
            frame_width = wav.getnchannels() * wav.getsampwidth()
            if frame_count <= 0 or wav.getframerate() <= 0 or frame_width <= 0:
                raise HTTPException(status_code=400, detail="ref_audio must contain audio")
            if len(wav.readframes(frame_count)) != frame_count * frame_width:
                raise HTTPException(status_code=400, detail="ref_audio is truncated")
    except (wave.Error, EOFError) as error:
        raise HTTPException(status_code=400, detail="ref_audio is an invalid WAV file") from error


def _load_model():
    global _model
    import torch
    from omnivoice import OmniVoice

    dtype = torch.float16 if TTS_DEVICE.startswith("cuda") else torch.float32
    _model = OmniVoice.from_pretrained(TTS_MODEL, device_map=TTS_DEVICE, dtype=dtype)


def _generate_wav_bytes(
    text: str,
    instruct: Optional[str],
    ref_text: Optional[str],
    ref_audio_bytes: Optional[bytes],
    speed: float,
) -> bytes:
    import torchaudio

    if _model is None:
        raise RuntimeError("OmniVoice model is not loaded")

    with tempfile.TemporaryDirectory(prefix="omnivoice-") as temp_dir:
        ref_audio_path = None
        if ref_audio_bytes is not None:
            ref_audio_path = Path(temp_dir) / "reference.wav"
            ref_audio_path.write_bytes(ref_audio_bytes)

        output_path = Path(temp_dir) / "output.wav"
        kwargs = {"text": text}
        if ref_audio_path is not None and ref_text:
            kwargs["ref_audio"] = str(ref_audio_path)
            kwargs["ref_text"] = ref_text
        elif instruct:
            kwargs["instruct"] = instruct
        if speed != 1.0:
            kwargs["speed"] = speed

        audio = _model.generate(**kwargs)
        torchaudio.save(str(output_path), audio[0], TTS_SAMPLE_RATE)
        return output_path.read_bytes()


def _authorization_header_valid(actual: str) -> bool:
    if not OMNIVOICE_API_TOKEN:
        return True
    return hmac.compare_digest(actual, f"Bearer {OMNIVOICE_API_TOKEN}")


def _authorization_valid(request: Request) -> bool:
    return _authorization_header_valid(request.headers.get("authorization", ""))


async def _require_authorized(request: Request) -> None:
    if not _authorization_valid(request):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if OMNIVOICE_API_HOST not in {"127.0.0.1", "localhost", "::1"} and not OMNIVOICE_API_TOKEN:
        raise RuntimeError("OMNIVOICE_API_TOKEN is required when the API is not loopback-only")
    await asyncio.to_thread(_load_model)
    yield


app = FastAPI(title="OmniVoice TTS", version="1.0.0", lifespan=lifespan)


class _RequestTooLarge(Exception):
    pass


async def _send_json_error(send, status_code: int, detail: str) -> None:
    body = f'{{"detail":"{detail}"}}'.encode("utf-8")
    await send({"type": "http.response.start", "status": status_code, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]})
    await send({"type": "http.response.body", "body": body})


class _TtsRequestLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/v1/tts":
            await self.app(scope, receive, send)
            return

        request_headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = request_headers.get(b"authorization", b"").decode("latin-1")
        if not _authorization_header_valid(authorization):
            await _send_json_error(send, 401, "Invalid bearer token")
            return

        content_length = request_headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > OMNIVOICE_MAX_REQUEST_BYTES:
                    await _send_json_error(send, 413, "request is too large")
                    return
            except ValueError:
                await _send_json_error(send, 400, "invalid content-length")
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > OMNIVOICE_MAX_REQUEST_BYTES:
                    raise _RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestTooLarge:
            await _send_json_error(send, 413, "request is too large")


app.add_middleware(_TtsRequestLimitMiddleware)

@app.get("/health")
async def health():
    return {"ok": _model is not None, "model": TTS_MODEL, "device": TTS_DEVICE}


@app.post("/v1/tts", response_class=Response)
async def synthesize(
    request: Request,
    text: str = Form(...),
    speed: float = Form(1.0),
    instruct: Optional[str] = Form(None),
    ref_text: Optional[str] = Form(None),
    ref_audio: Optional[UploadFile] = File(None),
):
    await _require_authorized(request)
    if not text or len(text) > 5000:
        raise HTTPException(status_code=422, detail="text must contain 1-5000 characters")
    if not math.isfinite(speed) or speed < 0.5 or speed > 3.0:
        raise HTTPException(status_code=422, detail="speed must be between 0.5 and 3.0")
    if instruct is not None and len(instruct) > 200:
        raise HTTPException(status_code=422, detail="instruct must contain at most 200 characters")
    if ref_text is not None and len(ref_text) > 5000:
        raise HTTPException(status_code=422, detail="ref_text is too long")

    ref_audio_bytes = None
    if ref_audio is not None:
        ref_audio_bytes = await ref_audio.read(OMNIVOICE_MAX_REF_AUDIO_BYTES + 1)
        if len(ref_audio_bytes) > OMNIVOICE_MAX_REF_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="ref_audio is too large")
        _validate_wav_bytes(ref_audio_bytes)

    async with _inference_lock:
        try:
            wav_bytes = await asyncio.to_thread(
                _generate_wav_bytes,
                text,
                instruct,
                ref_text,
                ref_audio_bytes,
                speed,
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="OmniVoice synthesis failed")

    return Response(content=wav_bytes, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=OMNIVOICE_API_HOST, port=OMNIVOICE_API_PORT)
