# LocalSub NPU

Offline Windows 11 AI subtitle generator using local Whisper/OpenVINO inference. No cloud AI token is required after model setup.

## Wave 1

- Tauri + React + TypeScript desktop UI scaffold
- Python local worker scaffold
- FFmpeg/OpenVINO diagnostics
- `NPU -> GPU -> CPU` auto device preference
- SRT validation and renderer
- Unit tests, Windows scripts, and GitHub Actions CI

Wave 1 deliberately does not yet extract audio, invoke Whisper, translate text, or package an installer.

## Architecture

```text
Tauri + React desktop UI
          |
          | local IPC / sidecar integration (next slice)
          v
Python local worker
  ├─ environment diagnostics
  ├─ FFmpeg extraction (next slice)
  ├─ OpenVINO Whisper inference (next slice)
  └─ SRT rendering
```

See [architecture](docs/architecture.md).

## Requirements

- Windows 11 x64
- Node.js 22 LTS+
- Python 3.11+
- Rust stable and Visual Studio C++ Build Tools for Tauri
- FFmpeg on `PATH` or configured via `FFMPEG_PATH`
- Intel NPU driver and OpenVINO runtime for NPU acceleration

## Quick start

```powershell
# Python worker
cd apps/inference
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
python -m app.main diagnostics

# Desktop UI
cd ..\desktop
npm install
npm run typecheck
npm run dev
```

Create local config from `.env.example`; do not commit `.env`.

## Device policy

`OPENVINO_DEVICE=auto` resolves in order `NPU`, `GPU`, then `CPU`. Explicit device selection must fail clearly if unavailable rather than silently using another device. Runtime logs in later waves will record requested and actual device selection.

## Privacy

- Source media remains local.
- No cloud API key is required for target offline inference.
- Do not commit models, source media, subtitles, logs, or secrets.

## Verification status

Scaffolding has not been executed on the target laptop in this workflow. Run `scripts/diagnostics.ps1` first, then validate the later Whisper workload on NPU, GPU, and CPU with identical short EN/VI fixtures.

## Roadmap

1. Wave 1: scaffold, diagnostics, subtitle renderer.
2. Wave 2: FFmpeg extraction and OpenVINO Whisper to SRT.
3. Wave 3: EN/VI translation, editor, VTT/TXT/JSON exports.
4. Wave 4: benchmark, installer, observability, release hardening.

## License

MIT. Models are not included and retain their own licenses.
