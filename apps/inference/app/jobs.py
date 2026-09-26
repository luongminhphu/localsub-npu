from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import audio, whisper
from app.audit import AuditLog
from app.config import Settings
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution, JobState
from app.segmentation import build_cues
from app.subtitles import render_srt

EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class JobRequest:
    source: Path
    language: str = "auto"
    task: str = "transcribe"
    model_id: str = whisper.DEFAULT_MODEL
    output: Path | None = None


@dataclass
class JobRecord:
    job_id: str
    source_path: Path
    model_id: str
    language: str
    task: str
    device_requested: str
    state: JobState = JobState.QUEUED
    transitions: list[dict[str, Any]] = field(default_factory=list)
    output_path: Path | None = None
    device: DeviceResolution | None = None
    device_attempts: tuple[str, ...] = ()
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


def default_output_path(settings: Settings, request: JobRequest) -> Path:
    lang = "en" if request.task == "translate" else request.language
    suffix = "" if lang == "auto" else f".{lang}"
    return settings.output_dir / f"{request.source.stem}{suffix}.srt"


def _openvino_version() -> tuple[str | None, tuple[str, ...]]:
    try:
        import openvino as ov
    except ImportError as exc:
        raise LocalSubError(ErrorCode.OPENVINO_UNAVAILABLE, "OpenVINO is not installed.") from exc
    return getattr(ov, "__version__", "unknown"), tuple(ov.Core().available_devices)


def run_job(
    settings: Settings,
    request: JobRequest,
    emit: EventSink | None = None,
    runtime_probe: Callable[[], tuple[str | None, tuple[str, ...]]] = _openvino_version,
    pipeline_factory: whisper.PipelineFactory | None = None,
    ffmpeg_runner: audio.Runner | None = None,
) -> JobRecord:
    record = JobRecord(
        job_id=uuid.uuid4().hex[:12],
        source_path=request.source,
        model_id=request.model_id,
        language=request.language,
        task=request.task,
        device_requested=settings.device_preference,
    )
    audit = AuditLog(settings.output_dir / "logs" / "jobs.jsonl")
    job_started = time.perf_counter()
    temp_audio = settings.output_dir / ".tmp" / f"{record.job_id}.wav"

    def transition(state: JobState, **extra: Any) -> None:
        record.state = state
        event = {"job_id": record.job_id, "state": state, "at": datetime.now(UTC), **extra}
        record.transitions.append({"state": state, "at": event["at"]})
        if emit:
            emit(event)

    def timed(name: str, started: float) -> None:
        record.timings_ms[name] = int((time.perf_counter() - started) * 1000)

    try:
        transition(JobState.PROBING)
        source = audio.validate_source(request.source)
        record.source_path = source
        ffmpeg = audio.resolve_ffmpeg(settings.ffmpeg_path)
        model_path = whisper.ensure_model_present(
            whisper.model_path_for(settings.model_dir, request.model_id), request.model_id
        )
        record.openvino_version, available = runtime_probe()

        transition(JobState.EXTRACTING_AUDIO)
        t = time.perf_counter()
        kwargs = {"runner": ffmpeg_runner} if ffmpeg_runner else {}
        audio.extract_audio(ffmpeg, source, temp_audio, **kwargs)
        record.source_duration_ms = audio.wav_duration_ms(temp_audio)
        samples = audio.load_wav_samples(temp_audio)
        timed("extract", t)

        transition(JobState.LOADING_MODEL)
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

        transition(JobState.RENDERING)
        t = time.perf_counter()
        cues = build_cues(result.segments, record.source_duration_ms)
        try:
            srt = render_srt(cues)
        except ValueError as exc:
            raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, str(exc)) from exc
        output = request.output or default_output_path(settings, request)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(srt, encoding="utf-8")
        except OSError as exc:
            raise LocalSubError(ErrorCode.OUTPUT_WRITE_FAILED, str(exc)) from exc
        record.output_path = output.resolve()
        record.cue_count = len(cues)
        timed("render", t)

        transition(JobState.COMPLETED, output_path=record.output_path, cue_count=len(cues))
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
            # Audit failure must never mask a finished subtitle or the original error.
            if emit:
                emit({"job_id": record.job_id, "warning": "audit log write failed"})

    return record
