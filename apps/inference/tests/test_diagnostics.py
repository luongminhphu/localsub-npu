from app.config import Settings
from app.diagnostics import resolve_device


def make_settings(device_preference: str) -> Settings:
    return Settings(
        device_preference=device_preference,
        ffmpeg_path="ffmpeg",
        output_dir=None,  # type: ignore[arg-type]
        model_dir=None,  # type: ignore[arg-type]
        log_level="INFO",
    )


def test_auto_prefers_npu() -> None:
    result = resolve_device(make_settings("auto"), ("CPU", "GPU", "NPU"))
    assert result.resolved == "NPU"
    assert result.fallback_reason is None


def test_auto_falls_back_to_cpu() -> None:
    result = resolve_device(make_settings("auto"), ("CPU",))
    assert result.resolved == "CPU"
    assert result.fallback_reason is not None


def test_explicit_npu_does_not_silently_fallback() -> None:
    result = resolve_device(make_settings("NPU"), ("CPU",))
    assert result.resolved is None
    assert result.fallback_reason == "No requested OpenVINO device is available."
