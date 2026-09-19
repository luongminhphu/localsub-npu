# Architecture

## Goal

LocalSub NPU produces subtitle files from locally selected media on Windows 11. It prioritizes Intel NPU execution through OpenVINO and reports explicit device outcomes rather than silently misrepresenting execution.

## Decisions

- **Desktop:** Tauri + React + TypeScript.
- **Worker:** Python isolates FFmpeg, OpenVINO, and model-specific dependencies.
- **Storage:** Wave 1 uses local files; SQLite is deferred until a complete job slice exists.
- **Privacy:** source paths, extracted audio, model cache, and captions remain local.
- **Device policy:** `auto` resolves NPU → GPU → CPU. An explicit device fails if unavailable.

## Lifecycle

```text
queued -> probing -> extracting_audio -> loading_model -> transcribing
      -> translating (optional) -> rendering -> completed | failed | cancelled
```

Future jobs must record timestamps, selected/resolved device, model/version, output path, state transitions, and sanitized error codes. Transcript content must not be logged by default.

## Error behavior

| Condition | Behavior |
|---|---|
| Unsupported input | `INPUT_UNSUPPORTED` before processing |
| FFmpeg missing | `FFMPEG_UNAVAILABLE` with setup guidance |
| OpenVINO missing | `OPENVINO_UNAVAILABLE`; diagnostics remains usable |
| Explicit NPU missing | `DEVICE_UNAVAILABLE`; no hidden fallback |
| Auto fallback | Continue and surface resolved device and reason |
| Invalid cue timeline | `SUBTITLE_VALIDATION_FAILED`; block export |
| Cancellation | `CANCELLED`; remove only generated temp files |

## Security and observability

Use native file selection, pass FFmpeg arguments as arrays, keep models outside the repo, and never log environment variables, raw media, or full transcript at INFO. Future structured logs should carry `job_id`, `state`, `elapsed_ms`, `model_id`, `device_requested`, `device_resolved`, `source_duration_ms`, and sanitized `error_code`.
