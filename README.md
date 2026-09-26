# LocalSub NPU

LocalSub NPU is an offline Windows 11 desktop application for producing subtitle files from video or audio. It is designed to prioritize Intel NPU execution through OpenVINO when available, while providing clear GPU/CPU fallback behavior.

The application never sends media to a cloud transcription service. Models are downloaded and stored locally by the user; after model setup, transcription can run offline without cloud AI tokens.

## Current scope (Wave 2)

- Wave 1: Tauri + React desktop UI shell, diagnostics, device policy, SRT renderer, CI
- **Wave 2 (this release):** end-to-end command-line pipeline in the Python worker
  - FFmpeg extraction to 16 kHz mono WAV (argument arrays, no shell strings)
  - OpenVINO GenAI `WhisperPipeline` on `NPU` / `GPU` / `CPU` with recorded fallback reason
  - Pre-converted Intel models (`OpenVINO/whisper-*-ov`) downloaded locally
  - Readable SRT cues (≤ 84 characters, ≤ 7 s, overlap repair)
  - Vietnamese or English transcription; `--task translate` for any speech → English
  - JSON Lines audit log per job (device, timings, real-time factor, error code — never transcript text)
  - Benchmark script comparing NPU vs GPU vs CPU

Not yet included: desktop UI wiring to the worker, English → Vietnamese translation, installer.

## Architecture

```text
Tauri + React desktop UI
          |
          | sidecar + JSON progress events (Wave 3)
          v
Python local worker  (python -m app.main ...)
  ├─ diagnostics
  ├─ models list | download
  └─ transcribe
       probing -> extracting_audio (FFmpeg) -> loading_model (OpenVINO NPU/GPU/CPU)
       -> transcribing (Whisper) -> rendering (SRT) -> completed | failed
```

Detailed design: [docs/architecture.md](docs/architecture.md).

## Requirements

- Windows 11 x64
- Node.js 22 LTS or later
- Python 3.11 or later
- Rust stable and the Visual Studio C++ Build Tools for Tauri
- FFmpeg available on `PATH`, or configured with `FFMPEG_PATH`
- Intel NPU driver and OpenVINO runtime for NPU acceleration

## Quick start (Windows 11, PowerShell)

```powershell
# 0. FFmpeg (once)
winget install --id Gyan.FFmpeg -e

# 1. Worker venv + OpenVINO GenAI + default model (whisper-small-int8, ~250 MB)
.\scripts\setup-wave2.ps1

# 2. Vietnamese subtitle on the NPU
.\scripts\transcribe.ps1 -Source "D:\Videos\meeting.mp4" -Language vi -Device NPU

# 3. Compare NPU / GPU / CPU on the same clip
.\scripts\benchmark-devices.ps1 -Source "D:\Videos\clip-60s.mp4" -Language vi
```

Output goes to `data\output\<name>.vi.srt`; the job log is `data\output\logs\jobs.jsonl`.

Direct worker usage:

```powershell
cd apps/inference
.\.venv\Scripts\python.exe -m app.main models list
.\.venv\Scripts\python.exe -m app.main models download whisper-large-v3-turbo-int8
.\.venv\Scripts\python.exe -m app.main transcribe D:\talk.mp4 --language vi --model whisper-large-v3-turbo-int8 --device GPU
.\.venv\Scripts\python.exe -m app.main transcribe D:\talk.mp4 --task translate   # -> English SRT
```

### Desktop UI (preview)

```powershell
cd apps/desktop
npm ci
npm run dev
```

## Useful commands

```powershell
# Machine prerequisites and local inference diagnostics
.\scripts\diagnostics.ps1

# Optional dependency bootstrap helper
.\scripts\bootstrap-windows.ps1

# Python tests (unit, no model needed)
cd apps/inference; pytest

# Real OpenVINO integration test
$env:LOCALSUB_IT_MEDIA="D:\clip.mp4"; $env:LOCALSUB_IT_MODEL="whisper-base-int8"; pytest -k integration -s

# Frontend checks
cd apps/desktop; npm run lint; npm run typecheck; npm run build
```

## Device behavior

`OPENVINO_DEVICE=auto` attempts `NPU`, then `GPU`, then `CPU`. This is a policy preference, not proof that all graph operations execute on an NPU. Every job logs the requested device, resolved device, attempted devices, OpenVINO version, model id, and fallback reason. The first NPU run compiles the model (can take a minute); compiled blobs are cached in `data/models/.ov-cache`, so later runs load much faster.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENVINO_DEVICE` | `auto` | `auto`, `NPU`, `GPU`, or `CPU` |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable or absolute path |
| `LOCALSUB_OUTPUT_DIR` | `./data/output` | Generated subtitle output directory |
| `LOCALSUB_MODEL_DIR` | `./data/models` | Local model cache directory |
| `LOCALSUB_LOG_LEVEL` | `INFO` | Local structured log level |

`.env` in the repository root is loaded automatically; real environment variables take precedence.

See [.env.example](.env.example).

## Privacy and security

- Source media stays on the local machine.
- No API key is required for the target offline workflow.
- Do not put credentials, tokens, source videos, audio files, or downloaded models into the repository.
- Each job appends an audit record to `data/output/logs/jobs.jsonl` with source path, output path, timestamps, model, requested/resolved device, timings, state, and error code. Transcript text is never logged.
- Models are downloaded once from Hugging Face (`OpenVINO/whisper-*-ov`); transcription itself runs offline.

## Verification status

- Unit tests (31) and lint pass on Python 3.11 and 3.14; the desktop app lints, type-checks, and builds.
- The full pipeline was run for real with `whisper-tiny-int8` on CPU (Linux CI sandbox, OpenVINO 2026.4): a 44 s MP4 produced a valid 8-cue SRT with real-time factor 0.02.
- **Pending:** a physical Intel NPU run on the target laptop (Core Ultra Series 2, Intel AI Boost). Run `scripts/benchmark-devices.ps1` and confirm `device_resolved = NPU` plus activity on Task Manager > NPU 0.

## Roadmap

1. Wave 1: scaffold, diagnostics, subtitle renderer.
2. Wave 2: FFmpeg extraction and OpenVINO Whisper transcription to SRT (CLI). ✅
3. Wave 3: desktop UI wired to the worker sidecar, EN→VI translation with a local LLM on NPU, editable subtitle table, VTT/TXT/JSON exports.
4. Wave 4: benchmark, installer, observability, and release hardening.

## License

MIT. Model files and their respective licenses are not included; each model must be obtained and used under its own license terms.
