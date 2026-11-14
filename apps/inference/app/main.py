from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app import analysis, history, mcp_http, mcp_server, model_store, power, translation, whisper
from app.audit import to_jsonable
from app.config import Settings, load_env_file
from app.diagnostics import build_diagnostic_report
from app.errors import ErrorCode, LocalSubError
from app.models import JobState
from app.project import FORMATS, export_project, parse_project, project_from_cues

ALL_MODELS: dict[str, tuple[str, str]] = {
    **{m: ("asr", r) for m, r in whisper.MODEL_CATALOG.items()},
    **{m: ("translation", r) for m, r in translation.TRANSLATION_CATALOG.items()},
}
DEVICES = ["auto", "NPU", "GPU", "CPU"]


def _print_json(payload: Any) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), flush=True)


def _formats(value: str) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(v.strip().lower() for v in value.split(",") if v.strip()))
    bad = [v for v in items if v not in FORMATS]
    if bad or not items:
        raise argparse.ArgumentTypeError(f"formats must be a comma list of {', '.join(FORMATS)}")
    return items


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= number <= analysis.MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {analysis.MAX_LIMIT}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LocalSub NPU local worker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diagnostics", help="Report FFmpeg/OpenVINO/device status")

    models = sub.add_parser("models", help="List or download OpenVINO models")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_sub.add_parser("list")
    download = models_sub.add_parser("download", help="Download or resume a model")
    download.add_argument("model_id", choices=sorted(ALL_MODELS))
    download.add_argument(
        "--events", action="store_true", help="Print JSON Lines progress events on stdout"
    )
    remove = models_sub.add_parser("remove", help="Delete a downloaded model folder")
    remove.add_argument("model_id", choices=sorted(ALL_MODELS))

    hist = sub.add_parser("history", help="Show recent jobs from the local audit log")
    hist.add_argument("--limit", type=int, default=20)

    an = sub.add_parser("analyze", help="Find performance problems in the local job log")
    an.add_argument("--limit", type=_limit, default=analysis.DEFAULT_LIMIT)
    an.add_argument("--format", choices=["json", "markdown"], default="json")
    an.add_argument("--language", choices=["vi", "en"], default="vi", help="markdown language")
    an.add_argument(
        "--redact", action="store_true", help="Hide media paths (default for the MCP server)"
    )

    mcp = sub.add_parser("mcp", help="Run the read-only MCP server on stdio for AI assistants")
    mcp.add_argument(
        "--no-redact", action="store_true", help="Show real media file names to the AI client"
    )
    mcp.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP on 127.0.0.1 (Perplexity custom connector) instead of stdio",
    )
    mcp.add_argument("--port", type=int, default=8765, help="HTTP port (1024-65535, 0 = any)")
    mcp.add_argument(
        "--events", action="store_true", help="Print JSON status events (with the key) on stdout"
    )
    mcp.add_argument("--rotate-token", action="store_true", help="Generate a new HTTP API key")
    mcp.add_argument(
        "--parent-pid", type=int, default=0, help="Stop when this process exits (desktop app)"
    )

    tr = sub.add_parser("transcribe", help="Generate subtitles from local media")
    tr.add_argument("source", type=Path)
    tr.add_argument("--language", choices=sorted(whisper.LANGUAGES), default="auto")
    tr.add_argument(
        "--task",
        choices=sorted(whisper.TASKS),
        default="transcribe",
        help="'translate' makes Whisper produce English directly",
    )
    tr.add_argument("--model", default=whisper.DEFAULT_MODEL, choices=sorted(whisper.MODEL_CATALOG))
    tr.add_argument("--device", choices=DEVICES, help="Override OPENVINO_DEVICE for Whisper")
    tr.add_argument(
        "--translate-to",
        choices=list(translation.TARGET_LANGUAGES),
        help="Add a translated track (LLM)",
    )
    tr.add_argument(
        "--translation-model",
        default=translation.DEFAULT_TRANSLATION_MODEL,
        choices=sorted(translation.TRANSLATION_CATALOG),
    )
    tr.add_argument("--translation-device", choices=DEVICES, help="Device for the translation LLM")
    tr.add_argument("--formats", type=_formats, default=("srt",), help="e.g. srt,vtt,txt,json")
    tr.add_argument("--output", type=Path, help="Output .srt path for the source track")
    tr.add_argument("--events", action="store_true", help="Stream JSON Lines events to stdout")
    tr.add_argument(
        "--allow-sleep",
        action="store_true",
        help="Let Windows sleep / throttle the worker while the job runs (default: keep awake)",
    )
    tr.add_argument(
        "--keep-display-on", action="store_true", help="Also keep the screen on during the job"
    )

    tl = sub.add_parser("translate", help="Translate an existing SRT file with the local LLM")
    tl.add_argument("source", type=Path)
    tl.add_argument(
        "--from", dest="source_language", choices=list(translation.SOURCE_LANGUAGES), required=True
    )
    tl.add_argument(
        "--to", dest="target_language", choices=list(translation.TARGET_LANGUAGES), required=True
    )
    tl.add_argument(
        "--model",
        default=translation.DEFAULT_TRANSLATION_MODEL,
        choices=sorted(translation.TRANSLATION_CATALOG),
    )
    tl.add_argument("--device", choices=DEVICES)
    tl.add_argument("--formats", type=_formats, default=("srt", "json"))
    tl.add_argument("--events", action="store_true")

    ex = sub.add_parser("export", help="Render an edited localsub project JSON to files")
    ex.add_argument("--input", default="-", help="Project JSON path, or '-' for stdin")
    ex.add_argument("--output-base", type=Path, required=True, help="Path without extension")
    ex.add_argument("--formats", type=_formats, default=("srt",))
    return parser


def _with_device(settings: Settings, device: str | None) -> Settings:
    if not device:
        return settings
    return Settings(
        device, settings.ffmpeg_path, settings.output_dir, settings.model_dir, settings.log_level
    )


def _event_printer(events: bool):
    def emit(event: dict[str, Any]) -> None:
        if events:
            print(json.dumps(to_jsonable(event), ensure_ascii=False), flush=True)
            return
        if event.get("type") == "progress":
            print(f"  translating {event['done']}/{event['total']}", file=sys.stderr, flush=True)
        elif event.get("type") == "state":
            extra = event.get("device") or event.get("model_id") or event.get("error_message") or ""
            print(f"[{event['state']}] {extra}".rstrip(), file=sys.stderr, flush=True)

    return emit


def _cmd_transcribe(args: argparse.Namespace, settings: Settings) -> int:
    from app.jobs import JobRequest, run_job

    settings = _with_device(settings, args.device)
    record = run_job(
        settings,
        JobRequest(
            source=args.source,
            language=args.language,
            task=args.task,
            model_id=args.model,
            output=args.output,
            translate_to=args.translate_to,
            translation_model=args.translation_model,
            translation_device=args.translation_device,
            formats=args.formats,
            keep_awake=not args.allow_sleep and power.env_flag("LOCALSUB_KEEP_AWAKE", True),
            keep_display_on=args.keep_display_on
            or power.env_flag("LOCALSUB_KEEP_DISPLAY_ON", False),
        ),
        emit=_event_printer(args.events),
    )
    summary = {
        "job_id": record.job_id,
        "state": record.state,
        "output_path": record.output_path,
        "outputs": record.outputs,
        "model_id": record.model_id,
        "device_requested": record.device_requested,
        "device_resolved": record.device.resolved if record.device else None,
        "fallback_reason": record.device.fallback_reason if record.device else None,
        "translate_to": record.translate_to,
        "translation_model": record.translation_model,
        "translation_device": (
            record.translation_device.resolved if record.translation_device else None
        ),
        "translation_fallback_lines": record.translation_fallback_lines,
        "translation_fallback_reasons": record.translation_fallback_reasons,
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
        print(
            json.dumps(to_jsonable({"type": "summary", "summary": summary}), ensure_ascii=False),
            flush=True,
        )
    else:
        _print_json(summary)
    code = 0 if record.state == JobState.COMPLETED else 1
    if translation.ABANDONED_PIPELINES:
        # A GPU/NPU driver died during translation: freeing that pipeline (also at interpreter
        # shutdown) would call into the broken driver and can hang, so leave without cleanup.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code


def _cmd_translate(args: argparse.Namespace, settings: Settings) -> int:
    from app.jobs import _openvino_version
    from app.subtitles import parse_srt

    settings = _with_device(settings, args.device)
    emit = _event_printer(args.events)
    source = args.source
    cues = parse_srt(source.read_text(encoding="utf-8-sig"))
    if not cues:
        raise LocalSubError(ErrorCode.INPUT_UNSUPPORTED, "No subtitle cues found in input.")
    path = translation.ensure_model_present(
        translation.model_path_for(settings.model_dir, args.model), args.model
    )
    _, available = _openvino_version()
    translator = translation.load_translator(
        path,
        args.model,
        settings.device_preference,
        settings.preferred_devices(),
        available,
        cache_dir=settings.model_dir / ".ov-cache",
    )
    emit({"type": "state", "state": JobState.TRANSLATING, "device": translator.device.resolved})
    result = translation.translate_lines(
        translator,
        [c.text for c in cues],
        args.source_language,
        args.target_language,
        on_progress=lambda d, t: emit(
            {"type": "progress", "stage": "translating", "done": d, "total": t}
        ),
    )
    project = project_from_cues(
        cues, list(result.lines), args.source_language, args.target_language, None
    )
    stem = source.name[: -len(".srt")] if source.name.lower().endswith(".srt") else source.stem
    for lang in translation.SOURCE_LANGUAGES:
        if stem.endswith(f".{lang}"):
            stem = stem[: -len(lang) - 1]
    outputs = export_project(project, source.with_name(stem), args.formats, include_source=False)
    _print_json(
        {
            "outputs": outputs,
            "device": translator.device,
            "fallback_lines": result.fallback_lines,
            "fallback_reasons": dict(result.fallback_reasons),
            "timings_ms": {"load": translator.load_ms, "translation": result.inference_ms},
        }
    )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, f"Invalid JSON: {exc}") from exc
    outputs = export_project(parse_project(payload), args.output_base, args.formats)
    _print_json({"outputs": outputs})
    return 0


def _cmd_models(args: argparse.Namespace, settings: Settings) -> int:
    if args.models_command == "list":
        _print_json(model_store.list_models(settings.model_dir))
        return 0
    if args.models_command == "remove":
        _print_json(model_store.remove(settings.model_dir, args.model_id))
        return 0

    def emit(event: dict[str, Any]) -> None:
        print(json.dumps(to_jsonable(event), ensure_ascii=False), flush=True)

    result = model_store.download(
        settings.model_dir, args.model_id, emit=emit if args.events else None
    )
    payload = {
        "type": "summary",
        "model_id": result.model_id,
        "kind": result.kind,
        "path": result.path,
        "status": result.status,
        "size_bytes": result.size_bytes,
        "already_installed": result.already_installed,
    }
    if args.events:
        emit(payload)
    else:
        _print_json(payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Vietnamese text over Windows pipes

    args = build_parser().parse_args(argv)
    load_env_file()
    settings = Settings.from_env()

    try:
        if args.command == "diagnostics":
            report = build_diagnostic_report(settings)
            payload = asdict(report)
            payload["output_dir"] = str(settings.output_dir)
            payload["model_dir"] = str(settings.model_dir)
            payload["installed_models"] = model_store.installed_models(settings.model_dir)
            _print_json(payload)
            return 0

        if args.command == "models":
            return _cmd_models(args, settings)

        if args.command == "history":
            _print_json(
                history.recent_jobs(settings.output_dir / "logs" / "jobs.jsonl", args.limit)
            )
            return 0
        if args.command == "analyze":
            report = analysis.analyze(
                settings.output_dir / "logs" / "jobs.jsonl", args.limit, redact=args.redact
            )
            if args.format == "markdown":
                print(analysis.optimization_plan(report, args.language), end="", flush=True)
            else:
                _print_json(report)
            return 0
        if args.command == "mcp":
            if args.http:
                if args.port != 0 and not 1024 <= args.port <= 65535:
                    print("localsub mcp: --port must be 1024-65535 (or 0)", file=sys.stderr)
                    return 2
                return mcp_http.serve_http(
                    settings,
                    port=args.port,
                    redact=not args.no_redact,
                    events=args.events,
                    rotate=args.rotate_token,
                    parent_pid=args.parent_pid,
                )
            return mcp_server.serve(settings, redact=not args.no_redact)
        if args.command == "transcribe":
            return _cmd_transcribe(args, settings)
        if args.command == "translate":
            return _cmd_translate(args, settings)
        if args.command == "export":
            return _cmd_export(args)
    except LocalSubError as exc:
        payload = {"type": "error", "error_code": exc.code, "error_message": exc.message}
        print(json.dumps(to_jsonable(payload), ensure_ascii=False), file=sys.stderr, flush=True)
        if getattr(args, "events", False):
            print(json.dumps(to_jsonable(payload), ensure_ascii=False), flush=True)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
