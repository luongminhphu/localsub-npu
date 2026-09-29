from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.devices import compile_with_fallback, ensure_ir_present
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution
from app.segmentation import TimedText

# Pre-converted OpenVINO IR models published by Intel on Hugging Face.
MODEL_CATALOG: dict[str, str] = {
    "whisper-tiny-int8": "OpenVINO/whisper-tiny-int8-ov",
    "whisper-base-int8": "OpenVINO/whisper-base-int8-ov",
    "whisper-small-int8": "OpenVINO/whisper-small-int8-ov",
    "whisper-medium-int8": "OpenVINO/whisper-medium-int8-ov",
    "whisper-large-v3-turbo-int8": "OpenVINO/whisper-large-v3-turbo-int8-ov",
    "whisper-large-v3-int8": "OpenVINO/whisper-large-v3-int8-ov",
}
DEFAULT_MODEL = "whisper-small-int8"
LANGUAGES = {"auto", "en", "ja", "vi"}
TASKS = {"transcribe", "translate"}

PipelineFactory = Callable[[Path, str, dict[str, Any]], Any]


@dataclass(frozen=True)
class LoadedModel:
    pipeline: Any
    model_id: str
    model_path: Path
    device: DeviceResolution
    load_ms: int
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class TranscriptionResult:
    segments: tuple[TimedText, ...]
    detected_language: str | None
    inference_ms: int


REQUIRED_FILES: tuple[str, ...] = (
    "openvino_encoder_model.xml",
    "openvino_decoder_model.xml",
    "openvino_tokenizer.xml",
)


def model_path_for(model_dir: Path, model_id: str) -> Path:
    if model_id not in MODEL_CATALOG:
        known = ", ".join(sorted(MODEL_CATALOG))
        raise LocalSubError(
            ErrorCode.MODEL_NOT_FOUND, f"Unknown model '{model_id}'. Known: {known}"
        )
    return model_dir / model_id


def ensure_model_present(path: Path, model_id: str) -> Path:
    return ensure_ir_present(
        path,
        REQUIRED_FILES,
        model_id,
        "models download",
    )


def download_model(model_dir: Path, model_id: str) -> Path:
    from app import model_store

    return model_store.download(model_dir, model_id).path


def _default_factory(path: Path, device: str, properties: dict[str, Any]) -> Any:
    try:
        import openvino_genai
    except ImportError as exc:
        raise LocalSubError(
            ErrorCode.OPENVINO_UNAVAILABLE, "openvino-genai is not installed in this environment."
        ) from exc
    return openvino_genai.WhisperPipeline(str(path), device, **properties)


def load_model(
    model_path: Path,
    model_id: str,
    requested: str,
    candidates: Sequence[str],
    available_devices: tuple[str, ...],
    cache_dir: Path | None = None,
    factory: PipelineFactory = _default_factory,
) -> LoadedModel:
    compiled = compile_with_fallback(
        model_path, requested, candidates, available_devices, factory, cache_dir
    )
    return LoadedModel(
        pipeline=compiled.pipeline,
        model_id=model_id,
        model_path=model_path,
        device=compiled.device,
        load_ms=compiled.load_ms,
        attempts=compiled.attempts,
    )


SAMPLE_RATE = 16_000
# Long media is transcribed in ~5 minute chunks cut at the quietest point near each target,
# so the UI gets progress, memory per call stays bounded and fully silent chunks are skipped.
CHUNK_SECONDS = 300
SEARCH_SECONDS = 10
FRAME_SECONDS = 0.1
SILENCE_PEAK = 0.003  # ~-50 dBFS: nothing Whisper could transcribe, only hallucinate on

ProgressSink = Callable[[int, int], None]
ChunkSink = Callable[[tuple[TimedText, ...], str | None], None]


def _frame_energy(samples: Sequence[float], start: int, stop: int) -> float:
    try:
        import numpy as np

        window = np.asarray(samples[start:stop], dtype=np.float32)
        return float(np.mean(window * window)) if window.size else 0.0
    except ImportError:  # pragma: no cover - numpy ships with OpenVINO
        window = samples[start:stop]
        return sum(x * x for x in window) / len(window) if window else 0.0


def split_points(
    samples: Sequence[float],
    sample_rate: int = SAMPLE_RATE,
    chunk_seconds: float = CHUNK_SECONDS,
    search_seconds: float = SEARCH_SECONDS,
) -> list[tuple[int, int]]:
    """Return [start, stop) sample ranges covering the audio, cut at quiet frames."""
    total = len(samples)
    chunk = int(chunk_seconds * sample_rate)
    if chunk <= 0 or total <= chunk + int(search_seconds * sample_rate):
        return [(0, total)] if total else []
    frame = max(1, int(FRAME_SECONDS * sample_rate))
    search = int(search_seconds * sample_rate)
    ranges: list[tuple[int, int]] = []
    begin = 0
    while total - begin > chunk + search:
        target = begin + chunk
        best, best_energy = target, float("inf")
        for pos in range(target - search, target + search, frame):
            energy = _frame_energy(samples, pos, pos + frame)
            if energy < best_energy:
                best, best_energy = pos, energy
        ranges.append((begin, best))
        begin = best
    ranges.append((begin, total))
    return ranges


def _is_silent(samples: Sequence[float]) -> bool:
    try:
        import numpy as np

        window = np.asarray(samples, dtype=np.float32)
        return bool(window.size) and float(np.max(np.abs(window))) < SILENCE_PEAK
    except ImportError:  # pragma: no cover
        return bool(samples) and max(abs(x) for x in samples) < SILENCE_PEAK


def _language_token(result: Any) -> str | None:
    detected = getattr(result, "language", None)
    if isinstance(detected, str):
        return detected.strip("<|>") or None
    return None


def transcribe(
    model: LoadedModel,
    samples: Sequence[float],
    language: str = "auto",
    task: str = "transcribe",
    on_progress: ProgressSink | None = None,
    chunk_seconds: float = CHUNK_SECONDS,
    on_chunk: ChunkSink | None = None,
) -> TranscriptionResult:
    """Transcribe chunk by chunk; ``on_progress(done_ms, total_ms)`` after each chunk.

    ``on_chunk(segments_so_far, detected_language)`` also runs after each chunk so a
    consumer (the overlapping translator) can start on finished lines."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    if task not in TASKS:
        raise ValueError(f"task must be one of {sorted(TASKS)}")
    started = time.perf_counter()
    total_ms = int(len(samples) * 1000 / SAMPLE_RATE)
    segments: list[TimedText] = []
    detected: str | None = None
    ranges = split_points(samples, SAMPLE_RATE, chunk_seconds)
    for begin, stop in ranges:
        piece = samples[begin:stop]
        offset_ms = int(round(begin * 1000 / SAMPLE_RATE))
        # Only long media skips silent chunks; a short clip always goes to the model.
        if len(ranges) == 1 or not _is_silent(piece):
            kwargs: dict[str, Any] = {"task": task, "return_timestamps": True}
            # Detect once on the first spoken chunk, then pin the language so later chunks
            # skip detection and cannot flip language mid-video.
            pinned = language if language != "auto" else detected
            if pinned:
                kwargs["language"] = f"<|{pinned}|>"
            try:
                result = model.pipeline.generate(piece, **kwargs)
            except Exception as exc:
                raise LocalSubError(
                    ErrorCode.TRANSCRIPTION_FAILED, f"Whisper inference failed: {str(exc)[:200]}"
                ) from exc
            if detected is None:
                detected = _language_token(result)
            chunks = getattr(result, "chunks", None) or []
            for chunk in chunks:
                start = float(chunk.start_ts)
                end = float(chunk.end_ts) if chunk.end_ts is not None else -1.0
                segments.append(
                    TimedText(
                        start_ms=offset_ms + int(round(start * 1000)),
                        end_ms=offset_ms + int(round(end * 1000)) if end >= 0 else None,
                        text=chunk.text,
                    )
                )
            if not chunks and getattr(result, "texts", None):
                segments.append(TimedText(offset_ms, None, " ".join(result.texts)))
        if on_chunk:
            on_chunk(tuple(segments), language if language != "auto" else detected)
        if on_progress:
            on_progress(min(total_ms, int(round(stop * 1000 / SAMPLE_RATE))), total_ms)
    inference_ms = int((time.perf_counter() - started) * 1000)
    return TranscriptionResult(tuple(segments), detected, inference_ms)
