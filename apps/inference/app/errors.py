from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INPUT_UNSUPPORTED = "INPUT_UNSUPPORTED"
    INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
    FFMPEG_UNAVAILABLE = "FFMPEG_UNAVAILABLE"
    AUDIO_EXTRACTION_FAILED = "AUDIO_EXTRACTION_FAILED"
    OPENVINO_UNAVAILABLE = "OPENVINO_UNAVAILABLE"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_INCOMPLETE = "MODEL_INCOMPLETE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    INSUFFICIENT_DISK = "INSUFFICIENT_DISK"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    DEVICE_LOST = "DEVICE_LOST"
    MODEL_COMPILE_FAILED = "MODEL_COMPILE_FAILED"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    TRANSLATION_FAILED = "TRANSLATION_FAILED"
    SUBTITLE_VALIDATION_FAILED = "SUBTITLE_VALIDATION_FAILED"
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


class LocalSubError(Exception):
    """Error with a stable, sanitized code that is safe to log and show in the UI."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
