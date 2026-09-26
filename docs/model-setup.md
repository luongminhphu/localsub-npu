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
    .ov-cache/                 # compiled NPU/GPU blobs (safe to delete)
  output/
    <name>.vi.srt
    logs/jobs.jsonl
```

## Translation

Whisper `--task translate` only produces **English**. English → Vietnamese needs a separate local model (planned for Wave 3, e.g. a small INT4 LLM on the NPU via OpenVINO GenAI `LLMPipeline`).

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
