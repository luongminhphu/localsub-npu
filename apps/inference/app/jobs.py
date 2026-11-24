from __future__ import annotations

import dataclasses
import gc
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import audio, power, translation, whisper
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
    keep_awake: bool = True  # hold off idle sleep and EcoQoS throttling while the job runs
    keep_display_on: bool = False


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
    # Why those lines stayed untranslated: missing / placeholder / loop / no_letters / too_long.
    translation_fallback_reasons: dict[str, int] | None = None
    # Japanese moan/gasp lines transliterated without the LLM (never counted as fallback).
    translation_interjection_lines: int | None = None
    translation_parallel: int | None = None  # batches per generate() call after tuning
    power: dict[str, Any] | None = None  # keep-awake / throttling state applied for the job
    overlap: bool | None = None  # translation ran while Whisper was still transcribing
    overlap_reason: str | None = None
    # NPU/GPU driver resets survived during speech recognition: at_ms, from, to, error.
    asr_recoveries: list[dict[str, Any]] = field(default_factory=list)
    # Translation moved off a device whose driver died (e.g. GPU CL_OUT_OF_RESOURCES):
    # at_line, from, to, ok, error.
    translation_recoveries: list[dict[str, Any]] = field(default_factory=list)
    translation_overlap_lines: int | None = None  # lines translated by the overlapping thread
    # First subtitle line that changed after it was handed to the overlapping translator
    # (None = no change). Lines from the last whole batch before it are still reused.
    translation_overlap_diverged_at: int | None = None
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


def stable_lines(
    segments: Sequence[TimedText], duration_ms: int | None, horizon_ms: int | None = None
) -> list[str]:
    """Cue texts that later chunks cannot change.

    A segment's cues depend only on its own text and duration, except that a segment with
    no (or a non-positive) end time borrows the next segment's start. ``horizon_ms`` is
    where the next audio piece begins: every later segment starts there or after it. A
    segment is final when it starts before the horizon and either has its own end or its
    next segment starts no later than the horizon. Whisper pads each window to 30 s, so a
    short last window can report a start past the piece end; such a segment (and anything
    after it) waits for the next chunk. Without a horizon the newest start is used, which
    holds back only the newest segment."""
    spoken = sorted(
        ((max(0, s.start_ms), s.end_ms) for s in segments if normalize_text(s.text)),
        key=lambda seg: seg[0],
    )
    if not spoken:
        return []
    horizon = spoken[-1][0] if horizon_ms is None else horizon_ms
    cutoff: int | None = None
    for i, (start, end) in enumerate(spoken):
        if start >= horizon:
            cutoff = start
            break
        if end is None or end <= start:
            following = spoken[i + 1][0] if i + 1 < len(spoken) else None
            if following is None or following > horizon:
                cutoff = start
                break
    cues = build_cues(segments, duration_ms)
    return [c.text for c in cues if cutoff is None or c.start_ms < cutoff]


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


RECOVERY_DELAY_S = 2.0  # let the driver finish its reset before reloading


def _translator_after_lost(
    settings: Settings,
    request: JobRequest,
    record: JobRecord,
    llm_path: Path,
    available: tuple[str, ...],
    llm_factory: translation.LLMFactory | None,
    lost: translation.TranslatorLost,
    at_line: int,
) -> translation.LoadedTranslator:
    """Load the translator on the CPU after its GPU/NPU driver died mid-run.

    Only when the translation device is Auto: an explicit device never falls back. The CPU
    shares no driver with the GPU/NPU, so it is safe to use while the broken pipeline stays
    abandoned (see ``translation.ABANDONED_PIPELINES``)."""
    source = lost.device or "device"
    entry: dict[str, Any] = {"at_line": at_line, "from": source, "to": "CPU", "ok": False}
    requested = (request.translation_device or settings.device_preference or "auto").lower()
    reason = None
    if requested != "auto":
        reason = f"translation device is set to {requested.upper()}; choose Auto or CPU"
    elif source == "CPU":
        reason = "the CPU itself failed"
    elif "CPU" not in {d.upper().split(".")[0] for d in available}:
        reason = "no CPU device reported by OpenVINO"
    if reason:
        entry["error"] = "NOT_RECOVERED"
        record.translation_recoveries.append(entry)
        raise LocalSubError(
            ErrorCode.TRANSLATION_FAILED, f"{lost.message[:300]} (not retried: {reason})"
        ) from lost
    llm_kwargs = {"factory": llm_factory} if llm_factory else {}
    try:
        cpu = translation.load_translator(
            llm_path,
            request.translation_model,
            "CPU",
            ["CPU"],
            available,
            cache_dir=settings.model_dir / ".ov-cache",
            **llm_kwargs,
        )
    except LocalSubError as exc:
        entry["error"] = exc.code
        record.translation_recoveries.append(entry)
        raise
    entry["ok"] = True
    record.translation_recoveries.append(entry)
    record.translation_device = DeviceResolution(
        requested=requested,
        resolved="CPU",
        available_devices=available,
        fallback_reason=f"{source} driver failed at line {at_line + 1}; continued on CPU",
    )
    record.timings_ms["reload_translation_model"] = (
        record.timings_ms.get("reload_translation_model", 0) + cpu.load_ms
    )
    return cpu


def _join(a: translation.TranslationResult, b: translation.TranslationResult):
    reasons: dict[str, int] = {}
    for key, n in (*a.fallback_reasons, *b.fallback_reasons):
        reasons[key] = reasons.get(key, 0) + n
    return translation.TranslationResult(
        (*a.lines, *b.lines),
        a.fallback_lines + b.fallback_lines,
        a.inference_ms + b.inference_ms,
        parallel=b.parallel if b.parallel is not None else a.parallel,
        tuning=(*a.tuning, *b.tuning),
        fallback_reasons=tuple(sorted(reasons.items())),
        interjection_lines=a.interjection_lines + b.interjection_lines,
    )


def _recover_asr(
    settings: Settings,
    request: JobRequest,
    record: JobRecord,
    failed: whisper.LoadedModel,
    at_ms: int,
    available: tuple[str, ...],
    overlapping: bool,
    send: EventSink,
    pipeline_factory: whisper.PipelineFactory | None,
) -> whisper.LoadedModel | None:
    """Reload Whisper after its device was lost and return the new model (or None).

    First the same device is reloaded (an NPU reset is often transient). With ``auto``,
    GPU then CPU follow. An explicit device never moves, matching the no-silent-fallback rule."""
    lost = failed.device.resolved or "?"
    previous = [r["to"] for r in record.asr_recoveries]
    order = [lost]
    if settings.device_preference == "auto":
        # GPU first even while it translates: large Whisper models on the CPU run several
        # times slower, and the Lunar Lake NPU driver hang (openvino#38403) tends to recur.
        others = ["GPU", "CPU"]
        order += [d for d in others if d != lost]
    # Each recovery moves one step down the list (the same device is retried only once).
    tried = {d: previous.count(d) for d in order}
    stamp = f"{at_ms // 60000:02d}:{at_ms // 1000 % 60:02d}"
    time.sleep(RECOVERY_DELAY_S)
    gc.collect()
    load_kwargs = {"factory": pipeline_factory} if pipeline_factory else {}
    for device in order:
        if device not in available or tried.get(device, 0) >= 1:
            continue
        tried[device] = tried.get(device, 0) + 1
        try:
            model = whisper.load_model(
                failed.model_path,
                request.model_id,
                device,
                [device],
                available,
                cache_dir=settings.model_dir / ".ov-cache",
                **load_kwargs,
            )
        except LocalSubError as exc:
            record.asr_recoveries.append(
                {"at_ms": at_ms, "from": lost, "to": device, "ok": False, "error": exc.code}
            )
            continue
        reason = f"{lost} device lost at {stamp}; continued on {device}"
        record.asr_recoveries.append({"at_ms": at_ms, "from": lost, "to": device, "ok": True})
        record.device = DeviceResolution(
            requested=settings.device_preference,
            resolved=device,
            available_devices=available,
            fallback_reason=reason,
        )
        model = dataclasses.replace(model, device=record.device)
        send({"type": "warning", "message": reason})
        send(
            {
                "type": "device",
                "stage": "transcribing",
                "device": device,
                "fallback_reason": reason,
            }
        )
        return model
    return None


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
        self.diverged_at: int | None = None
        self.source: str | None = None
        self.language_known = threading.Event()
        self.translator: translation.LoadedTranslator | None = None
        self.result: translation.TranslationResult | None = None
        self.error: BaseException | None = None
        # Device died mid-run: ``result`` holds the lines done before it, ``translator`` is
        # abandoned and the main thread continues on the CPU.
        self.lost: translation.TranslatorLost | None = None
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
        except translation.TranslatorLost as exc:
            if self.translator is not None:
                translation.abandon(self.translator)
            self.translator = None
            self.lost = exc
            self.result = exc.partial
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
        should not), stop feeding past the last whole batch before it; ``finish`` reuses
        that prefix and translates the rest the normal way."""
        if self.diverged:
            return
        if stable[: len(self.fed)] != self.fed:
            self.diverged = True
            self.diverged_at = _first_difference(self.fed, stable)
            self.feed.cut(_whole_batches(self.diverged_at))
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
        done = min(len(self.result.lines), len(self.fed))
        changed = _first_difference(self.fed[:done], texts)
        if changed < done:
            # A batch is translated with its lines and the CONTEXT_LINES before it, so only
            # batches that end before the first changed line match a fresh translation.
            if self.diverged_at is None:
                self.diverged_at = changed
            done = _whole_batches(changed)
        return done


def _first_difference(old: Sequence[str], new: Sequence[str]) -> int:
    """Index of the first line of ``old`` that ``new`` does not repeat (len(old) if none)."""
    for i, line in enumerate(old):
        if i >= len(new) or new[i] != line:
            return i
    return len(old)


def _whole_batches(lines: int) -> int:
    batch = translation.DEFAULT_BATCH_SIZE
    return (lines // batch) * batch


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
    kernel32: Any | None = None,
) -> JobRecord:
    """Run one job with the PC held awake (Windows) until it finishes, fails or is cancelled."""
    with power.keep_awake(request.keep_awake, request.keep_display_on, kernel32) as state:
        return _run_job(
            settings,
            request,
            emit,
            runtime_probe,
            pipeline_factory,
            ffmpeg_runner,
            llm_factory,
            state,
        )


def _run_job(
    settings: Settings,
    request: JobRequest,
    emit: EventSink | None = None,
    runtime_probe: Callable[[], tuple[str | None, tuple[str, ...]]] = _openvino_version,
    pipeline_factory: whisper.PipelineFactory | None = None,
    ffmpeg_runner: audio.Runner | None = None,
    llm_factory: translation.LLMFactory | None = None,
    power_state: power.PowerState | None = None,
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
    if power_state is not None:
        record.power = power_state.to_dict()
        send({"type": "power", **record.power})
    try:
        transition(JobState.PROBING)
        unknown = [f for f in request.formats if f not in FORMATS]
        if unknown or not request.formats:
            raise LocalSubError(ErrorCode.INPUT_UNSUPPORTED, f"Unknown export formats: {unknown}")
        if request.translate_to not in (None, *translation.TARGET_LANGUAGES):
            raise LocalSubError(ErrorCode.INPUT_UNSUPPORTED, "translate_to must be vi or en")
        whisper.check_language_support(request.model_id, request.language, request.task)
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
        kwargs = {"runner": ffmpeg_runner} if ffmpeg_runner else {}

        def extract() -> Any:
            t = time.perf_counter()
            audio.extract_audio(ffmpeg, source, temp_audio, **kwargs)
            duration = audio.wav_duration_ms(temp_audio)
            pcm = audio.load_wav_samples(temp_audio)
            timed("extract", t)
            return duration, pcm

        # FFmpeg (CPU, a subprocess) and compiling Whisper from the cache (disk, NPU/GPU) do
        # not compete, so they run at the same time; the pool waits for FFmpeg even if the
        # load fails, so the temp WAV is never deleted under it.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="extract") as pool:
            extracting = pool.submit(extract)
            transition(JobState.LOADING_MODEL, model_id=request.model_id)
            load_kwargs = {"factory": pipeline_factory} if pipeline_factory else {}
            try:
                model = whisper.load_model(
                    model_path,
                    request.model_id,
                    settings.device_preference,
                    settings.preferred_devices(),
                    available,
                    cache_dir=settings.model_dir / ".ov-cache",
                    **load_kwargs,
                )
            except BaseException:
                extracting.cancel()
                raise
            record.source_duration_ms, samples = extracting.result()
        record.device = model.device
        record.device_attempts = model.attempts
        record.timings_ms["load_model"] = model.load_ms

        def speech_language(detected: str | None) -> str | None:
            if request.task == "translate":
                return "en"
            return request.language if request.language != "auto" else detected

        overlap: OverlapTranslation | None = None
        target = request.translate_to
        if target and speech_language(None) == target:
            # Known up front (e.g. Distil-Whisper EN -> en): loading the LLM early is pure waste.
            record.overlap = False
            record.overlap_reason = f"speech already in {target}, nothing to translate"
        elif target and llm_path is not None:
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

        def on_chunk(
            segments: tuple[TimedText, ...], detected: str | None, horizon_ms: int | None = None
        ) -> None:
            if overlap is None:
                return
            language = speech_language(detected)
            if language is not None or segments:
                overlap.set_language(language)
            overlap.offer(stable_lines(segments, record.source_duration_ms, horizon_ms))

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

        def recover(failed: whisper.LoadedModel, at_ms: int) -> whisper.LoadedModel | None:
            return _recover_asr(
                settings,
                request,
                record,
                failed,
                at_ms,
                available,
                overlap is not None,
                send,
                pipeline_factory,
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
                recover=recover,
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
        # Drop every reference to the Whisper pipeline(s) before the translator needs memory.
        record.device = result.model.device if result.model else record.device
        result = whisper.TranscriptionResult(
            result.segments, result.detected_language, result.inference_ms
        )
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
                head = overlap.result if done else None
                record.translation_overlap_lines = done
                record.translation_overlap_diverged_at = overlap.diverged_at
            if done < len(texts):
                if translator is None and overlap is not None and overlap.lost is not None:
                    translator = _translator_after_lost(
                        settings,
                        request,
                        record,
                        llm_path,
                        available,
                        llm_factory,
                        overlap.lost,
                        done,
                    )
                if translator is None:
                    translator = _load_translator(
                        settings, request, record, llm_path, available, llm_factory
                    )
                try:
                    tail = _translate(translator, texts[done:], source_lang, target, send)
                except translation.TranslatorLost as lost:
                    translation.abandon(translator)
                    translator = None
                    at = done + len(lost.partial.lines)
                    translator = _translator_after_lost(
                        settings, request, record, llm_path, available, llm_factory, lost, at
                    )
                    rest = _translate(translator, texts[at:], source_lang, target, send)
                    tail = _join(lost.partial, rest)
            else:
                tail = translation.TranslationResult((), 0, 0)
            translations = [*(head.lines[:done] if head else ()), *tail.lines]
            record.translation_fallback_lines = (head.fallback_lines if head else 0) + (
                tail.fallback_lines
            )
            reasons: dict[str, int] = {}
            for key, n in (*(head.fallback_reasons if head else ()), *tail.fallback_reasons):
                reasons[key] = reasons.get(key, 0) + n
            record.translation_fallback_reasons = reasons
            record.translation_interjection_lines = (
                head.interjection_lines if head else 0
            ) + tail.interjection_lines
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
