# fk-gen-tts-template — Generate Voice Template

Create a reusable voice template for consistent narration across all scenes.

**IMPORTANT:** Always create a voice template BEFORE narrating scenes. Without a template, each scene generates with a slightly different voice. With a template, voice cloning ensures 100% consistency.

## Prerequisites

- GLA server running: `curl http://127.0.0.1:8100/health`
- OmniVoice installed in the Python environment used by the agent (see below)

### Installing OmniVoice

OmniVoice is a multilingual zero-shot TTS model (600+ languages) with voice cloning.
Source: https://github.com/tuannguyenhoangit-droid/OmniVoice

> **Windows users:** Run all setup commands inside **WSL** or **Git Bash** (not CMD/PowerShell). The project's `setup.sh` and all bash scripts require a Unix shell.

**Step 1 — Install PyTorch** (in the FlowKit launcher venv, or a separate venv if you set
`TTS_PYTHON_BIN`):

```bash
# macOS Apple Silicon (CPU — recommended for GLA, MPS produces gibberish)
$HOME/.venvs/flowkit/bin/python -m pip install torch==2.8.0 torchaudio==2.8.0

# Linux / WSL with NVIDIA GPU
python -m pip install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 \
  --extra-index-url https://download.pytorch.org/whl/cu128

# Linux / WSL CPU-only
python -m pip install torch==2.8.0 torchaudio==2.8.0
```

**Step 2 — Install OmniVoice** (choose one):

```bash
# From PyPI (stable)
$HOME/.venvs/flowkit/bin/python -m pip install omnivoice

# From source
$HOME/.venvs/flowkit/bin/python -m pip install \
  git+https://github.com/k2-fsa/OmniVoice.git

# Dev install
git clone https://github.com/k2-fsa/OmniVoice.git
cd OmniVoice
$HOME/.venvs/flowkit/bin/python -m pip install -e .
```

**Step 3 — Verify installation:**

```bash
$HOME/.venvs/flowkit/bin/python -c \
  'from omnivoice import OmniVoice; print("OmniVoice OK")'
```

FlowKit's local TTS subprocess defaults to the interpreter that launched FlowKit
(`sys.executable`). `TTS_PYTHON_BIN` is optional and is only needed when OmniVoice
is installed in another venv.

For remote GPU inference, run `agent/omnivoice_api.py` as a separate FastAPI service.
The FlowKit remote backend uploads `ref_audio` bytes and expects raw `audio/wav` bytes
back; it never sends a local filesystem path to the remote service.

**HuggingFace mirror** (if model download is slow):
```bash
export HF_ENDPOINT="https://hf-mirror.com"
```

**HuggingFace mirror** (if model download is slow):
```bash
export HF_ENDPOINT="https://hf-mirror.com"
```

## Workflow

### Step 1: Create Voice Template

**IMPORTANT:** Always use the **standard base transcript** for ALL templates.
This ensures `ref_text` is always known — no need to extract/transcribe later.

**Base transcript (English):**
> In the year twenty twenty-four, the world changed forever. Nations rose and fell, heroes emerged from the shadows, and ordinary people faced extraordinary challenges.

**When user specifies a language**, translate the base transcript to their language:

- Vietnamese → `"Năm hai nghìn không trăm hai mươi tư, thế giới thay đổi mãi mãi. Các quốc gia hưng thịnh và sụp đổ, anh hùng xuất hiện từ bóng tối, và những người bình thường đối mặt với thử thách phi thường."`
- Japanese → `"二千二十四年、世界は永遠に変わった。国々は興亡し、英雄は影から現れ、普通の人々は非凡な試練に直面した。"`
- Korean → `"이천이십사년, 세계는 영원히 변했습니다. 나라들이 흥망하고, 영웅들이 그림자에서 나타나고, 평범한 사람들이 비범한 도전에 직면했습니다."`
- (Any other language: translate the base transcript yourself)

**Why this text?** It's generic (not project-specific), covers varied phonemes (numbers, nouns, verbs), and is ~5s at normal speed — ideal for voice cloning reference.

```bash
# Example: Vietnamese template
curl -X POST http://127.0.0.1:8100/api/tts/templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "narrator_male_vn",
    "text": "Eo biển Hormuz, nơi hẹp nhất chỉ ba mươi ba ki-lô-mét. Hai mươi phần trăm lượng dầu thế giới đi qua đây mỗi ngày.",
    "instruct": "male, moderate pitch, young adult",
    "speed": 1.0
  }'
```

The `text` field serves dual purpose:
1. **During template creation:** OmniVoice speaks this text to generate the template WAV
2. **During scene narration:** Used as `ref_text` for voice cloning (phoneme alignment)

Same base transcript across all templates → `ref_text` is always known → consistent voice cloning without transcript extraction.

### Step 2: Listen & Verify

Open the returned `audio_path` and verify the voice matches your vision. If not, delete and recreate with different `instruct`.

### Step 3: Link to Project

```bash
curl -X PATCH http://127.0.0.1:8100/api/projects/<PID> \
  -H "Content-Type: application/json" \
  -d '{"narrator_ref_audio": "<audio_path from step 1>"}'
```

Or pass `template` name directly when narrating (recommended):
```bash
curl -X POST http://127.0.0.1:8100/api/videos/<VID>/narrate \
  -d '{"project_id": "<PID>", "template": "narrator_male_vn", "speed": 1.1}'
```

## Valid Instruct Terms

### English
- **Gender:** male, female
- **Age:** child, teenager, young adult, middle-aged, elderly
- **Pitch:** very low pitch, low pitch, moderate pitch, high pitch, very high pitch
- **Style:** whisper
- **Accent:** american accent, british accent, australian accent, canadian accent, indian accent, japanese accent, korean accent, chinese accent, russian accent, portuguese accent

### Tips
- Use comma + space between terms: `"male, low pitch, american accent"`
- Keep instruct short — 2-3 terms work best
- For Vietnamese narration, `"male, moderate pitch, young adult"` gives a clear documentary voice
- `speed: 1.1` gives slightly faster, more dynamic pacing

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tts/templates` | POST | Create voice template |
| `/api/tts/templates` | GET | List all templates |
| `/api/tts/templates/{name}` | GET | Get template details |
| `/api/tts/templates/{name}` | DELETE | Delete template |

## Important Notes

- Voice templates use **voice design** (instruct string) to generate an anchor voice
- When narrating scenes, the template WAV is used as **ref_audio** for voice cloning
- This ensures every scene sounds like the same narrator
- CPU mode only (MPS produces artifacts) — generation takes ~15-30s per template
- Template WAV is saved permanently in `output/_shared/tts_templates/`
