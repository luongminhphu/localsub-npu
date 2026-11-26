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

## `MODEL_NOT_FOUND`

Run `python -m app.main models download <model-id>` from `apps/inference`, or `.\scripts\setup-wave2.ps1 -Model <model-id>`. Confirm `LOCALSUB_MODEL_DIR` points to the same folder for download and transcription.

## `MODEL_INCOMPLETE` / `DOWNLOAD_FAILED`

The model folder exists but the download did not finish (missing `.bin` weights or leftover `*.incomplete`
files). Press **Resume** (Tải tiếp) in the Models section, or run `models download <model-id>` again: Hugging Face
resumes the partial files. A finished download writes `.localsub-model.json` in the model folder.

## `INSUFFICIENT_DISK`

The worker needs the model size plus 10% free in `LOCALSUB_MODEL_DIR`. Free space or point the variable to
another drive.

## `MODEL_COMPILE_FAILED` on NPU

- Update the Intel NPU driver (Lenovo Vantage / Intel driver page) and reboot.
- Delete `data/models/.ov-cache` and retry.
- Try a smaller model (`whisper-base-int8`) to separate driver problems from model size limits.
- Use `--device auto` to fall back to GPU/CPU; the reason is recorded in the job summary.

## First NPU run is slow

Expected: OpenVINO compiles the model for the NPU on first use. Later runs reuse `data/models/.ov-cache`.

## Subtitles look incorrect

- Set `--language vi` explicitly for Vietnamese audio instead of auto-detection.
- Use a larger model (`whisper-medium-int8` or `whisper-large-v3-turbo-int8`) for Vietnamese.
- Repeated phrases on long silence are a known Whisper hallucination pattern; trim silence or use a larger model.

## Installer: "Error opening file for writing ... worker\_internal\VCRUNTIME140.dll"

A `localsub-worker.exe` from the install folder is still running, usually started as an MCP
server by Claude Desktop, Claude Code, VS Code or Perplexity, so its DLLs are locked. Installers
built with `windows/hooks.nsh` stop every program running from `worker\` before copying files,
and since 0.22.4 keep stopping it for up to ~10 s, because an AI app may start its MCP server
again right away (the install log lists each process stopped). If it still fails, something
else holds the file (an AI app that restarts the server immediately, antivirus scanning it): do
not press Ignore. Quit the AI apps (Claude Desktop: system tray → Quit; close Claude Code, VS Code,
Perplexity), run `Get-Process localsub-worker | Stop-Process -Force` in PowerShell, then press
Retry. If Ignore was pressed, run the installer again once everything is closed.
