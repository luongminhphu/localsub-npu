# Model setup

## Wave 1 status

No model downloader or Whisper invocation is implemented in Wave 1. This document specifies the intended local-only model workflow so artifacts are not accidentally committed into the repository.

## Intended layout

```text
data/
  models/
    whisper-small-openvino-int8/
    translation-en-vi/
    translation-vi-en/
```

`data/` is excluded by `.gitignore`.

## Planned model policy

- Begin hardware verification with a compact Whisper OpenVINO model such as a base or small INT8 variant.
- Use a locally stored translation model for English-to-Vietnamese. Native Whisper translation is generally oriented toward English output, so it is not sufficient by itself for EN → VI.
- Store model identifier, source URL, revision, checksum, license, conversion recipe, and OpenVINO version in a local manifest.
- Require users to accept the upstream model license during model download/setup.

## OpenVINO device selection

Set `OPENVINO_DEVICE` in `.env`:

```text
OPENVINO_DEVICE=auto
```

`auto` asks the worker to attempt `NPU`, followed by `GPU`, followed by `CPU`. An explicit setting such as `NPU` is a hard requirement: if the NPU is unavailable, the job must fail with an actionable message rather than silently run elsewhere.

## Verification checklist

1. Install OpenVINO in the local Python environment when the Whisper slice is added.
2. Run `scripts/diagnostics.ps1` and retain the JSON output.
3. Confirm `NPU` appears in `available_devices`.
4. Run the same short audio fixture on NPU, GPU, and CPU.
5. Record elapsed time, real-time factor, peak memory, device resolution, and transcription quality.
6. Confirm no cloud transcription endpoint is called after model setup.
