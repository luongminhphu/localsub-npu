"""The translation device dies mid-run (Intel GPU CL_OUT_OF_RESOURCES): keep what was
translated, never touch the broken pipeline again, continue on the CPU when Auto."""

import pytest

from app import translation
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution, JobState
from tests.fakes import FakeLLM
from tests.test_overlap import CHUNKS, LINES_PER_CHUNK, StreamingWhisper, chunked, run  # noqa: F401

GPU_DEATH = (
    "Exception from src\\inference\\src\\cpp\\infer_request.cpp:224: Exception from "
    "src\\plugins\\intel_gpu\\src\\runtime\\ocl\\ocl_common.hpp:62: [GPU] CL_OUT_OF_RESOURCES "
    "exception. Due to a driver bug, any subsequent OpenCL API call will cause program to "
    "hang, so program will be terminated."
)


class DyingLLM(FakeLLM):
    """Works for ``ok_calls`` generate() calls, then fails like the Intel GPU driver."""

    def __init__(self, ok_calls: int, message: str = GPU_DEATH) -> None:
        super().__init__()
        self.ok_calls = ok_calls
        self.message = message
        self.calls = 0

    def generate(self, prompt, config=None, **kwargs):
        self.calls += 1
        if self.calls > self.ok_calls:
            raise RuntimeError(self.message)
        return super().generate(prompt, config, **kwargs)


def _translator(llm, device="GPU"):
    return translation.LoadedTranslator(
        llm, "qwen3-0.6b-int4", DeviceResolution("auto", device, (device,)), 0, (device,)
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(translation, "_config", lambda tokens: object())
    monkeypatch.setattr(translation, "ABANDONED_PIPELINES", [], raising=False)


def test_fatal_gpu_error_keeps_partial_result_and_does_not_retry() -> None:
    llm = DyingLLM(ok_calls=1)
    lines = [f"line {i}" for i in range(40)]
    with pytest.raises(translation.TranslatorLost) as err:
        translation.translate_lines(_translator(llm), lines, "en", "vi", parallel=1)
    lost = err.value
    assert lost.code == ErrorCode.TRANSLATION_FAILED and lost.device == "GPU"
    batch = translation.DEFAULT_BATCH_SIZE
    assert lost.partial.lines == tuple(f"[vi] line {i}" for i in range(batch))
    assert llm.calls == 2  # no back-off retry on the poisoned device
    assert f"line {batch + 1}" in lost.message


def test_non_fatal_error_still_backs_off() -> None:
    assert not translation.is_device_fatal(RuntimeError("GPU out of memory"))
    assert translation.is_device_fatal(RuntimeError(GPU_DEATH))
    wrapped = LocalSubError(ErrorCode.TRANSLATION_FAILED, "LLM generation failed: x")
    wrapped.__cause__ = RuntimeError("[GPU] CL_MEM_OBJECT_ALLOCATION_FAILURE")
    assert translation.is_device_fatal(wrapped)


def _factory(gpu_llm, cpu_llm, devices):
    def factory(path, device, props):
        devices.append(device)
        return gpu_llm if device == "GPU" else cpu_llm

    return factory


def test_job_continues_on_cpu_after_gpu_dies(
    tmp_path, settings, installed_model, chunked, monkeypatch  # noqa: F811
) -> None:
    monkeypatch.setenv("LOCALSUB_OVERLAP", "0")
    gpu, cpu, devices = DyingLLM(ok_calls=2), FakeLLM(), []
    record, _ = run(
        settings, tmp_path, StreamingWhisper(delay=0), None, device="auto",
        llm_factory=_factory(gpu, cpu, devices),
    )  # fmt: skip
    assert record.state == JobState.COMPLETED, record.error_message
    assert devices == ["GPU", "CPU"]
    assert record.translation_device.resolved == "CPU"
    assert "GPU driver failed" in record.translation_device.fallback_reason
    batch = translation.DEFAULT_BATCH_SIZE
    assert record.translation_recoveries == [
        {"at_line": 2 * batch, "from": "GPU", "to": "CPU", "ok": True}
    ]
    assert translation.ABANDONED_PIPELINES == [gpu]
    srt = (settings.output_dir / "Họp giao ban.vi.srt").read_text(encoding="utf-8")
    for c in range(CHUNKS):
        for i in range(LINES_PER_CHUNK):
            assert f"[vi] chunk{c} line{i}" in srt
    assert record.translation_fallback_lines == 0


def test_overlap_thread_recovers_on_cpu(
    tmp_path, settings, installed_model, chunked  # noqa: F811
) -> None:
    gpu, cpu, devices = DyingLLM(ok_calls=1), FakeLLM(), []
    record, _ = run(
        settings, tmp_path, StreamingWhisper(delay=0.02), None, device="auto",
        llm_factory=_factory(gpu, cpu, devices),
    )  # fmt: skip
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.overlap is True
    assert devices == ["GPU", "CPU"]
    assert record.translation_recoveries[0]["ok"] is True
    assert translation.ABANDONED_PIPELINES == [gpu]
    srt = (settings.output_dir / "Họp giao ban.vi.srt").read_text(encoding="utf-8")
    assert srt.count("[vi] chunk") == CHUNKS * LINES_PER_CHUNK


def test_explicit_gpu_does_not_fall_back(
    tmp_path, settings, installed_model, chunked, monkeypatch  # noqa: F811
) -> None:
    monkeypatch.setenv("LOCALSUB_OVERLAP", "0")
    gpu, cpu, devices = DyingLLM(ok_calls=0), FakeLLM(), []
    record, _ = run(
        settings, tmp_path, StreamingWhisper(delay=0), None, device="GPU",
        llm_factory=_factory(gpu, cpu, devices),
    )  # fmt: skip
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.TRANSLATION_FAILED
    assert "choose Auto or CPU" in record.error_message
    assert devices == ["GPU"]
    assert record.translation_recoveries[0]["ok"] is False


ONEDNN_FAIL = (
    "Exception from src\\inference\\src\\cpp\\infer_request.cpp:224: Exception from "
    "src\\plugins\\intel_gpu\\src\\graph\\impls\\onednn\\primitive_onednn_base.h:559: "
    "could not execute a primitive"
)


def test_gpu_error_that_survives_back_off_hands_over_to_cpu() -> None:
    # Not a known driver-death marker, so the GPU is retried with fewer batches first; when
    # that still fails the translator is lost (CPU next) instead of failing the whole job.
    assert not translation.is_device_fatal(RuntimeError(ONEDNN_FAIL))
    llm = DyingLLM(ok_calls=1, message=ONEDNN_FAIL)
    lines = [f"line {i}" for i in range(80)]
    with pytest.raises(translation.TranslatorLost) as err:
        translation.translate_lines(_translator(llm), lines, "en", "vi", parallel=1)
    assert err.value.device == "GPU"
    assert len(err.value.partial.lines) == translation.DEFAULT_BATCH_SIZE
    assert "could not execute a primitive" in err.value.message


def test_cpu_error_still_fails_plainly() -> None:
    llm = DyingLLM(ok_calls=0, message=ONEDNN_FAIL)
    with pytest.raises(LocalSubError) as err:
        translation.translate_lines(_translator(llm, "CPU"), ["a", "b"], "en", "vi", parallel=1)
    assert not isinstance(err.value, translation.TranslatorLost)


def test_job_continues_on_cpu_after_onednn_failure(
    tmp_path, settings, installed_model, chunked, monkeypatch  # noqa: F811
) -> None:
    monkeypatch.setenv("LOCALSUB_OVERLAP", "0")
    gpu, cpu, devices = DyingLLM(ok_calls=1, message=ONEDNN_FAIL), FakeLLM(), []
    record, _ = run(
        settings, tmp_path, StreamingWhisper(delay=0), None, device="auto",
        llm_factory=_factory(gpu, cpu, devices),
    )  # fmt: skip
    assert record.state == JobState.COMPLETED, record.error_message
    assert devices == ["GPU", "CPU"]
    assert record.translation_device.resolved == "CPU"
    assert record.translation_recoveries[0]["ok"] is True
    srt = (settings.output_dir / "Họp giao ban.vi.srt").read_text(encoding="utf-8")
    assert srt.count("[vi] chunk") == CHUNKS * LINES_PER_CHUNK
