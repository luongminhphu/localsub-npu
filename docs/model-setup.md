# Model setup

## Available models

Intel publishes pre-converted OpenVINO IR models, so no PyTorch/Optimum export is required:

| Model id | Hugging Face repo | Notes |
|---|---|---|
| `whisper-tiny-int8` | `OpenVINO/whisper-tiny-int8-ov` | Smoke tests only |
| `whisper-base-int8` | `OpenVINO/whisper-base-int8-ov` | Fast, weak on Vietnamese |
| `whisper-small-int8` | `OpenVINO/whisper-small-int8-ov` | **Default**; first NPU target |
| `whisper-medium-int8` | `OpenVINO/whisper-medium-int8-ov` | Better Vietnamese, slower |
| `whisper-large-v3-turbo-int8` | `OpenVINO/whisper-large-v3-turbo-int8-ov` | Best quality/speed balance for Vietnamese; try GPU and NPU |
| `whisper-large-v3-int8` | `OpenVINO/whisper-large-v3-int8-ov` | Highest quality, slowest |

```powershell
cd apps/inference
.\.venv\Scripts\python.exe -m app.main models download whisper-small-int8
```

Models are stored in `LOCALSUB_MODEL_DIR` (default `data/models/<model-id>`), which is git-ignored. Check each model card for its license.

## Layout

```text
data/
  models/
    whisper-small-int8/        # OpenVINO IR + tokenizer/detokenizer
    qwen3-8b-int4-cw/          # translation LLM
    .ov-cache/                 # compiled NPU/GPU blobs (safe to delete)
  output/
    <name>.en.srt  <name>.vi.srt  <name>.ja.srt  <name>.*.vtt  <name>.*.txt
    <name>.localsub.json       # editable project (source + translation per cue)
    logs/jobs.jsonl
```

## Translation models

| Model id | Hugging Face repo | Size | Notes |
|---|---|---|---|
| `qwen3-8b-int4-cw` | `OpenVINO/Qwen3-8B-int4-cw-ov` | ~5 GB | **Default**; channel-wise INT4 recommended for Intel NPU |
| `qwen2.5-7b-instruct-int4` | `OpenVINO/Qwen2.5-7B-Instruct-int4-ov` | ~4.5 GB | Good EN↔VI; best on the Arc GPU |
| `qwen3-4b-int4` | `OpenVINO/Qwen3-4B-int4-ov` | ~2.5 GB | Balanced |
| `qwen2.5-1.5b-instruct-int4` | `OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov` | ~1 GB | Fast drafts, needs review |
| `qwen3-0.6b-int4` | `OpenVINO/Qwen3-0.6B-int4-ov` | ~0.5 GB | Pipeline smoke tests only |

Whisper `--task translate` can still produce English directly from any speech. For EN → VI (or
VI → EN with better quality) use `--translate-to`:

- Cues are sent in batches of 8 as numbered lines, with the 2 previous lines as context.
- Qwen3 "thinking" is disabled through the chat template; any `<think>` block is stripped.
- On NPU the LLM compiles static shapes (`MAX_PROMPT_LEN=1536`, `MIN_RESPONSE_LEN=768`).
  The first compile takes minutes; `.ov-cache` makes later loads fast.
- If a line is missing from the model output it is retried alone; if still missing the source
  text is kept and `translation_fallback_lines` in the job summary is incremented.

## NPU notes

- OpenVINO GenAI documents Whisper tiny/base/small/large as supported on NPU with the standard pipeline.
- The first NPU compile is slow; the `.ov-cache` directory makes subsequent loads fast.
- `OPENVINO_DEVICE=NPU` (or `--device NPU`) is a hard requirement; `auto` falls back and records why.

## Verification checklist

1. `.\scripts\diagnostics.ps1` shows `NPU` in `available_devices`.
2. `.\scripts\benchmark-devices.ps1 -Source <60 s clip> -Language vi` completes on NPU, GPU, and CPU.
3. Record load time (first and cached), inference time, real-time factor, and subtitle quality per device.
4. Task Manager > Performance > NPU 0 shows utilization during the NPU run.
5. No network traffic during transcription after the model download.

## Korean source (0.10.0)

- Speech language `ko`; translation directions ko→en, ko→vi (targets stay `en`/`vi`).
- Recommended Whisper: `whisper-large-v3-turbo-int8` (or `whisper-medium-int8`).
- Korean cues split on words (Korean uses spaces), max 36 characters, reading-speed estimate 85 ms/char (Netflix Korean guide: 2 x 16 chars, 12 cps).
- The prompt adds a Korean rule (sentences span lines and drop subjects; one output line per input line; render honorifics/speech levels naturally; keep names). No romanized examples: Qwen3 0.6B copied them into every line.

## Japanese source (0.4.0)

- Speech languages: `auto`, `en`, `vi`, `ja`. Translation directions: en↔vi, ja→en, ja→vi (targets are `en`/`vi` only).
- Recommended Whisper for Japanese: `whisper-large-v3-turbo-int8` (or `whisper-medium-int8`); tiny/base/small often pick the wrong kanji.
- Japanese cues split by character, max 32 characters, preferring `。、！？` or the spaces Whisper inserts between sentences; reading-speed estimate 150 ms/char.
- The prompt adds a Japanese rule (sentences span several lines; keep one output line per input line; keep names; render honorifics naturally). Full-width numbering (`１．`) is normalised.
- Implausible outputs (placeholder echoes such as `N. text`, repetition loops, no letters, >4x source length) are rejected, retried once per line, then fall back to the source text.
