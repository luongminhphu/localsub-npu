# Architecture

## Goal

LocalSub NPU produces subtitle files from locally selected media on Windows 11. The system prioritizes Intel NPU execution through OpenVINO, but must preserve correctness when NPU is unavailable by reporting an explicit outcome rather than silently misrepresenting execution.

## Design decisions

- **Desktop shell:** Tauri + React + TypeScript keeps the desktop package smaller than an Electron-first approach while retaining a modern UI.
- **Inference boundary:** Python isolates FFmpeg, OpenVINO, and model-specific dependencies from the UI. The initial implementation is a local worker; it is not an internet-facing API.
- **Storage:** Wave 1 uses local files. SQLite may be introduced for persistent job history after a complete job vertical slice exists.
- **Privacy:** Source paths, extracted audio, model cache, and output captions are local. Cloud transcription APIs are out of scope.
- **Device policy:** `auto` resolves in order NPU → GPU → CPU. Explicit device selection must fail clearly if unavailable; it must not silently use another device.

## Target job lifecycle

```text
queued
  -> probing
  -> extracting_audio
  -> loading_model
  -> transcribing
  -> translating (optional)
  -> rendering
  -> completed | failed | cancelled
```

Each job must record start/end timestamps, requested and resolved device, model id/version, output path, state transitions, and sanitized error codes. Do not log transcript contents by default.

## Data model

```text
SubtitleCue
- index: integer, sequential from 1
- start_ms: non-negative integer
- end_ms: integer greater than start_ms
- text: non-empty normalized text

DeviceResolution
- requested: auto | NPU | GPU | CPU
- resolved: NPU | GPU | CPU | null
- available_devices: string[]
- fallback_reason: string | null
```

## Error semantics

| Condition | Error code / behavior |
|---|---|
| Unsupported input | `INPUT_UNSUPPORTED`; reject before processing |
| FFmpeg missing | `FFMPEG_UNAVAILABLE`; provide configured path guidance |
| OpenVINO missing | `OPENVINO_UNAVAILABLE`; allow diagnostics to complete |
| Explicit NPU missing | `DEVICE_UNAVAILABLE`; do not change device automatically |
| Auto device fallback | Continue with resolved device and expose fallback reason |
| Invalid cue timeline | `SUBTITLE_VALIDATION_FAILED`; block export |
| Cancel request | `CANCELLED`; cleanup temporary audio safely |

## Security boundaries

- Accept only local file paths selected through the native dialog.
- Do not build shell command strings from user-provided paths; use argument arrays for FFmpeg in the future worker.
- Store model downloads outside repository paths.
- Never log environment variables, access tokens, raw media, or full transcripts at INFO level.
- Future file deletion must only target generated temp files within the configured workspace.

## Observability

The worker must emit structured local logs per job: `job_id`, `state`, `elapsed_ms`, `model_id`, `device_requested`, `device_resolved`, `source_duration_ms`, and sanitized `error_code`. The UI must show a timestamp, local timezone, runtime/device status, and output file location.

## Wave 3: desktop ↔ worker protocol

The Tauri Rust layer exposes seven commands: `worker_location`, `run_diagnostics`, `list_models`,
`start_job`, `cancel_job`, `export_project`, `load_project`. `start_job` validates every field
against allow-lists, then spawns `python -m app.main transcribe <file> ... --events` with an argument
vector, `PYTHONUTF8=1` and (on Windows) `CREATE_NO_WINDOW`.

| Channel | Payload |
|---|---|
| `job-event` | one JSON object per stdout line: `state`, `progress` (translation `done/total`), `summary`, `error` |
| `job-log` | human-readable stderr lines |
| `job-exit` | `{ code, cancelled }` |

The `completed` event carries the full project (`localsub.project/v1`) so the editor opens
without re-reading files. Transcript text travels only over the local pipe and into output files;
the audit log still contains no text.

Cancel terminates the worker process. Temporary WAVs from killed jobs are pruned by the next job
(files older than 6 hours in `output/.tmp`).

### Project file (`*.localsub.json`)

```json
{
  "schema": "localsub.project/v1",
  "source_media": "D:/Videos/talk.mp4",
  "source_language": "en",
  "target_language": "vi",
  "cues": [{ "start_ms": 0, "end_ms": 2500, "text": "Hello", "translation": "Xin chào" }]
}
```

`export` re-validates the cue timeline (same rules as the UI) and writes
`<base>.<lang>.<srt|vtt|txt>` for each track plus `<base>.localsub.json`.

### Translation

`app/translation.py` loads an OpenVINO GenAI `LLMPipeline` through the same NPU → GPU → CPU
fallback as Whisper (`app/devices.py`). Batches of 8 numbered cues are translated with greedy
decoding; the parser maps `N. text` lines back to cue indexes so timing is never touched by the model.
