from __future__ import annotations

import gc
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
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
from app.segmentation import TimedText, build_cues, normalize_text
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
    translation_parallel: int | None = None  # batches per generate() call after tuning
    overlap: bool | None = None  # translation ran while Whisper was still transcribing
    overlap_reason: str | None = None
    translation_overlap_lines: int | None = None  # lines translated by the overlapping thread
    translation_tuning: list[list[float]] | None = None  # [[parallel, lines/s], ...]
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
    return Settings(
        device, settings.ffmpeg_path, settings.output_dir, settings.model_dir, settings.log_level
    )


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


OVERLAP_RAM_FACTOR = 1.3  # free RAM needed, as a multiple of the LLM size, to load it early


def stable_lines(segments: Sequence[TimedText], duration_ms: int | None) -> list[str]:
    """Cue texts that later chunks cannot change.

    A segment's cues depend only on its own text and duration, except that a segment with
    no end time borrows the next segment's start. So every cue before the newest segment is
    final; the newest segment's cues are left for the next chunk (or the final pass)."""
    starts = [s.start_ms for s in segments if normalize_text(s.text)]
    if not starts:
        return []
    newest = max(starts)
    return [c.text for c in build_cues(segments, duration_ms) if c.start_ms < newest]


def _load_translator(
    settings: Settings,
    request: JobRequest,
    record: JobRecord,
    llm_path: Path,
    available: tuple[str, ...],
    llm_factory: translation.LLMFactory | None,
) -> translation.LoadedTranslator:
    llm_settings = _settings_with_device(settings, request.translation_device)
    llm_kwargs = {"factory": llm_factory} if llm_factory else {}
    translator = translation.load_translator(
        llm_path,
        request.translation_model,
        llm_settings.device_preference,
        translation.candidate_devices(llm_settings.device_preference),
        available,
        cache_dir=settings.model_dir / ".ov-cache",
        **llm_kwargs,
    )
    record.translation_device = translator.device
    record.timings_ms["load_translation_model"] = translator.load_ms
    return translator


def _device_event(translator: translation.LoadedTranslator, done: int, total: int) -> dict:
    return {
        "type": "progress",
        "stage": "translating",
        "done": done,
        "total": total,
        "device": translator.device.resolved,
        "fallback_reason": translator.device.fallback_reason,
    }


def overlap_plan(
    request: JobRequest,
    asr_device: str | None,
    available: tuple[str, ...],
    llm_bytes: int = 0,
    free_bytes: int | None = None,
) -> tuple[bool, str]:
    """Whether translation may run on its own thread while Whisper transcribes.

    Only when the two models land on different devices (default: Whisper on NPU, LLM on
    GPU); on one device they would just take turns and compete for memory.
    ``LOCALSUB_OVERLAP=0`` disables it, ``force`` allows one device (benchmarks)."""
    mode = os.getenv("LOCALSUB_OVERLAP", "").strip().lower()
    if mode in ("0", "off", "false", "no"):
        return False, "disabled by LOCALSUB_OVERLAP"
    preference = request.translation_device or "auto"
    llm_device = next(
        (d for d in translation.candidate_devices(preference) if d in available),
        preference,
    )
    if mode != "force" and llm_device == asr_device:
        return False, f"speech and translation both on {asr_device}"
    if free_bytes is not None and llm_bytes and free_bytes < llm_bytes * OVERLAP_RAM_FACTOR:
        gib = 1024**3
        return False, (
            f"not enough free RAM to load translation early "
            f"({free_bytes / gib:.1f} GiB free, {llm_bytes * OVERLAP_RAM_FACTOR / gib:.1f} needed)"
        )
    return True, f"speech on {asr_device}, translation on {llm_device}"


class OverlapTranslation:
    """Loads the LLM and translates finished subtitle lines on its own thread while Whisper
    keeps transcribing. The main thread feeds lines, then calls ``finish``."""

    def __init__(
        self,
        load: Callable[[], translation.LoadedTranslator],
        target: str,
        send: Callable[[dict[str, Any]], None],
    ) -> None:
        self.load = load
        self.target = target
        self.send = send
        self.feed = translation.LineFeed()
        self.fed: list[str] = []
        self.diverged = False
        self.source: str | None = None
        self.language_known = threading.Event()
        self.translator: translation.LoadedTranslator | None = None
        self.result: translation.TranslationResult | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="translate-overlap", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            self.translator = self.load()
            self.language_known.wait()
            if self.source is None or self.feed.aborted:
                return
            self.send(_device_event(self.translator, 0, len(self.feed)))
            self.result = translation.translate_feed(
                self.translator,
                self.feed,
                self.source,
                self.target,
                on_progress=lambda done, total: self.send(
                    {"type": "progress", "stage": "translating", "done": done, "total": total}
                ),
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            self.error = exc

    def set_language(self, source: str | None) -> None:
        """Language of the speech once known; ``None`` = nothing to translate here."""
        if self.language_known.is_set():
            return
        if source in translation.LANGUAGE_NAMES and source != self.target:
            self.source = source
        self.language_known.set()

    def offer(self, stable: list[str]) -> None:
        """Queue lines that will not change any more. If an earlier line did change (it
        should not), stop feeding; ``finish`` translates the rest the normal way."""
        if self.diverged:
            return
        if stable[: len(self.fed)] != self.fed:
            self.diverged = True
            return
        new = stable[len(self.fed) :]
        if new:
            self.fed.extend(new)
            self.feed.extend(new)

    def cancel(self) -> None:
        self.feed.abort()
        self.language_known.set()

    def finish(self, texts: list[str]) -> int:
        """Feed the final lines, wait for the thread; returns how many leading ``texts`` the
        overlapped run translated (the rest still need translating)."""
        self.offer(texts)
        self.feed.close()
        self.language_known.set()
        self.thread.join()
        if isinstance(self.error, LocalSubError):
            raise self.error
        if self.error is not None:
            raise LocalSubError(
                ErrorCode.TRANSLATION_FAILED, f"Translation failed: {str(self.error)[:200]}"
            ) from self.error
        if self.result is None:
            return 0
        done = len(self.result.lines)
        return done if texts[:done] == self.fed[:done] else 0


def _translate(
    translator: translation.LoadedTranslator,
    texts: list[str],
    source_lang: str,
    target: str,
    send: Callable[[dict[str, Any]], None],
) -> translation.TranslationResult:
    send(_device_event(translator, 0, len(texts)))
    return translation.translate_lines(
        translator,
        texts,
        source_lang,
        target,
        on_progress=lambda done, total: send(
            {"type": "progress", "stage": "translating", "done": done, "total": total}
        ),
    )


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

    send_lock = threading.Lock()  # the overlap thread emits too; keep JSON lines whole

    def send(event: dict[str, Any]) -> None:
        if emit:
            with send_lock:
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
        if request.translate_to not in (None, *translation.TARGET_LANGUAGES):
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

        def speech_language(detected: str | None) -> str | None:
            if request.task == "translate":
                return "en"
            return request.language if request.language != "auto" else detected

        overlap: OverlapTranslation | None = None
        target = request.translate_to
        if target and llm_path is not None:
            record.overlap, record.overlap_reason = overlap_plan(
                request,
                model.device.resolved,
                available,
                translation.model_size_bytes(llm_path),
                translation.available_memory_bytes(),
            )
            if record.overlap:
                path = llm_path
                overlap = OverlapTranslation(
                    lambda: _load_translator(
                        settings, request, record, path, available, llm_factory
                    ),
                    target,
                    send,
                )
                overlap.start()

        def on_chunk(segments: tuple[TimedText, ...], detected: str | None) -> None:
            if overlap is None:
                return
            language = speech_language(detected)
            if language is not None or segments:
                overlap.set_language(language)
            overlap.offer(stable_lines(segments, record.source_duration_ms))

        transition(
            JobState.TRANSCRIBING,
            device=model.device.resolved,
            fallback_reason=model.device.fallback_reason,
        )
        send(
            {
                "type": "progress",
                "stage": "transcribing",
                "done": 0,
                "total": record.source_duration_ms or 0,
            }
        )
        try:
            result = whisper.transcribe(
                model,
                samples,
                request.language,
                request.task,
                on_progress=lambda done, total: send(
                    {"type": "progress", "stage": "transcribing", "done": done, "total": total}
                ),
                on_chunk=on_chunk,
            )
        except BaseException:
            if overlap is not None:
                overlap.cancel()
            raise
        asr_finished = time.perf_counter()
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
            if overlap is not None:
                overlap.cancel()
            raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, str(exc)) from exc

        source_lang = speech_language(record.detected_language)
        translations: list[str] | None = None
        if overlap is not None and not (cues and source_lang != target):
            overlap.cancel()  # nothing to translate; the thread exits after loading
            overlap = None
        if target and llm_path is not None and cues and source_lang != target:
            if source_lang not in translation.LANGUAGE_NAMES:
                raise LocalSubError(
                    ErrorCode.TRANSLATION_FAILED,
                    f"Cannot translate from detected language '{source_lang}'; set --language.",
                )
            transition(JobState.TRANSLATING, model_id=request.translation_model)
            texts = [c.text for c in cues]
            done = 0
            head: translation.TranslationResult | None = None
            translator = None
            if overlap is not None:
                done = overlap.finish(texts)
                translator = overlap.translator
                head = overlap.result if done else None  # a diverged run is discarded
                record.translation_overlap_lines = done
            if done < len(texts):
                if translator is None:
                    translator = _load_translator(
                        settings, request, record, llm_path, available, llm_factory
                    )
                tail = _translate(translator, texts[done:], source_lang, target, send)
            else:
                tail = translation.TranslationResult((), 0, 0)
            translations = [*(head.lines[:done] if head else ()), *tail.lines]
            record.translation_fallback_lines = (head.fallback_lines if head else 0) + (
                tail.fallback_lines
            )
            record.timings_ms["translation"] = (head.inference_ms if head else 0) + (
                tail.inference_ms
            )
            if overlap is not None:
                # Wall time translation added after Whisper finished: what overlap saves.
                record.timings_ms["translation_after_asr"] = int(
                    (time.perf_counter() - asr_finished) * 1000
                )
            tuned = tail if tail.parallel is not None else head
            record.translation_parallel = tuned.parallel if tuned else None
            record.translation_tuning = [
                list(t) for t in (head.tuning if head else ()) + tail.tuning
            ] or None
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
