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


class ChunkedPipeline:
    """Returns one chunk per call and records the language token it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict]] = []

    def generate(self, samples, **kwargs):
        self.calls.append((len(samples), dict(kwargs)))

        class R:
            chunks = [Chunk(0.5, 2.0, " hello")]
            texts = ["hello"]
            language = "<|en|>"

        return R()


def _speech(seconds: float, rate: int = 16_000) -> list[float]:
    return [0.2 if i % 2 else -0.2 for i in range(int(seconds * rate))]


def test_split_points_cut_at_quiet_frame() -> None:
    rate = 100  # tiny rate keeps the test fast; the logic is rate independent
    samples = _speech(30, rate)
    for i in range(int(9.5 * rate), int(9.8 * rate)):
        samples[i] = 0.0
    ranges = whisper.split_points(samples, rate, chunk_seconds=10, search_seconds=2)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(samples)
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False))
    assert 950 <= ranges[0][1] < 980  # inside the silent gap, not at exactly 10 s
    assert whisper.split_points([], rate) == []
    assert whisper.split_points(_speech(5, rate), rate, 10, 2) == [(0, 500)]


def test_chunked_transcribe_offsets_progress_silence_and_pinned_language(
    tmp_path: Path, monkeypatch
) -> None:
    rate = whisper.SAMPLE_RATE
    samples = _speech(4) + [0.0] * (4 * rate) + _speech(4)
    spans = [(0, 4 * rate), (4 * rate, 8 * rate), (8 * rate, 12 * rate)]
    monkeypatch.setattr(whisper, "split_points", lambda s, r, c: spans)
    pipe = ChunkedPipeline()
    model = whisper.LoadedModel(pipe, "m", tmp_path, None, 0, ())  # type: ignore[arg-type]
    progress: list[tuple[int, int]] = []
    result = whisper.transcribe(
        model, samples, language="auto", on_progress=lambda d, t: progress.append((d, t))
    )
    assert len(pipe.calls) == 2  # the silent middle chunk is skipped
    assert "language" not in pipe.calls[0][1]
    assert pipe.calls[1][1]["language"] == "<|en|>"  # detected once, then pinned
    assert [(s.start_ms, s.end_ms) for s in result.segments] == [(500, 2000), (8500, 10000)]
    assert progress == [(4000, 12000), (8000, 12000), (12000, 12000)]
    assert result.detected_language == "en"
