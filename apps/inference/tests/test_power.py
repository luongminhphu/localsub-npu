"""Keep-awake and power-throttling opt-out (Windows API calls through a fake kernel32)."""

import pytest

from app import power
from app.jobs import JobRequest, run_job
from app.models import JobState
from tests.test_jobs import fake_ffmpeg, make_source, probe_npu
from tests.test_whisper import FakePipeline


class FakeKernel32:
    def __init__(self, execution_state_ok: bool = True, throttling_ok: bool = True) -> None:
        self.states: list[int] = []
        self.throttling_calls: list[tuple[int, int]] = []
        self.execution_state_ok = execution_state_ok
        self.throttling_ok = throttling_ok
        self.awake_during_job: bool | None = None

    def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 - Win32 name
        self.states.append(flags)
        return 0x80000000 if self.execution_state_ok else 0

    def GetCurrentProcess(self) -> int:  # noqa: N802
        return -1

    def SetProcessInformation(self, handle, info_class, state_ref, size) -> int:  # noqa: N802
        state = state_ref._obj
        self.throttling_calls.append((info_class, state.ControlMask, state.StateMask))
        return 1 if self.throttling_ok else 0

    @property
    def awake(self) -> bool:
        return bool(self.states) and self.states[-1] & power.ES_SYSTEM_REQUIRED != 0


def test_keep_awake_sets_and_restores_execution_state() -> None:
    k = FakeKernel32()
    with power.keep_awake(True, display=False, kernel32=k) as state:
        assert k.awake
        assert state.keep_awake and state.throttling_disabled and not state.display_on
    assert k.states == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]
    info_class, control, state_mask = k.throttling_calls[0]
    assert info_class == power.PROCESS_POWER_THROTTLING
    assert control & power.THROTTLE_EXECUTION_SPEED and state_mask == 0  # throttling off


def test_display_flag_and_restore_on_error() -> None:
    k = FakeKernel32()
    with pytest.raises(RuntimeError), power.keep_awake(True, display=True, kernel32=k) as state:
        assert state.display_on
        raise RuntimeError("job crashed")
    assert k.states[0] & power.ES_DISPLAY_REQUIRED
    assert k.states[-1] == power.ES_CONTINUOUS  # restored even when the job raises


def test_disabled_or_unsupported_is_a_no_op(monkeypatch) -> None:
    k = FakeKernel32()
    with power.keep_awake(False, kernel32=k) as state:
        assert state.reason == "disabled" and not state.keep_awake
    assert k.states == []
    monkeypatch.setattr(power, "_kernel32", lambda: None)
    with power.keep_awake(True) as state:
        assert state.reason == "not supported on this OS"


def test_failed_api_reports_reason() -> None:
    k = FakeKernel32(execution_state_ok=False, throttling_ok=False)
    with power.keep_awake(True, kernel32=k) as state:
        assert not state.keep_awake and not state.throttling_disabled
        assert "failed" in (state.reason or "")
    assert k.states == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]  # nothing to restore


def test_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("X_FLAG", "off")
    assert power.env_flag("X_FLAG", True) is False
    monkeypatch.setenv("X_FLAG", "1")
    assert power.env_flag("X_FLAG", False) is True
    monkeypatch.setenv("X_FLAG", "maybe")
    assert power.env_flag("X_FLAG", True) is True


class AwakeCheckingPipeline(FakePipeline):
    def __init__(self, kernel32: FakeKernel32) -> None:
        super().__init__()
        self.kernel32 = kernel32

    def generate(self, samples, **kwargs):
        self.kernel32.awake_during_job = self.kernel32.awake
        return super().generate(samples, **kwargs)


@pytest.mark.parametrize("keep", [True, False])
def test_job_is_held_awake_only_while_running(tmp_path, settings, installed_model, keep) -> None:
    k = FakeKernel32()
    events: list[dict] = []
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="vi", keep_awake=keep),
        emit=events.append,
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: AwakeCheckingPipeline(k),
        ffmpeg_runner=fake_ffmpeg,
        kernel32=k,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert k.awake_during_job is keep
    assert not k.awake  # released after the job
    assert record.power is not None and record.power["keep_awake"] is keep
    assert [e for e in events if e.get("type") == "power"][0]["keep_awake"] is keep


def test_failed_job_still_releases_keep_awake(tmp_path, settings, installed_model) -> None:
    k = FakeKernel32()
    record = run_job(
        settings,
        JobRequest(source=tmp_path / "missing.mp4"),
        runtime_probe=probe_npu,
        ffmpeg_runner=fake_ffmpeg,
        kernel32=k,
    )
    assert record.state == JobState.FAILED
    assert k.states[-1] == power.ES_CONTINUOUS
