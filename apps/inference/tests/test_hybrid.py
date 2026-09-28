"""GPU + NPU hybrid translation: shared queue, lane failures, planning and job wiring."""

import threading
import time

import pytest

from app import translation
from app.errors import ErrorCode, LocalSubError
from app.jobs import JobRequest, run_job
from app.models import DeviceResolution, JobState
from tests.fakes import FakeLLM
from tests.test_jobs import fake_ffmpeg, install_llm, make_source, probe_npu
from tests.test_whisper import FakePipeline


class SlowLLM(FakeLLM):
    def __init__(self, delay: float, fail_after: int | None = None) -> None:
        super().__init__()
        self.delay = delay
        self.fail_after = fail_after
        self.calls = 0
        self.threads: set[str] = set()

    def generate(self, prompt, config=None, **kwargs):
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("device lost")
        time.sleep(self.delay)
        return super().generate(prompt, config, **kwargs)


def lane(device: str, llm: FakeLLM | None = None, error: Exception | None = None):
    def load() -> translation.LoadedTranslator:
        if error:
            raise error
        return translation.LoadedTranslator(
            llm, "qwen3-0.6b-int4", DeviceResolution(device, device, (device,)), 5, (device,)
        )

    return translation.Lane(device, load)


LINES = [f"line {i}" for i in range(1, 81)]  # 10 batches of 8


def test_both_lanes_share_the_queue_and_keep_order() -> None:
    gpu, npu = SlowLLM(0.02), SlowLLM(0.02)
    progress: list[int] = []
    lanes_seen: list[tuple[str, str]] = []
    result, outcome = translation.translate_lines_hybrid(
        [lane("GPU", gpu), lane("NPU", npu)],
        LINES,
        "en",
        "vi",
        on_progress=lambda d, t: progress.append(d),
        on_lane=lambda d, s, r: lanes_seen.append((d, s)),
    )
    assert list(result.lines) == [f"[vi] {x}" for x in LINES]
    assert result.device_lines["GPU"] + result.device_lines["NPU"] == 80
    assert result.device_lines["NPU"] > 0, "NPU must take part"
    assert progress == sorted(progress) and progress[-1] == 80
    assert sorted(lanes_seen) == [("GPU", "ready"), ("NPU", "ready")]
    assert gpu.threads == {"translate-GPU"} and npu.threads == {"translate-NPU"}
    assert isinstance(outcome["GPU"], translation.LoadedTranslator)


def test_faster_lane_does_more_work() -> None:
    gpu, npu = SlowLLM(0.01), SlowLLM(0.08)
    result, _ = translation.translate_lines_hybrid(
        [lane("GPU", gpu), lane("NPU", npu)], LINES, "en", "vi"
    )
    assert result.device_lines["GPU"] > result.device_lines.get("NPU", 0)


def test_lane_that_fails_to_load_is_skipped() -> None:
    gpu = SlowLLM(0.0)
    events: list[tuple[str, str, str | None]] = []
    result, outcome = translation.translate_lines_hybrid(
        [lane("GPU", gpu), lane("NPU", error=RuntimeError("compile failed"))],
        LINES,
        "en",
        "vi",
        on_lane=lambda d, s, r: events.append((d, s, r)),
    )
    assert result.device_lines == {"GPU": 80}
    assert outcome["NPU"] == "NPU: compile failed"
    assert ("NPU", "failed", "NPU: compile failed") in events


def test_lane_that_fails_mid_run_returns_its_batches() -> None:
    gpu, npu = SlowLLM(0.03), SlowLLM(0.0, fail_after=1)
    result, outcome = translation.translate_lines_hybrid(
        [lane("GPU", gpu), lane("NPU", npu)], LINES, "en", "vi"
    )
    assert list(result.lines) == [f"[vi] {x}" for x in LINES]
    assert "device lost" in str(outcome["NPU"])


def test_all_lanes_failing_raises() -> None:
    with pytest.raises(LocalSubError) as err:
        translation.translate_lines_hybrid(
            [lane("GPU", error=RuntimeError("a")), lane("NPU", error=RuntimeError("b"))],
            LINES,
            "en",
            "vi",
        )
    assert err.value.code == ErrorCode.TRANSLATION_FAILED
    assert "GPU: a" in str(err.value) and "NPU: b" in str(err.value)


def test_hybrid_plan_checks_devices_and_memory() -> None:
    gb = 2**30
    assert translation.hybrid_plan(("CPU", "GPU", "NPU"), 5 * gb, 20 * gb) == (["GPU", "NPU"], None)
    devices, why = translation.hybrid_plan(("CPU", "GPU"), 5 * gb, 20 * gb)
    assert devices == ["GPU"] and why == "hybrid: NPU not available"
    devices, why = translation.hybrid_plan(("CPU", "GPU", "NPU"), 5 * gb, 8 * gb)
    assert devices == ["GPU"] and why and "RAM" in why
    assert translation.hybrid_plan(("CPU",), 1, None)[0] == ["CPU"]
    assert translation.hybrid_plan(("CPU", "GPU.0", "NPU"), 1, None)[0] == ["GPU", "NPU"]


def _run(tmp_path, settings, probe, monkeypatch=None, free=None):
    install_llm(settings)
    if monkeypatch is not None:
        monkeypatch.setattr(translation, "available_memory_bytes", lambda: free)
    devices_loaded: list[str] = []
    events: list[dict] = []

    def factory(path, device, props):
        devices_loaded.append(device)
        return FakeLLM()

    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="en",
            translation_model="qwen3-0.6b-int4",
            translation_device=translation.HYBRID,
        ),
        emit=events.append,
        runtime_probe=probe,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        llm_factory=factory,
        ffmpeg_runner=fake_ffmpeg,
    )
    return record, sorted(devices_loaded), events


def test_job_hybrid_uses_gpu_and_npu(tmp_path, settings, installed_model, monkeypatch):
    record, loaded, events = _run(tmp_path, settings, probe_npu, monkeypatch, free=None)
    assert record.state == JobState.COMPLETED, record.error_message
    assert loaded == ["GPU", "NPU"]
    assert record.translation_device and record.translation_device.requested == "HYBRID"
    assert sum((record.translation_device_lines or {}).values()) == 2
    assert "load_translation_gpu" in record.timings_ms
    assert "load_translation_npu" in record.timings_ms
    devices = {e.get("device") for e in events if e.get("stage") == "translating"}
    assert "GPU+NPU" in devices or "GPU" in devices


def test_job_hybrid_without_npu_runs_on_gpu(tmp_path, settings, installed_model):
    record, loaded, _ = _run(tmp_path, settings, lambda: ("2026.4", ("CPU", "GPU")))
    assert record.state == JobState.COMPLETED, record.error_message
    assert loaded == ["GPU"]
    assert record.translation_device.resolved == "GPU"
    assert record.translation_device.fallback_reason == "hybrid: NPU not available"
