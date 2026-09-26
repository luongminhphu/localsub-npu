from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.devices import compile_with_fallback, ensure_ir_present, snapshot
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
LANGUAGES = {"auto", "en", "vi"}
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
        ("openvino_encoder_model.xml", "openvino_decoder_model.xml", "openvino_tokenizer.xml"),
        model_id,
        "models download",
    )


def download_model(model_dir: Path, model_id: str) -> Path:
    target = model_path_for(model_dir, model_id)
    snapshot(MODEL_CATALOG[model_id], target)
    return ensure_model_present(target, model_id)


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


def transcribe(
    model: LoadedModel,
    samples: Sequence[float],
    language: str = "auto",
    task: str = "transcribe",
) -> TranscriptionResult:
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    if task not in TASKS:
        raise ValueError(f"task must be one of {sorted(TASKS)}")

    kwargs: dict[str, Any] = {"task": task, "return_timestamps": True}
    if language != "auto":
        kwargs["language"] = f"<|{language}|>"

    started = time.perf_counter()
    try:
        result = model.pipeline.generate(samples, **kwargs)
    except Exception as exc:
        raise LocalSubError(
            ErrorCode.TRANSCRIPTION_FAILED, f"Whisper inference failed: {str(exc)[:200]}"
        ) from exc
    inference_ms = int((time.perf_counter() - started) * 1000)

    segments: list[TimedText] = []
    for chunk in getattr(result, "chunks", None) or []:
        start = float(chunk.start_ts)
        end = float(chunk.end_ts) if chunk.end_ts is not None else -1.0
        segments.append(
            TimedText(
                start_ms=int(round(start * 1000)),
                end_ms=int(round(end * 1000)) if end >= 0 else None,
                text=chunk.text,
            )
        )
    if not segments and getattr(result, "texts", None):
        segments.append(TimedText(0, None, " ".join(result.texts)))

    detected = getattr(result, "language", None)
    if isinstance(detected, str):
        detected = detected.strip("<|>") or None
    return TranscriptionResult(tuple(segments), detected, inference_ms)
