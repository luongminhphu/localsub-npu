from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    PROBING = "probing"
    EXTRACTING_AUDIO = "extracting_audio"
    LOADING_MODEL = "loading_model"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class DeviceResolution:
    requested: str
    resolved: str | None
    available_devices: tuple[str, ...]
    fallback_reason: str | None = None
