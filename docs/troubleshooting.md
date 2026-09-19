# Troubleshooting

## FFmpeg missing

Install an FFmpeg build for Windows and add its `bin` folder to `PATH`, or set `FFMPEG_PATH` in `.env`. Restart the terminal, then run `scripts/diagnostics.ps1`.

## OpenVINO or NPU unavailable

Activate the intended Python environment, update the Intel NPU driver from the laptop vendor or Intel, reboot, inspect Device Manager/Task Manager, then run `python -m app.main diagnostics` from `apps/inference`. `OPENVINO_DEVICE=NPU` is intentionally strict; use `auto` only if fallback is acceptable.

## Task Manager shows 0% NPU

An idle NPU at 0% is normal. Verify a real inference workload and worker logs showing requested/resolved device. Preprocessing, decoding, and post-processing may still execute on CPU even when a model graph runs partly on NPU.

## Tauri build fails

Install Rust stable plus Visual Studio Build Tools with Desktop development with C++ and Windows SDK. Confirm `cargo --version`, then run `npm install` and `npm run build` in `apps/desktop`.

## Subtitle quality

Wave 1 does not transcribe media. In later waves, validate language selection, model variant, source audio quality, and FFmpeg extraction before tuning segmentation.
