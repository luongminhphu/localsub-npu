"""Speech recognition survives an NPU/GPU driver reset (ZE_RESULT_ERROR_DEVICE_LOST)."""

import pytest

from app import jobs, whisper
from app.errors import ErrorCode
from app.jobs import JobRequest, run_job
from app.models import JobState
from tests.test_jobs import install_llm, make_source, probe_npu
from tests.test_overlap import CHUNKS, LINES_PER_CHUNK, Chunk, TimedFakeLLM, chunked, fake_ffmpeg

__all__ = ["chunked"]  # pytest fixture re-used from test_overlap

LOST = (
    "Exception from src\\inference\\src\\cpp\\infer_request.cpp:246: Exception from "
    "src\\plugins\\intel_npu\\src\\utils\\src\\zero\\zero_wrappers.cpp:382: "
    "L0 zeFenceHostSynchronize result: ZE_RESULT_ERROR_DEVICE_LOST"
)


class Whisper:
    """Shared call counter across reloads; the listed calls fail with ``error``."""

    def __init__(self, fail_calls: set[int], error: str = LOST) -> None:
        self.fail_calls = fail_calls
        self.error = error
        self.calls = 0
        self.loads: list[str] = []

    def factory(self, path, device, props):
        self.loads.append(device)
        return self

    def generate(self, samples, **kwargs):
        call = self.calls
        self.calls += 1
        if call in self.fail_calls:
            raise RuntimeError(self.error)

        class R:
            chunks = [Chunk(i * 1.0, i * 1.0 + 0.8, f" call{call} line{i}") for i in range(3)]
            texts = None

        return R()


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(jobs, "RECOVERY_DELAY_S", 0)


def run(settings, tmp_path, asr: Whisper, device="auto", translate=False, available=None):
    settings = settings.__class__(**{**settings.__dict__, "device_preference": device})
    if translate:
        install_llm(settings)
    events: list[dict] = []
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="ja",
            translate_to="vi" if translate else None,
            translation_model="qwen3-0.6b-int4",
            translation_device="GPU",
        ),
        emit=events.append,
        runtime_probe=available or probe_npu,
        pipeline_factory=asr.factory,
        llm_factory=lambda p, d, props: TimedFakeLLM(),
        ffmpeg_runner=fake_ffmpeg,
    )
    return record, events


def test_device_lost_detection() -> None:
    assert whisper.is_device_lost(RuntimeError(LOST))
    assert whisper.is_device_lost(RuntimeError("NPU device lost"))
    assert not whisper.is_device_lost(RuntimeError("out of memory"))


def test_npu_reset_reloads_same_device_and_keeps_finished_chunks(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr = Whisper(fail_calls={2})
    record, events = run(settings, tmp_path, asr)
    assert record.state == JobState.COMPLETED, record.error_message
    assert asr.loads == ["NPU", "NPU"]  # initial load, then the same device again
    assert asr.calls == CHUNKS + 1  # only the failed chunk was redone
    assert record.cue_count and record.cue_count >= CHUNKS * 3 - 1
    assert record.asr_recoveries[0]["from"] == "NPU" and record.asr_recoveries[0]["ok"]
    assert record.device.resolved == "NPU" and "device lost" in record.device.fallback_reason
    assert any(e.get("type") == "device" and e["device"] == "NPU" for e in events)
    assert any(e.get("type") == "warning" and "device lost" in e["message"] for e in events)


def test_repeated_reset_moves_to_gpu_then_cpu_on_auto(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr = Whisper(fail_calls={1, 2, 3})
    record, _ = run(settings, tmp_path, asr)
    assert record.state == JobState.COMPLETED, record.error_message
    assert asr.loads == ["NPU", "NPU", "GPU", "CPU"]
    assert [r["to"] for r in record.asr_recoveries] == ["NPU", "GPU", "CPU"]
    assert record.device.resolved == "CPU"


def test_overlap_prefers_cpu_while_translator_holds_gpu(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr = Whisper(fail_calls={1, 2})
    record, _ = run(settings, tmp_path, asr, translate=True)
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.overlap is True
    assert asr.loads == ["NPU", "NPU", "CPU"]
    assert record.translation_fallback_lines == 0


def test_explicit_npu_retries_once_then_fails_with_device_lost(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr = Whisper(fail_calls={1, 2, 3, 4, 5})
    record, _ = run(settings, tmp_path, asr, device="NPU")
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.DEVICE_LOST
    assert asr.loads == ["NPU", "NPU"]  # never moves off an explicit device


def test_other_inference_errors_do_not_trigger_recovery(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr = Whisper(fail_calls={1}, error="Whisper decoder crashed")
    record, _ = run(settings, tmp_path, asr)
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.TRANSCRIPTION_FAILED
    assert asr.loads == ["NPU"] and record.asr_recoveries == []


def test_recovery_reload_failure_is_recorded(tmp_path, settings, installed_model, chunked):
    asr = Whisper(fail_calls={1})
    original = asr.factory

    def flaky(path, device, props):
        if len(asr.loads) == 1 and device == "NPU":
            asr.loads.append(device)
            raise RuntimeError("NPU compile failed after reset")
        return original(path, device, props)

    asr.factory = flaky
    record, _ = run(settings, tmp_path, asr)
    assert record.state == JobState.COMPLETED, record.error_message
    assert [(r["to"], r["ok"]) for r in record.asr_recoveries] == [("NPU", False), ("GPU", True)]
    assert LINES_PER_CHUNK  # imported constant keeps the shared fixture module loaded
