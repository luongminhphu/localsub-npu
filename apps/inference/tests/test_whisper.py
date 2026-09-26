from dataclasses import dataclass
from pathlib import Path

import pytest

from app import whisper
from app.errors import ErrorCode, LocalSubError


def factory_failing_on(*bad: str):
    calls: list[str] = []

    def factory(path, device, props):
        calls.append(device)
        if device in bad:
            raise RuntimeError(f"Exception from src/plugins/{device}: compile error\ntrace")
        return object()

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def test_auto_falls_back_from_npu_compile_failure(tmp_path: Path) -> None:
    f = factory_failing_on("NPU")
    loaded = whisper.load_model(
        tmp_path, "m", "auto", ["NPU", "GPU", "CPU"], ("CPU", "GPU", "NPU"), factory=f
    )
    assert loaded.device.resolved == "GPU"
    assert "NPU compile failed" in (loaded.device.fallback_reason or "")
    assert loaded.attempts == ("NPU", "GPU")


def test_auto_prefers_npu(tmp_path: Path) -> None:
    loaded = whisper.load_model(
        tmp_path, "m", "auto", ["NPU", "GPU", "CPU"], ("CPU", "GPU.0", "NPU"),
        cache_dir=tmp_path / "cache", factory=factory_failing_on(),
    )
    assert loaded.device.resolved == "NPU"
    assert loaded.device.fallback_reason is None


def test_explicit_npu_compile_failure_does_not_fallback(tmp_path: Path) -> None:
    f = factory_failing_on("NPU")
    with pytest.raises(LocalSubError) as err:
        whisper.load_model(tmp_path, "m", "NPU", ["NPU"], ("CPU", "NPU"), factory=f)
    assert err.value.code == ErrorCode.MODEL_COMPILE_FAILED
    assert f.calls == ["NPU"]


def test_explicit_device_missing(tmp_path: Path) -> None:
    with pytest.raises(LocalSubError) as err:
        whisper.load_model(tmp_path, "m", "NPU", ["NPU"], ("CPU",), factory=factory_failing_on())
    assert err.value.code == ErrorCode.DEVICE_UNAVAILABLE


def test_model_missing_message_has_download_hint(tmp_path: Path) -> None:
    with pytest.raises(LocalSubError) as err:
        whisper.ensure_model_present(tmp_path / "nope", "whisper-small-int8")
    assert "models download whisper-small-int8" in err.value.message


@dataclass
class Chunk:
    start_ts: float
    end_ts: float
    text: str


class FakePipeline:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def generate(self, samples, **kwargs):
        self.kwargs = kwargs

        class R:
            chunks = [Chunk(0.0, 1.25, " Xin chào"), Chunk(1.25, -1.0, " các bạn")]
            texts = ["Xin chào các bạn"]
            language = "<|vi|>"

        return R()


def test_transcribe_maps_chunks_and_language_token(tmp_path: Path) -> None:
    pipe = FakePipeline()
    model = whisper.LoadedModel(pipe, "m", tmp_path, None, 0, ())  # type: ignore[arg-type]
    result = whisper.transcribe(model, [0.0] * 10, language="vi", task="transcribe")
    assert pipe.kwargs == {"task": "transcribe", "return_timestamps": True, "language": "<|vi|>"}
    assert [(s.start_ms, s.end_ms) for s in result.segments] == [(0, 1250), (1250, None)]
    assert result.detected_language == "vi"


def test_transcribe_auto_language_omits_token(tmp_path: Path) -> None:
    pipe = FakePipeline()
    model = whisper.LoadedModel(pipe, "m", tmp_path, None, 0, ())  # type: ignore[arg-type]
    whisper.transcribe(model, [0.0], language="auto", task="translate")
    assert "language" not in pipe.kwargs and pipe.kwargs["task"] == "translate"
