from __future__ import annotations

import shutil
import subprocess
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.errors import ErrorCode, LocalSubError

SAMPLE_RATE = 16_000

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts",
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    }
)

Runner = Callable[..., subprocess.CompletedProcess[Any]]


def validate_source(source: Path) -> Path:
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise LocalSubError(
            ErrorCode.INPUT_UNSUPPORTED, f"Unsupported media extension: {source.suffix or '(none)'}"
        )
    if not source.is_file():
        raise LocalSubError(ErrorCode.INPUT_NOT_FOUND, "Source media file does not exist.")
    return source.resolve()


def resolve_ffmpeg(ffmpeg_path: str) -> str:
    resolved = shutil.which(ffmpeg_path)
    if resolved is None:
        raise LocalSubError(
            ErrorCode.FFMPEG_UNAVAILABLE,
            f"FFmpeg not found ('{ffmpeg_path}'). Add it to PATH or set FFMPEG_PATH.",
        )
    return resolved


def build_ffmpeg_command(ffmpeg: str, source: Path, destination: Path) -> list[str]:
    """Argument array (never a shell string) that extracts 16 kHz mono PCM WAV."""
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        # Only the first audio track: default selection decodes the most-channel track (5.1).
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(destination),
    ]


def extract_audio(
    ffmpeg: str,
    source: Path,
    destination: Path,
    runner: Runner = subprocess.run,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(ffmpeg, source, destination)
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise LocalSubError(ErrorCode.FFMPEG_UNAVAILABLE, f"Unable to start FFmpeg: {exc}") from exc

    if completed.returncode != 0 or not destination.is_file():
        stderr = (completed.stderr or "").strip().splitlines()
        detail = stderr[-1][:300] if stderr else f"exit code {completed.returncode}"
        raise LocalSubError(ErrorCode.AUDIO_EXTRACTION_FAILED, f"FFmpeg failed: {detail}")
    return destination


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return int(handle.getnframes() * 1000 / handle.getframerate())


def load_wav_samples(path: Path) -> Sequence[float]:
    """Load 16 kHz mono PCM16 WAV as float32 samples in [-1, 1]."""
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != SAMPLE_RATE:
            raise LocalSubError(
                ErrorCode.AUDIO_EXTRACTION_FAILED, "Extracted audio must be 16 kHz mono WAV."
            )
        if handle.getsampwidth() != 2:
            raise LocalSubError(ErrorCode.AUDIO_EXTRACTION_FAILED, "Extracted audio must be PCM16.")
        frames = handle.readframes(handle.getnframes())

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with OpenVINO
        import array

        pcm = array.array("h")
        pcm.frombytes(frames)
        return [sample / 32768.0 for sample in pcm]

    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
