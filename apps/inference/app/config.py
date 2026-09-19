from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_DEVICE_PREFERENCES = {"auto", "NPU", "GPU", "CPU"}


@dataclass(frozen=True)
class Settings:
    device_preference: str
    ffmpeg_path: str
    output_dir: Path
    model_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        device = os.getenv("OPENVINO_DEVICE", "auto").strip()
        if device.lower() == "auto":
            device = "auto"
        else:
            device = device.upper()
        if device not in VALID_DEVICE_PREFERENCES:
            valid = ", ".join(sorted(VALID_DEVICE_PREFERENCES))
            raise ValueError(f"OPENVINO_DEVICE must be one of: {valid}")

        return cls(
            device_preference=device,
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg",
            output_dir=Path(os.getenv("LOCALSUB_OUTPUT_DIR", "./data/output")),
            model_dir=Path(os.getenv("LOCALSUB_MODEL_DIR", "./data/models")),
            log_level=os.getenv("LOCALSUB_LOG_LEVEL", "INFO").upper(),
        )

    def preferred_devices(self) -> list[str]:
        if self.device_preference == "auto":
            return ["NPU", "GPU", "CPU"]
        return [self.device_preference]
