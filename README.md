# LocalSub NPU

LocalSub NPU is an offline Windows 11 desktop application for producing subtitle files from video or audio. It is designed to prioritize Intel NPU execution through OpenVINO when available, while providing clear GPU/CPU fallback behavior.

The application never sends media to a cloud transcription service. Models are downloaded and stored locally by the user; after model setup, transcription can run offline without cloud AI tokens.

## Wave 1 scope

- React + TypeScript + Tauri desktop UI shell
- Python local worker foundation
- FFmpeg and OpenVINO environment diagnostics
- Device preference resolution: `NPU` -> `GPU` -> `CPU`
- SRT domain model, validation, and renderer
- Unit tests and CI checks
- Windows bootstrap and diagnostics scripts

Wave 1 intentionally does not yet invoke Whisper inference, extract audio, translate text, or package a Windows installer. Those capabilities are the next vertical slices.

## Architecture

```text
Tauri + React desktop UI
          |
          | local IPC / sidecar integration (next slice)
          v
Python local worker
  ├─ environment diagnostics
  ├─ FFmpeg audio extraction (next slice)
  ├─ OpenVINO Whisper inference (next slice)
  └─ SRT/VTT rendering
```

Detailed design: [docs/architecture.md](docs/architecture.md).

## Requirements

- Windows 11 x64
- Node.js 22 LTS or later
- Python 3.11 or later
- Rust stable and the Visual Studio C++ Build Tools for Tauri
- FFmpeg available on `PATH`, or configured with `FFMPEG_PATH`
- Intel NPU driver and OpenVINO runtime for NPU acceleration

## Quick start

### 1. Configure the Python worker

```powershell
cd apps/inference
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
python -m app.main diagnostics
```

### 2. Configure the desktop UI

```powershell
cd apps/desktop
npm install
npm run typecheck
npm run dev
```

### 3. Create local configuration

```powershell
Copy-Item .env.example .env
```

Update model and output paths in `.env` if desired. Do not commit `.env`.

## Useful commands

```powershell
# Machine prerequisites and local inference diagnostics
.\scripts\diagnostics.ps1

# Optional dependency bootstrap helper
.\scripts\bootstrap-windows.ps1

# Python tests
cd apps/inference; pytest

# Frontend checks
cd apps/desktop; npm run lint; npm run typecheck; npm run build
```

## Device behavior

`OPENVINO_DEVICE=auto` attempts `NPU`, then `GPU`, then `CPU`. This is a policy preference, not proof that all graph operations execute on an NPU. The future inference worker will log the requested target, resolved device, OpenVINO version, model identifier, and fallback reason for every job.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENVINO_DEVICE` | `auto` | `auto`, `NPU`, `GPU`, or `CPU` |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable or absolute path |
| `LOCALSUB_OUTPUT_DIR` | `./data/output` | Generated subtitle output directory |
| `LOCALSUB_MODEL_DIR` | `./data/models` | Local model cache directory |
| `LOCALSUB_LOG_LEVEL` | `INFO` | Local structured log level |

See [.env.example](.env.example).

## Privacy and security

- Source media stays on the local machine.
- No API key is required for the target offline workflow.
- Do not put credentials, tokens, source videos, audio files, or downloaded models into the repository.
- Future jobs will persist an audit record locally with source path, output path, timestamps, selected model, requested device, resolved device, state, and error metadata. The transcript itself is not logged by default.

## Verification status

The repository contains scaffolding and tests, but it has not been executed against this laptop through this repository workflow. The first required hardware verification is to run `scripts/diagnostics.ps1`, then test the future Whisper slice on `NPU`, `GPU`, and `CPU` with the same short EN and VI fixtures.

## Roadmap

1. Wave 1: scaffold, diagnostics, subtitle renderer.
2. Wave 2: FFmpeg extraction and OpenVINO Whisper transcription to SRT.
3. Wave 3: EN/VI translation, editable subtitle table, VTT/TXT/JSON exports.
4. Wave 4: benchmark, installer, observability, and release hardening.

## License

MIT. Model files and their respective licenses are not included; each model must be obtained and used under its own license terms.
