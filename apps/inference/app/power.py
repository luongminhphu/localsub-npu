"""Keep Windows awake and at full speed while a job runs.

* ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` stops the idle timer from
  putting the PC to sleep (and, with ``display=True``, the screen from turning off) until
  the job ends. It does not override closing the lid or choosing Sleep from the Start menu.
* Opting the worker out of power throttling (EcoQoS) stops Windows 11 from parking it on
  efficiency cores when the app window is in the background or minimised.

Both calls are no-ops elsewhere, never raise, and report what was applied so the job record
and the UI can show it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
THROTTLING_VERSION = 1
THROTTLE_EXECUTION_SPEED = 0x1
THROTTLE_IGNORE_TIMER_RESOLUTION = 0x4


@dataclass(frozen=True)
class PowerState:
    keep_awake: bool = False
    display_on: bool = False
    throttling_disabled: bool = False
    reason: str | None = None  # why something could not be applied

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_awake": self.keep_awake,
            "display_on": self.display_on,
            "throttling_disabled": self.throttling_disabled,
            "reason": self.reason,
        }


def _kernel32() -> Any | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.GetCurrentProcess.argtypes = []
        k32.SetProcessInformation.restype = ctypes.c_int
        k32.SetProcessInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        k32.SetThreadExecutionState.restype = ctypes.c_uint
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        return k32
    except (ImportError, AttributeError, OSError):
        return None


def disable_power_throttling(kernel32: Any | None = None) -> bool:
    """Ask Windows to run this process at full speed (no EcoQoS). True when applied."""
    kernel32 = kernel32 if kernel32 is not None else _kernel32()
    if kernel32 is None:
        return False
    try:
        import ctypes

        class _State(ctypes.Structure):
            _fields_ = [
                ("Version", ctypes.c_ulong),
                ("ControlMask", ctypes.c_ulong),
                ("StateMask", ctypes.c_ulong),
            ]

        mask = THROTTLE_EXECUTION_SPEED | THROTTLE_IGNORE_TIMER_RESOLUTION
        state = _State(THROTTLING_VERSION, mask, 0)  # control these flags, all turned off
        # Typed arguments matter on x64: without them ctypes passes the pseudo handle (-1)
        # from GetCurrentProcess as a 32-bit int, Windows sees 0xFFFFFFFF, an invalid
        # handle, and the call fails (0.9.0 logged throttling_disabled=false on Windows).
        handle = kernel32.GetCurrentProcess()
        if not isinstance(handle, ctypes.c_void_p):
            handle = ctypes.c_void_p(handle if handle is not None else -1)
        ok = kernel32.SetProcessInformation(
            handle,
            ctypes.c_int(PROCESS_POWER_THROTTLING),
            ctypes.byref(state),
            ctypes.c_ulong(ctypes.sizeof(state)),
        )
        return bool(ok)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


FULL_SPEED_ENV = "LOCALSUB_FULL_SPEED"


@contextmanager
def keep_awake(
    enabled: bool = True,
    display: bool = False,
    kernel32: Any | None = None,
    full_speed: bool | None = None,
) -> Iterator[PowerState]:
    """Hold the PC awake for the ``with`` block (call on the job's main thread).

    ``full_speed`` (EcoQoS opt-out) is off by default since 0.9.2: on Lunar Lake the CPU,
    GPU and NPU share one power budget, so forcing the worker's CPU threads to high QoS
    can take power from the GPU translator and the NPU. v0.8.0 never changed it.
    Opt in with ``LOCALSUB_FULL_SPEED=1``."""
    if full_speed is None:
        full_speed = env_flag(FULL_SPEED_ENV, False)
    if not enabled:
        yield PowerState(reason="disabled")
        return
    kernel32 = kernel32 if kernel32 is not None else _kernel32()
    if kernel32 is None:
        yield PowerState(reason="not supported on this OS")
        return
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if display else 0)
    try:
        previous = kernel32.SetThreadExecutionState(flags)
    except (AttributeError, OSError, TypeError, ValueError):
        previous = 0
    throttling = disable_power_throttling(kernel32) if full_speed else False
    state = PowerState(
        keep_awake=bool(previous),
        display_on=bool(previous) and display,
        throttling_disabled=throttling,
        reason=None if previous else "SetThreadExecutionState failed",
    )
    try:
        yield state
    finally:
        if previous:
            try:
                kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # back to normal idle rules
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default
