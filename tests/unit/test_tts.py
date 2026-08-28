"""Focused tests for local-path and remote OmniVoice TTS contracts."""

import io
import wave

import pytest
from agent import omnivoice_api
from agent.services import tts


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 240)
    return output.getvalue()


class _FakeResponse:
    def __init__(self, body: bytes, status_code: int = 200, content_type: str = "audio/wav"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    response = _FakeResponse(_wav_bytes())
    calls = []

    def __init__(self, **kwargs):
        self.timeout = kwargs["timeout"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_remote_generation_uploads_reference_bytes_and_writes_local_wav(tmp_path, monkeypatch):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(_wav_bytes())
    output = tmp_path / "scene.wav"
    _FakeClient.calls = []

    monkeypatch.setattr(tts, "TTS_BACKEND", "remote")
    monkeypatch.setattr(tts, "TTS_REMOTE_URL", "https://tts.example.test")
    monkeypatch.setattr(tts, "TTS_REMOTE_TOKEN", "secret")
    monkeypatch.setattr(tts.httpx, "AsyncClient", _FakeClient)

    result = await tts._generate_remote(
        text="Xin chao",
        output_path=str(output),
        ref_audio=str(reference),
        ref_text="Mau transcript",
        speed=1.1,
    )

    assert result == str(output)


    assert output.read_bytes() == _wav_bytes()
    method, url, request = _FakeClient.calls[0]
    assert method == "POST"
    assert url == "https://tts.example.test/v1/tts"
    assert request["headers"] == {"authorization": "Bearer secret"}
    assert request["data"] == {"text": "Xin chao", "speed": "1.1", "ref_text": "Mau transcript"}
    assert request["files"]["ref_audio"][1] == _wav_bytes()


@pytest.mark.asyncio
async def test_remote_generation_does_not_replace_target_on_invalid_response(tmp_path, monkeypatch):
    output = tmp_path / "scene.wav"
    original = b"previous audio"
    output.write_bytes(original)

    class InvalidClient(_FakeClient):
        response = _FakeResponse(b"not wav", content_type="audio/wav")

    monkeypatch.setattr(tts, "TTS_BACKEND", "remote")
    monkeypatch.setattr(tts, "TTS_REMOTE_URL", "http://127.0.0.1:8200")
    monkeypatch.setattr(tts.httpx, "AsyncClient", InvalidClient)

    with pytest.raises(RuntimeError, match="non-WAVE"):
        await tts._generate_remote("Xin chao", str(output))

    assert output.read_bytes() == original


def test_write_wav_atomically_creates_valid_wav(tmp_path):
    output = tmp_path / "nested" / "scene.wav"

    tts._write_wav_atomically(str(output), _wav_bytes())

    assert output.exists()
    with wave.open(str(output), "rb") as wav:
        assert wav.getnframes() == 240
        assert wav.getframerate() == 24000


def test_wav_validator_rejects_truncated_payload():
    with pytest.raises(RuntimeError, match="truncated"):
        tts._validate_wav_bytes(_wav_bytes()[:-10])


def test_standalone_api_auth_and_wav_response(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(omnivoice_api, "OMNIVOICE_API_TOKEN", "secret")
    monkeypatch.setattr(omnivoice_api, "_load_model", lambda: None)
    monkeypatch.setattr(omnivoice_api, "_generate_wav_bytes", lambda *args: _wav_bytes())

    with TestClient(omnivoice_api.app) as client:
        unauthorized = client.post("/v1/tts", data={"text": "Xin chao"}, headers={})
        assert unauthorized.status_code == 401

        authorized = client.post(
            "/v1/tts",
            data={"text": "Xin chao", "speed": "1.0", "ref_text": "Mau transcript"},
            files={"ref_audio": ("reference.wav", _wav_bytes(), "audio/wav")},
            headers={"Authorization": "Bearer secret"},
        )
        assert authorized.status_code == 200
        assert authorized.headers["content-type"] == "audio/wav"
        assert authorized.content == _wav_bytes()


def test_standalone_api_rejects_oversized_request(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(omnivoice_api, "OMNIVOICE_API_TOKEN", "")
    monkeypatch.setattr(omnivoice_api, "OMNIVOICE_MAX_REQUEST_BYTES", 100)
    monkeypatch.setattr(omnivoice_api, "_load_model", lambda: None)

    with TestClient(omnivoice_api.app) as client:
        response = client.post("/v1/tts", data={"text": "x" * 1000})

    assert response.status_code == 413


def test_standalone_api_rejects_nonfinite_speed(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(omnivoice_api, "OMNIVOICE_API_TOKEN", "")
    monkeypatch.setattr(omnivoice_api, "_load_model", lambda: None)

    with TestClient(omnivoice_api.app) as client:
        response = client.post("/v1/tts", data={"text": "Xin chao", "speed": "nan"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remote_generation_bounds_reference_read(tmp_path, monkeypatch):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"x" * 11)

    monkeypatch.setattr(tts, "TTS_BACKEND", "remote")
    monkeypatch.setattr(tts, "TTS_REMOTE_URL", "http://127.0.0.1:8200")
    monkeypatch.setattr(tts, "TTS_REMOTE_MAX_BYTES", 10)

    with pytest.raises(RuntimeError, match="size limit"):
        await tts._generate_remote(
            "Xin chao",
            str(tmp_path / "scene.wav"),
            ref_audio=str(reference),
            ref_text="Mau transcript",
        )
