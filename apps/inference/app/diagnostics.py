from __future__ import annotations

import shutil
from dataclasses import dataclass

from app.config import Settings
from app.models import DeviceResolution


@dataclass(frozen=True)
class DiagnosticReport:
    ffmpeg_available: bool
    ffmpeg_path: str
    openvino_version: str | None
    available_devices: tuple[str, ...]
    device_resolution: DeviceResolution


def resolve_device(settings: Settings, available_devices: tuple[str, ...]) -> DeviceResolution:
    available = {device.upper() for device in available_devices}

    for candidate in settings.preferred_devices():
        if candidate.upper() in available:
            fallback_reason = None
            if candidate != settings.preferred_devices()[0]:
                fallback_reason = f"Preferred devices unavailable; selected {candidate}."
            return DeviceResolution(
                requested=settings.device_preference,
                resolved=candidate,
                available_devices=available_devices,
                fallback_reason=fallback_reason,
            )

    return DeviceResolution(
        requested=settings.device_preference,
        resolved=None,
        available_devices=available_devices,
        fallback_reason="No requested OpenVINO device is available.",
    )


def build_diagnostic_report(settings: Settings) -> DiagnosticReport:
    ffmpeg_available = shutil.which(settings.ffmpeg_path) is not None
    openvino_version: str | None = None
    available_devices: tuple[str, ...] = ()

    try:
        import openvino as ov

        core = ov.Core()
        openvino_version = getattr(ov, "__version__", "unknown")
        available_devices = tuple(core.available_devices)
    except ImportError:
        pass
    except Exception:
        # Diagnostics must remain usable when an installed driver/runtime is unhealthy.
        pass

    return DiagnosticReport(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=settings.ffmpeg_path,
        openvino_version=openvino_version,
        available_devices=available_devices,
        device_resolution=resolve_device(settings, available_devices),
    )
