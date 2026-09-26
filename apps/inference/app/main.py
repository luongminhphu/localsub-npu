from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.audit import to_jsonable
from app.config import Settings, load_env_file
from app.diagnostics import build_diagnostic_report
from app.errors import LocalSubError
from app.models import JobState


def _print_json(payload: Any) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    from app import whisper

    parser = argparse.ArgumentParser(description="LocalSub NPU local worker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diagnostics", help="Report FFmpeg/OpenVINO/device status")

    models = sub.add_parser("models", help="List or download OpenVINO Whisper models")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_sub.add_parser("list")
    download = models_sub.add_parser("download")
    download.add_argument("model_id", choices=sorted(whisper.MODEL_CATALOG))

    tr = sub.add_parser("transcribe", help="Generate an SRT subtitle from local media")
    tr.add_argument("source", type=Path)
    tr.add_argument("--language", choices=sorted(whisper.LANGUAGES), default="auto")
    tr.add_argument(
        "--task",
        choices=sorted(whisper.TASKS),
        default="transcribe",
        help="'translate' produces English subtitles from any spoken language",
    )
    tr.add_argument("--model", default=whisper.DEFAULT_MODEL, choices=sorted(whisper.MODEL_CATALOG))
    tr.add_argument(
        "--device", choices=["auto", "NPU", "GPU", "CPU"], help="Override OPENVINO_DEVICE"
    )
    tr.add_argument("--output", type=Path, help="Output .srt path")
    tr.add_argument("--events", action="store_true", help="Stream JSON progress events to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    from app import whisper
    from app.jobs import JobRequest, run_job

    args = build_parser().parse_args(argv)
    load_env_file()
    settings = Settings.from_env()

    if args.command == "diagnostics":
        report = build_diagnostic_report(settings)
        payload = asdict(report)
        payload["output_dir"] = str(settings.output_dir)
        payload["model_dir"] = str(settings.model_dir)
        payload["installed_models"] = [
            m for m in whisper.MODEL_CATALOG if (settings.model_dir / m).is_dir()
        ]
        _print_json(payload)
        return 0

    if args.command == "models":
        if args.models_command == "list":
            _print_json(
                [
                    {
                        "id": m,
                        "source": repo,
                        "installed": (settings.model_dir / m).is_dir(),
                        "path": settings.model_dir / m,
                    }
                    for m, repo in whisper.MODEL_CATALOG.items()
                ]
            )
            return 0
        try:
            path = whisper.download_model(settings.model_dir, args.model_id)
        except LocalSubError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _print_json({"model_id": args.model_id, "path": path})
        return 0

    if args.command == "transcribe":
        if args.device:
            settings = Settings(
                device_preference=args.device,
                ffmpeg_path=settings.ffmpeg_path,
                output_dir=settings.output_dir,
                model_dir=settings.model_dir,
                log_level=settings.log_level,
            )

        def emit(event: dict[str, Any]) -> None:
            if args.events:
                print(json.dumps(to_jsonable(event), ensure_ascii=False), flush=True)
            else:
                state = event.get("state", "")
                extra = event.get("device") or event.get("error_message") or ""
                print(f"[{state}] {extra}".rstrip(), file=sys.stderr, flush=True)

        record = run_job(
            settings,
            JobRequest(
                source=args.source,
                language=args.language,
                task=args.task,
                model_id=args.model,
                output=args.output,
            ),
            emit=emit,
        )
        summary = {
            "job_id": record.job_id,
            "state": record.state,
            "output_path": record.output_path,
            "model_id": record.model_id,
            "device_requested": record.device_requested,
            "device_resolved": record.device.resolved if record.device else None,
            "fallback_reason": record.device.fallback_reason if record.device else None,
            "openvino_version": record.openvino_version,
            "source_duration_ms": record.source_duration_ms,
            "timings_ms": record.timings_ms,
            "real_time_factor": record.real_time_factor,
            "cue_count": record.cue_count,
            "detected_language": record.detected_language,
            "error_code": record.error_code,
            "error_message": record.error_message,
        }
        if args.events:
            print(json.dumps(to_jsonable({"summary": summary}), ensure_ascii=False), flush=True)
        else:
            _print_json(summary)
        return 0 if record.state == JobState.COMPLETED else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
