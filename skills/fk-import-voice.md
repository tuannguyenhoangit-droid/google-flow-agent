# fk-import-voice — Import Existing Voice as Template

Register an existing WAV file as a reusable voice template for narration. Auto-transcribes the audio and registers it in the template system.

## When to Use

- You have a real voice recording (WAV) you want to use for narration
- You want to clone a specific voice rather than designing one with `instruct`
- Complementary to `/fk-gen-tts-template` which creates synthetic voices

## Prerequisites

- GLA server running: `curl http://127.0.0.1:8100/health`
- `faster-whisper` installed in `/opt/homebrew/bin/python3.10`
- WAV file placed in `output/_shared/tts_templates/`

## Workflow

### Step 1: Locate the WAV file

```bash
ls -la output/_shared/tts_templates/*.wav
```

The user should have already placed their WAV file here. File should be:
- Format: WAV (any encoding — pcm_s16le, pcm_f32le, etc.)
- Duration: 3-10s ideal for voice cloning
- Content: Clear speech, minimal background noise

### Step 2: Transcribe with faster-whisper

Use `large-v3` model for accurate transcription. **Always use `/opt/homebrew/bin/python3.10`** — it has torch + faster-whisper installed.

```bash
/opt/homebrew/bin/python3.10 -c "
from faster_whisper import WhisperModel
model = WhisperModel('large-v3', device='cpu')
segments, info = model.transcribe('<WAV_PATH>', beam_size=5)
print(f'Language: {info.language} ({info.language_probability:.0%})')
for seg in segments:
    print(seg.text.strip())
" 2>&1 | grep -v '\[ctranslate2\]'
```

**IMPORTANT:** This runs on CPU and takes ~60s for large-v3 model loading + transcription. Be patient.

Show the transcript to the user and ask them to confirm or correct it. Accurate `ref_text` is critical for voice cloning quality.

### Step 3: Register template in templates.json

After user confirms transcript:

```bash
python3 -c "
import json
from pathlib import Path

templates_dir = Path('output/_shared/tts_templates')
meta_file = templates_dir / 'templates.json'
wav_path = str(templates_dir / '<FILENAME>.wav')

meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
meta['<TEMPLATE_NAME>'] = {
    'name': '<TEMPLATE_NAME>',
    'audio_path': wav_path,
    'text': '<CONFIRMED_TRANSCRIPT>',
    'instruct': '<VOICE_DESCRIPTION>',
    'duration': <DURATION>,
}
meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
print('Template registered')
"
```

Template name: derive from filename (e.g., `vi_male_narrator.wav` → `vi_male_narrator`).

### Step 4: Verify via API

```bash
curl -s http://127.0.0.1:8100/api/tts/templates
# Should list the new template
```

### Step 5: Test voice clone

Generate a short test sentence using the imported voice:

```bash
curl -s -X POST http://127.0.0.1:8100/api/tts/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "<SHORT_TEST_SENTENCE_IN_SAME_LANGUAGE>",
    "instruct": "<VOICE_DESCRIPTION>",
    "ref_audio": "<WAV_PATH>",
    "ref_text": "<CONFIRMED_TRANSCRIPT>"
  }'
```

Play the output for user to verify voice quality matches the original.

## Naming Convention

| Language | Template name pattern | Example |
|----------|----------------------|---------|
| Vietnamese | `vi_{gender}_narrator` | `vi_male_narrator` |
| English | `en_{gender}_narrator` | `en_female_narrator` |
| Japanese | `ja_{gender}_narrator` | `ja_male_narrator` |
| Multi-lang | `{primary_lang}_{gender}_narrator` | `vi_male_narrator` |

## Notes

- **Transcript accuracy matters** — `ref_text` must match the audio closely for good voice cloning. Always confirm with the user.
- **CPU by default** — whisper large-v3 and local OmniVoice both run on CPU (MPS produces artifacts). For GPU speed, run OmniVoice as a remote service and set `TTS_BACKEND=remote` (see `fk-gen-tts-template.md`); transcription still runs locally on CPU.
- **3-10s audio** — shorter clips lack enough voice characteristics; longer clips slow down cloning.
- **One template per voice** — don't register multiple templates for the same voice. Update instead.
