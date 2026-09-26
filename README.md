# LocalSub NPU

LocalSub NPU is an offline Windows 11 desktop application for producing subtitle files from video or audio. It is designed to prioritize Intel NPU execution through OpenVINO when available, while providing clear GPU/CPU fallback behavior.

The application never sends media to a cloud transcription service. Models are downloaded and stored locally by the user; after model setup, transcription can run offline without cloud AI tokens.

## Current scope (Wave 3)

- Wave 1: Tauri + React desktop UI shell, diagnostics, device policy, SRT renderer, CI
- Wave 2: end-to-end CLI pipeline — FFmpeg → OpenVINO Whisper on NPU/GPU/CPU → SRT, audit log, benchmark
- **Wave 3 (this release):**
  - **Desktop app wired to the worker**: pick media with the native dialog, live progress stepper,
    actual device per stage (speech and translation), cancel, worker log
  - **EN ↔ VI translation with a local LLM** via OpenVINO GenAI `LLMPipeline`
    (default `qwen3-8b-int4-cw`, channel-wise INT4 for the NPU). Cues are translated in numbered
    batches with context, so timestamps never change; missing lines are retried and, if still
    missing, keep the source text and are counted in the audit log
  - **Editable subtitle table**: edit times, source and translation, split / merge / delete,
    live validation (overlaps, empty text, > 84 characters)
  - **Exports**: SRT, WebVTT, plain text and `*.localsub.json` project files (re-open to keep editing)
  - New CLI commands: `transcribe --translate-to`, `translate <file.srt>`, `export`

Not yet included: installer and bundled Python runtime (Wave 4).

## Architecture

```text
Tauri 2 desktop (React)  ──invoke──▶  Rust commands (allow-listed arguments, no shell)
        ▲                                   │ spawn: python -m app.main transcribe ... --events
        └──── job-event / job-log / job-exit ◀┘ stdout JSON Lines, stderr log

Python local worker
  probing → extracting_audio (FFmpeg) → loading_model → transcribing (Whisper, NPU/GPU/CPU)
  → translating (LLM, NPU/GPU/CPU, optional) → rendering (SRT/VTT/TXT/JSON) → completed | failed
```

The Whisper pipeline is released before the LLM is loaded so both fit comfortably in memory.
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

# 1. Worker venv + OpenVINO GenAI + Whisper small (~250 MB) + translation LLM (~5 GB)
.\scripts\setup-wave2.ps1 -TranslationModel qwen3-8b-int4-cw

# 2. English video -> English + Vietnamese subtitles, both on the NPU
.\scripts\transcribe.ps1 -Source "D:\Videos\webinar.mp4" -Language en -TranslateTo vi -Formats srt,vtt,json

# 3. Compare NPU / GPU / CPU for Whisper on the same clip
.\scripts\benchmark-devices.ps1 -Source "D:\Videos\clip-60s.mp4" -Language vi
```

Output goes to `data\output\<name>.en.srt`, `<name>.vi.srt`, `<name>.localsub.json`; the job log
is `data\output\logs\jobs.jsonl`.

### Desktop app

Prerequisites: Rust stable, Visual Studio C++ Build Tools, WebView2 (preinstalled on Windows 11),
and the worker venv from step 1.

```powershell
cd apps\desktop
npm ci
npm run tauri dev      # development window
npm run tauri build    # MSI/NSIS bundle in src-tauri\target\release\bundle
```

The app finds the worker at `apps\inference\.venv\Scripts\python.exe` by walking up from the
executable or working directory. Override with `LOCALSUB_WORKER_DIR` and `LOCALSUB_WORKER_PYTHON`.
`npm run dev` in a normal browser shows a clearly labelled preview with sample data.

### Direct worker usage

```powershell
cd apps\inference
.\.venv\Scripts\python.exe -m app.main models list
.\.venv\Scripts\python.exe -m app.main models download qwen2.5-7b-instruct-int4
.\.venv\Scripts\python.exe -m app.main transcribe D:\talk.mp4 --language vi --translate-to en --formats srt,json
.\.venv\Scripts\python.exe -m app.main translate D:\talk.en.srt --from en --to vi --device NPU
Get-Content D:\talk.localsub.json | .\.venv\Scripts\python.exe -m app.main export --output-base D:\final\talk --formats srt,vtt
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

# Real LLM translation test
$env:LOCALSUB_IT_LLM="qwen3-8b-int4-cw"; pytest -k real_translation -s

# Frontend and Tauri checks
cd apps/desktop; npm run lint; npm run typecheck; npm test; npm run build
cd src-tauri; cargo test
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
| `LOCALSUB_WORKER_DIR` | auto-detected | Desktop app: folder containing `app/main.py` |
| `LOCALSUB_WORKER_PYTHON` | `<worker>\.venv\Scripts\python.exe` | Desktop app: Python executable |

`.env` in the repository root is loaded automatically; real environment variables take precedence.

See [.env.example](.env.example).

## Privacy and security

- Source media stays on the local machine.
- No API key is required for the target offline workflow.
- Do not put credentials, tokens, source videos, audio files, or downloaded models into the repository.
- Each job appends an audit record to `data/output/logs/jobs.jsonl` with source path, output path, timestamps, model, requested/resolved device, timings, state, and error code. Transcript text is never logged.
- Models are downloaded once from Hugging Face (`OpenVINO/whisper-*-ov`, `OpenVINO/Qwen*-ov`); transcription and translation run offline.
- The desktop shell only exposes allow-listed commands; every device, language, model id and format is validated in Rust before the worker is spawned with an argument array (no shell). The Tauri capability grants only `core:default` and `dialog:default`.

## Verification status

- Python: 55 unit tests + lint pass on Python 3.11 and 3.14. Frontend: lint, type-check, 12 Vitest tests, build.
  Rust: `cargo check` and argument-validation tests (Linux sandbox and Windows CI).
- Real CPU run (Linux sandbox, OpenVINO 2026.4): 44 s MP4 → 8 English cues with `whisper-tiny-int8`,
  then EN → VI with `qwen3-0.6b-int4` in ~7 s, writing SRT/VTT/TXT/JSON. The 0.6B model proves the
  pipeline only; its Vietnamese is poor — use `qwen3-8b-int4-cw` or `qwen2.5-7b-instruct-int4`.
- Desktop UI reviewed visually in browser preview mode (sample data) at desktop and narrow widths.
- **Pending on the target laptop:** physical NPU runs for Whisper and the 8B LLM, and `npm run tauri dev`
  end to end. Check Task Manager > NPU 0 and `translation_device` in the job summary.

## Roadmap

1. Wave 1: scaffold, diagnostics, subtitle renderer.
2. Wave 2: FFmpeg extraction and OpenVINO Whisper transcription to SRT (CLI). ✅
3. Wave 3: desktop UI wired to the worker, EN↔VI translation with a local LLM on NPU, editable subtitle table, VTT/TXT/JSON exports. ✅
4. Wave 4: benchmark, installer, observability, and release hardening.

## License

MIT. Model files and their respective licenses are not included; each model must be obtained and used under its own license terms.
