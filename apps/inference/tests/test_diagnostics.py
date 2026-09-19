from pathlib import Path

from app.config import Settings
from app.diagnostics import resolve_device


def make_settings(device_preference: str) -> Settings:
    return Settings(device_preference, "ffmpeg", Path("out"), Path("models"), "INFO")


def test_auto_prefers_npu() -> None:
    assert resolve_device(make_settings("auto"), ("CPU", "GPU", "NPU")).resolved == "NPU"


def test_auto_falls_back_to_cpu() -> None:
    result = resolve_device(make_settings("auto"), ("CPU",))
    assert result.resolved == "CPU"
    assert result.fallback_reason is not None


def test_explicit_npu_does_not_silently_fallback() -> None:
    result = resolve_device(make_settings("NPU"), ("CPU",))
    assert result.resolved is None
    assert result.fallback_reason == "No requested OpenVINO device is available."
