# Model setup

## Wave 1 status

No downloader or Whisper invocation exists yet. This document defines the local-only path so model artifacts are never committed.

## Intended local layout

```text
data/
  models/
    whisper-small-openvino-int8/
    translation-en-vi/
    translation-vi-en/
```

`data/` is gitignored.

## Planned policy

- Start verification with a compact OpenVINO Whisper base/small INT8 model.
- English-to-Vietnamese requires a local translation model because Whisper's native translate path is generally English-oriented.
- Persist model identifier, source, revision, checksum, license, conversion recipe, and OpenVINO version in a local manifest.
- Require explicit upstream license acceptance during model setup.

## Device verification

1. Install OpenVINO in the local Python environment when Wave 2 is added.
2. Run `scripts/diagnostics.ps1`.
3. Confirm `NPU` is listed in `available_devices`.
4. Benchmark the same short EN/VI fixture on NPU, GPU, and CPU.
5. Capture elapsed time, real-time factor, memory, device resolution, and quality.
6. Confirm no cloud transcription endpoint is used after model setup.
