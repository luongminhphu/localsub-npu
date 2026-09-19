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
