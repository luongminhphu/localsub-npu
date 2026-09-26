from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.config import Settings


def write_wav(path: Path, seconds: float = 2.0, rate: int = 16_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        device_preference="auto",
        ffmpeg_path="python3",  # any executable on PATH; real FFmpeg is replaced by a fake runner
        output_dir=tmp_path / "out",
        model_dir=tmp_path / "models",
        log_level="INFO",
    )


@pytest.fixture
def installed_model(settings: Settings) -> Path:
    path = settings.model_dir / "whisper-small-int8"
    path.mkdir(parents=True)
    names = ("openvino_encoder_model.xml", "openvino_decoder_model.xml", "openvino_tokenizer.xml")
    for name in names:
        (path / name).write_text("<xml/>")
        (path / name).with_suffix(".bin").write_bytes(b"\0")
    return path
