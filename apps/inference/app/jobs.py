from __future__ import annotations

import gc
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import audio, translation, whisper
from app.audit import AuditLog
from app.config import Settings
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution, JobState
from app.project import FORMATS, export_project, project_from_cues
from app.segmentation import build_cues
from app.subtitles import validate_cues

EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class JobRequest:
    source: Path
    language: str = "auto"
    task: str = "transcribe"
    model_id: str = whisper.DEFAULT_MODEL
    output: Path | None = None
    translate_to: str | None = None
    translation_model: str = translation.DEFAULT_TRANSLATION_MODEL
    translation_device: str | None = None
    formats: tuple[str, ...] = ("srt",)


@dataclass
class JobRecord:
    """Audit record. Must never contain transcript or translation text."""

    job_id: str
    source_path: Path
    model_id: str
    language: str
    task: str
    device_requested: str
    state: JobState = JobState.QUEUED
    transitions: list[dict[str, Any]] = field(default_factory=list)
    output_path: Path | None = None
    outputs: list[Path] = field(default_factory=list)
    device: DeviceResolution | None = None
    device_attempts: tuple[str, ...] = ()
    translate_to: str | None = None
    translation_model: str | None = None
    translation_device: DeviceResolution | None = None
    translation_fallback_lines: int | None = None
    openvino_version: str | None = None
    source_duration_ms: int | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    real_time_factor: float | None = None
    cue_count: int | None = None
    detected_language: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


def output_base(settings: Settings, request: JobRequest) -> tuple[Path, bool]:
    """Return (base path, whether to add a language suffix to the source track)."""
    if request.output is not None:
        out = request.output
        return (out.with_suffix("") if out.suffix.lower() == ".srt" else out), False
    return settings.output_dir / request.source.stem, True


def _settings_with_device(settings: Settings, device: str | None) -> Settings:
    if not device:
        return settings
    return Settings(device, settings.ffmpeg_path, settings.output_dir, settings.model_dir,
                    settings.log_level)


def _openvino_version() -> tuple[str | None, tuple[str, ...]]:
    try:
        import openvino as ov
    except ImportError as exc:
        raise LocalSubError(ErrorCode.OPENVINO_UNAVAILABLE, "OpenVINO is not installed.") from exc
    return getattr(ov, "__version__", "unknown"), tuple(ov.Core().available_devices)


STALE_TEMP_SECONDS = 6 * 3600


def prune_stale_temp(temp_dir: Path, now: float | None = None) -> int:
    """Remove WAVs left behind by jobs that were killed (desktop Cancel terminates the worker)."""
    if not temp_dir.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - STALE_TEMP_SECONDS
    removed = 0
    for wav in temp_dir.glob("*.wav"):
        try:
            if wav.stat().st_mtime < cutoff:
                wav.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def run_job(
    settings: Settings,
    request: JobRequest,
    emit: EventSink | None = None,
    runtime_probe: Callable[[], tuple[str | None, tuple[str, ...]]] = _openvino_version,
    pipeline_factory: whisper.PipelineFactory | None = None,
    ffmpeg_runner: audio.Runner | None = None,
    llm_factory: translation.LLMFactory | None = None,
) -> JobRecord:
    record = JobRecord(
        job_id=uuid.uuid4().hex[:12],
        source_path=request.source,
        model_id=request.model_id,
        language=request.language,
        task=request.task,
        device_requested=settings.device_preference,
        translate_to=request.translate_to,
        translation_model=request.translation_model if request.translate_to else None,
    )
    audit = AuditLog(settings.output_dir / "logs" / "jobs.jsonl")
    job_started = time.perf_counter()
    temp_audio = settings.output_dir / ".tmp" / f"{record.job_id}.wav"

    def send(event: dict[str, Any]) -> None:
        if emit:
            emit({"job_id": record.job_id, "at": datetime.now(UTC), **event})

    def transition(state: JobState, **extra: Any) -> None:
        record.state = state
        record.transitions.append({"state": state, "at": datetime.now(UTC)})
        send({"type": "state", "state": state, **extra})

    def timed(name: str, started: float) -> None:
        record.timings_ms[name] = int((time.perf_counter() - started) * 1000)

    prune_stale_temp(temp_audio.parent)
    try:
        transition(JobState.PROBING)
        unknown = [f for f in request.formats if f not in FORMATS]
        if unknown or not request.formats:
            raise LocalSubError(ErrorCode.INPUT_UNSUPPORTED, f"Unknown export formats: {unknown}")
        if request.translate_to not in (None, "vi", "en"):
            raise LocalSubError(ErrorCode.INPUT_UNSUPPORTED, "translate_to must be vi or en")
        source = audio.validate_source(request.source)
        record.source_path = source
        ffmpeg = audio.resolve_ffmpeg(settings.ffmpeg_path)
        model_path = whisper.ensure_model_present(
            whisper.model_path_for(settings.model_dir, request.model_id), request.model_id
        )
        llm_path: Path | None = None
        if request.translate_to:
            llm_path = translation.ensure_model_present(
                translation.model_path_for(settings.model_dir, request.translation_model),
                request.translation_model,
            )
        record.openvino_version, available = runtime_probe()

        transition(JobState.EXTRACTING_AUDIO)
        t = time.perf_counter()
        kwargs = {"runner": ffmpeg_runner} if ffmpeg_runner else {}
        audio.extract_audio(ffmpeg, source, temp_audio, **kwargs)
        record.source_duration_ms = audio.wav_duration_ms(temp_audio)
        samples = audio.load_wav_samples(temp_audio)
        timed("extract", t)

        transition(JobState.LOADING_MODEL, model_id=request.model_id)
        load_kwargs = {"factory": pipeline_factory} if pipeline_factory else {}
        model = whisper.load_model(
            model_path,
            request.model_id,
            settings.device_preference,
            settings.preferred_devices(),
            available,
            cache_dir=settings.model_dir / ".ov-cache",
            **load_kwargs,
        )
        record.device = model.device
        record.device_attempts = model.attempts
        record.timings_ms["load_model"] = model.load_ms

        transition(
            JobState.TRANSCRIBING,
            device=model.device.resolved,
            fallback_reason=model.device.fallback_reason,
        )
        result = whisper.transcribe(model, samples, request.language, request.task)
        record.timings_ms["inference"] = result.inference_ms
        record.detected_language = result.detected_language
        if record.source_duration_ms:
            record.real_time_factor = round(result.inference_ms / record.source_duration_ms, 4)
        del model, samples
        gc.collect()

        cues = build_cues(result.segments, record.source_duration_ms)
        try:
            validate_cues(cues)
        except ValueError as exc:
            raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, str(exc)) from exc

        if request.task == "translate":
            source_lang: str | None = "en"
        elif request.language != "auto":
            source_lang = request.language
        else:
            source_lang = record.detected_language

        translations: list[str] | None = None
        target = request.translate_to
        if target and llm_path is not None and cues and source_lang != target:
            if source_lang not in translation.LANGUAGE_NAMES:
                raise LocalSubError(
                    ErrorCode.TRANSLATION_FAILED,
                    f"Cannot translate from detected language '{source_lang}'; set --language.",
                )
            llm_settings = _settings_with_device(settings, request.translation_device)
            transition(JobState.TRANSLATING, model_id=request.translation_model)
            llm_kwargs = {"factory": llm_factory} if llm_factory else {}
            translator = translation.load_translator(
                llm_path,
                request.translation_model,
                llm_settings.device_preference,
                llm_settings.preferred_devices(),
                available,
                cache_dir=settings.model_dir / ".ov-cache",
                **llm_kwargs,
            )
            record.translation_device = translator.device
            record.timings_ms["load_translation_model"] = translator.load_ms
            send(
                {
                    "type": "progress",
                    "stage": "translating",
                    "done": 0,
                    "total": len(cues),
                    "device": translator.device.resolved,
                    "fallback_reason": translator.device.fallback_reason,
                }
            )
            translated = translation.translate_lines(
                translator,
                [c.text for c in cues],
                source_lang,
                target,
                on_progress=lambda done, total: send(
                    {"type": "progress", "stage": "translating", "done": done, "total": total}
                ),
            )
            translations = list(translated.lines)
            record.translation_fallback_lines = translated.fallback_lines
            record.timings_ms["translation"] = translated.inference_ms
            del translator
            gc.collect()

        transition(JobState.RENDERING)
        t = time.perf_counter()
        project = project_from_cues(
            cues,
            translations,
            source_language=source_lang,
            target_language=target if translations else None,
            source_media=str(source),
        )
        base, suffix_source = output_base(settings, request)
        record.outputs = export_project(
            project, base, request.formats, suffix_source_language=suffix_source
        )
        srts = [p for p in record.outputs if p.suffix == ".srt"]
        record.output_path = (srts or record.outputs)[0].resolve()
        record.cue_count = len(cues)
        timed("render", t)

        transition(
            JobState.COMPLETED,
            output_path=record.output_path,
            outputs=record.outputs,
            cue_count=len(cues),
            project=project.to_dict(),
        )
    except LocalSubError as exc:
        record.error_code = exc.code
        record.error_message = exc.message
        transition(JobState.FAILED, error_code=exc.code, error_message=exc.message)
    except KeyboardInterrupt:
        transition(JobState.CANCELLED)
        raise
    finally:
        try:
            temp_audio.unlink(missing_ok=True)
        except OSError:
            pass
        record.timings_ms["total"] = int((time.perf_counter() - job_started) * 1000)
        record.finished_at = datetime.now(UTC)
        try:
            audit.write(record)
        except OSError:
            send({"type": "warning", "message": "audit log write failed"})

    return record
