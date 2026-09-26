from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_DEVICE_PREFERENCES = {"auto", "NPU", "GPU", "CPU"}


def load_env_file(start: Path | None = None, max_depth: int = 4) -> Path | None:
    """Load KEY=VALUE pairs from the nearest .env without overriding the real environment."""
    directory = (start or Path.cwd()).resolve()
    for _ in range(max_depth):
        candidate = directory / ".env"
        if candidate.is_file():
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return candidate
        if directory.parent == directory:
            break
        directory = directory.parent
    return None


@dataclass(frozen=True)
class Settings:
    device_preference: str
    ffmpeg_path: str
    output_dir: Path
    model_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
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
