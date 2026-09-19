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
    preferred = settings.preferred_devices()
    for candidate in preferred:
        if candidate.upper() in available:
            reason = None if candidate == preferred[0] else f"Preferred devices unavailable; selected {candidate}."
            return DeviceResolution(settings.device_preference, candidate, available_devices, reason)
    return DeviceResolution(
        settings.device_preference,
        None,
        available_devices,
        "No requested OpenVINO device is available.",
    )


def build_diagnostic_report(settings: Settings) -> DiagnosticReport:
    ffmpeg_available = shutil.which(settings.ffmpeg_path) is not None
    version: str | None = None
    devices: tuple[str, ...] = ()
    try:
        import openvino as ov
        core = ov.Core()
        version = getattr(ov, "__version__", "unknown")
        devices = tuple(core.available_devices)
    except ImportError:
        pass
    except Exception:
        pass
    return DiagnosticReport(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=settings.ffmpeg_path,
        openvino_version=version,
        available_devices=devices,
        device_resolution=resolve_device(settings, devices),
    )
