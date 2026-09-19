# Troubleshooting

## `ffmpeg` is not found

- Install an FFmpeg build for Windows and add its `bin` directory to `PATH`.
- Alternatively set `FFMPEG_PATH` to an absolute executable path in `.env`.
- Restart the terminal after changing `PATH`.
- Run `scripts/diagnostics.ps1` again.

## OpenVINO or NPU is unavailable

- Confirm the Python environment with OpenVINO is activated when running the worker.
- Update the Intel NPU driver from your laptop vendor or Intel, then reboot.
- Check Windows Device Manager and Task Manager > Performance for an NPU entry.
- Run `python -m app.main diagnostics` from `apps/inference` and check `available_devices`.
- If `OPENVINO_DEVICE=NPU`, no automatic fallback is expected. Use `auto` only when fallback is acceptable.

## Task Manager reports 0% NPU utilization

An idle NPU showing 0% is normal. A valid verification requires a real inference workload and a worker log showing the requested/resolved device. Some graph operations, decoding, preprocessing, and post-processing can remain on CPU even when OpenVINO compiles part of a pipeline for NPU.

## Tauri build fails on Windows

- Install Rust stable using rustup.
- Install Visual Studio Build Tools with the Desktop development with C++ workload and Windows SDK.
- Confirm `cargo --version` works.
- Run `npm install` inside `apps/desktop`, then `npm run build`.

## Subtitles look incorrect

Wave 1 does not transcribe media. In later waves, first verify source language selection, model variant, audio quality, and FFmpeg extraction before changing subtitle segmentation settings.
