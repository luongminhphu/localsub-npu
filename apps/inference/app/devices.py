from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution

Factory = Callable[[Path, str, dict[str, Any]], Any]
PropertiesFor = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class CompiledPipeline:
    pipeline: Any
    device: DeviceResolution
    load_ms: int
    attempts: tuple[str, ...]


def compile_with_fallback(
    model_path: Path,
    requested: str,
    candidates: Sequence[str],
    available_devices: tuple[str, ...],
    factory: Factory,
    cache_dir: Path | None = None,
    properties_for: PropertiesFor | None = None,
) -> CompiledPipeline:
    """Compile an OpenVINO GenAI pipeline on the first working device.

    Explicit device requests never fall back. ``auto`` tries candidates in order (NPU -> GPU ->
    CPU) and records why each earlier candidate was skipped.
    """
    available = {d.upper().split(".")[0] for d in available_devices}
    explicit = requested != "auto"
    reasons: list[str] = []
    attempts: list[str] = []

    for device in candidates:
        if device not in available:
            if explicit:
                raise LocalSubError(
                    ErrorCode.DEVICE_UNAVAILABLE,
                    f"{device} requested but OpenVINO reports: "
                    f"{', '.join(available_devices) or 'none'}",
                )
            reasons.append(f"{device} not available")
            continue

        properties: dict[str, Any] = dict(properties_for(device)) if properties_for else {}
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            properties["CACHE_DIR"] = str(cache_dir)

        attempts.append(device)
        started = time.perf_counter()
        try:
            pipeline = factory(model_path, device, properties)
        except LocalSubError:
            raise
        except Exception as exc:  # OpenVINO raises RuntimeError subclasses
            message = str(exc).strip()
            detail = message.splitlines()[0][:200] if message else type(exc).__name__
            if explicit:
                raise LocalSubError(
                    ErrorCode.MODEL_COMPILE_FAILED, f"Compile on {device} failed: {detail}"
                ) from exc
            reasons.append(f"{device} compile failed: {detail}")
            continue

        return CompiledPipeline(
            pipeline=pipeline,
            device=DeviceResolution(
                requested=requested,
                resolved=device,
                available_devices=available_devices,
                fallback_reason="; ".join(reasons) or None,
            ),
            load_ms=int((time.perf_counter() - started) * 1000),
            attempts=tuple(attempts),
        )

    raise LocalSubError(
        ErrorCode.DEVICE_UNAVAILABLE,
        "No device could run the model: " + ("; ".join(reasons) or "none"),
    )


def ensure_ir_present(path: Path, required: Sequence[str], model_id: str, command: str) -> Path:
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise LocalSubError(
            ErrorCode.MODEL_NOT_FOUND,
            f"Model '{model_id}' not found at {path}. Run: python -m app.main {command} {model_id}",
        )
    return path


def snapshot(repo_id: str, target: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise LocalSubError(
            ErrorCode.OPENVINO_UNAVAILABLE, 'Install extras: pip install -e ".[openvino]"'
        ) from exc
    snapshot_download(repo_id=repo_id, local_dir=str(target))
    return target
