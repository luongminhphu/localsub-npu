"""Wave 6B: translation overlaps transcription (Whisper on NPU, LLM on GPU)."""

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import translation, whisper
from app.errors import ErrorCode, LocalSubError
from app.jobs import JobRequest, OverlapTranslation, overlap_plan, run_job, stable_lines
from app.models import DeviceResolution, JobState
from app.segmentation import TimedText, build_cues
from tests.conftest import write_wav
from tests.fakes import FakeLLM
from tests.test_jobs import install_llm, make_source, probe_npu


@dataclass
class Chunk:
    start_ts: float
    end_ts: float
    text: str


CHUNKS = 4
LINES_PER_CHUNK = 12


class StreamingWhisper:
    """One call per chunk; each returns LINES_PER_CHUNK short segments and takes a while."""

    def __init__(self, delay: float = 0.05, fail_at: int | None = None) -> None:
        self.delay = delay
        self.fail_at = fail_at
        self.calls = 0
        self.finished_at: float | None = None

    def generate(self, samples, **kwargs):
        call = self.calls
        self.calls += 1
        if self.fail_at is not None and call == self.fail_at:
            raise RuntimeError("Whisper decoder crashed")
        time.sleep(self.delay)

        class R:
            chunks = [
                Chunk(i * 1.0, i * 1.0 + 0.8, f" chunk{call} line{i}")
                for i in range(LINES_PER_CHUNK)
            ]
            texts = ["x"]
            language = "<|ja|>"

        if call == CHUNKS - 1:
            self.finished_at = time.perf_counter()
        return R()


class TimedFakeLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.first_call: float | None = None
        self.lock = threading.Lock()

    def generate(self, prompt, config=None, **kwargs):
        with self.lock:
            if self.first_call is None:
                self.first_call = time.perf_counter()
        return super().generate(prompt, config, **kwargs)


def fake_ffmpeg(cmd, **_kwargs):
    write_wav(Path(cmd[-1]), seconds=CHUNKS * LINES_PER_CHUNK * 1.0)
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def chunked(monkeypatch):
    """Split the test audio into CHUNKS equal ranges, none treated as silent."""

    def split(samples, sample_rate, chunk_seconds=300.0, **_):
        step = max(1, len(samples) // CHUNKS)
        bounds = [i * step for i in range(CHUNKS)] + [len(samples)]
        return list(zip(bounds, bounds[1:], strict=False))

    monkeypatch.setattr(whisper, "split_points", split)
    monkeypatch.setattr(whisper, "_is_silent", lambda piece: False)
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "1")  # 8-line units start sooner
    monkeypatch.setattr(translation, "_config", lambda tokens: object())


def run(settings, tmp_path, asr, llm, device="GPU", llm_factory=None, **kw):
    install_llm(settings)
    events: list[dict] = []
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="ja",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            translation_device=device,
            formats=("srt",),
            **kw,
        ),
        emit=events.append,
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, dev, props: asr,
        llm_factory=llm_factory or (lambda path, dev, props: llm),
        ffmpeg_runner=fake_ffmpeg,
    )
    return record, events


def test_translation_starts_before_transcription_ends(
    tmp_path, settings, installed_model, chunked
) -> None:
    asr, llm = StreamingWhisper(), TimedFakeLLM()
    record, events = run(settings, tmp_path, asr, llm)
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.overlap is True and "NPU" in record.overlap_reason
    assert record.device.resolved == "NPU" and record.translation_device.resolved == "GPU"
    assert llm.first_call is not None and asr.finished_at is not None
    assert llm.first_call < asr.finished_at  # the GPU was busy while Whisper still ran
    assert record.translation_overlap_lines and record.translation_overlap_lines > 0
    assert "translation_after_asr" in record.timings_ms
    # Same result as translating the finished list: every cue, in order, translated once.
    srt = (settings.output_dir / "Họp giao ban.vi.srt").read_text(encoding="utf-8")
    for c in range(CHUNKS):
        for i in range(LINES_PER_CHUNK):
            assert f"[vi] chunk{c} line{i}" in srt
    assert record.cue_count == CHUNKS * LINES_PER_CHUNK
    assert len(llm.prompts) == -(-record.cue_count // translation.DEFAULT_BATCH_SIZE)
    # Translation progress interleaves with transcription progress.
    stages = [e["stage"] for e in events if e.get("type") == "progress"]
    first_mt = stages.index("translating")
    assert "transcribing" in stages[first_mt:]


def test_same_device_runs_sequentially(tmp_path, settings, installed_model, chunked) -> None:
    asr, llm = StreamingWhisper(delay=0), TimedFakeLLM()
    record, _ = run(settings, tmp_path, asr, llm, device="NPU")
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.overlap is False and "both on NPU" in record.overlap_reason
    assert llm.first_call > asr.finished_at
    assert "translation_after_asr" not in record.timings_ms


def test_overlap_can_be_disabled(tmp_path, settings, installed_model, chunked, monkeypatch):
    monkeypatch.setenv("LOCALSUB_OVERLAP", "0")
    record, _ = run(settings, tmp_path, StreamingWhisper(delay=0), TimedFakeLLM())
    assert record.state == JobState.COMPLETED
    assert record.overlap is False and "LOCALSUB_OVERLAP" in record.overlap_reason


def test_transcription_failure_stops_the_translation_thread(
    tmp_path, settings, installed_model, chunked
) -> None:
    before = {t.name for t in threading.enumerate()}
    record, _ = run(settings, tmp_path, StreamingWhisper(fail_at=2), TimedFakeLLM())
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.TRANSCRIPTION_FAILED
    time.sleep(0.2)
    leftover = [t for t in threading.enumerate() if t.name == "translate-overlap"]
    assert not leftover or "translate-overlap" in before


def test_translation_model_load_failure_fails_the_job(
    tmp_path, settings, installed_model, chunked
) -> None:
    def broken(path, device, props):
        raise RuntimeError("GPU out of memory")

    record, _ = run(settings, tmp_path, StreamingWhisper(delay=0), None, llm_factory=broken)
    assert record.state == JobState.FAILED
    assert record.error_code is not None and record.cue_count is None


def test_overlap_plan_rules(monkeypatch) -> None:
    monkeypatch.delenv("LOCALSUB_OVERLAP", raising=False)
    req = JobRequest(source=Path("x.mp4"), translate_to="vi")
    avail = ("CPU", "GPU", "NPU")
    assert overlap_plan(req, "NPU", avail)[0] is True  # auto -> GPU
    assert overlap_plan(req, "GPU", avail) == (False, "speech and translation both on GPU")
    gib = 1024**3
    ok, reason = overlap_plan(req, "NPU", avail, llm_bytes=5 * gib, free_bytes=4 * gib)
    assert ok is False and "RAM" in reason
    assert overlap_plan(req, "NPU", avail, llm_bytes=5 * gib, free_bytes=8 * gib)[0] is True
    monkeypatch.setenv("LOCALSUB_OVERLAP", "force")
    assert overlap_plan(req, "GPU", avail)[0] is True


def test_stable_lines_hold_back_only_the_newest_segment() -> None:
    segs = [
        TimedText(0, 1000, "một"),
        TimedText(1000, None, "hai"),  # no end: borrows the next start
        TimedText(3000, None, "ba"),
    ]
    assert stable_lines(segs, None) == ["một", "hai"]
    final = [c.text for c in build_cues([*segs, TimedText(4000, 5000, "bốn")], None)]
    assert final[:2] == stable_lines(segs, None)
    assert stable_lines([], None) == [] and stable_lines([TimedText(0, 1, "  ")], None) == []


def test_feed_matches_translating_the_whole_list(monkeypatch) -> None:
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "2")
    lines = [f"l{i}" for i in range(37)]
    whole_llm, fed_llm = FakeLLM(), FakeLLM()
    tr = lambda llm: translation.LoadedTranslator(  # noqa: E731
        llm, "m", DeviceResolution("GPU", "GPU", ("GPU",)), 0, ("GPU",)
    )
    whole = translation.translate_lines(tr(whole_llm), lines, "en", "vi")
    feed = translation.LineFeed()
    out: list = []
    worker = threading.Thread(
        target=lambda: out.append(translation.translate_feed(tr(fed_llm), feed, "en", "vi"))
    )
    worker.start()
    for i in range(0, len(lines), 5):
        feed.extend(lines[i : i + 5])
        time.sleep(0.005)
    feed.close()
    worker.join(5)
    assert out and out[0].lines == whole.lines
    assert sorted(fed_llm.prompts) == sorted(whole_llm.prompts)  # same prompts, same context


def test_divergent_lines_fall_back_to_translating_after_asr() -> None:
    llm = FakeLLM()
    tr = translation.LoadedTranslator(
        llm, "m", DeviceResolution("GPU", "GPU", ("GPU",)), 0, ("GPU",)
    )
    ov = OverlapTranslation(lambda: tr, "vi", lambda e: None)
    ov.start()
    ov.set_language("en")
    ov.offer(["a", "b"])
    ov.offer(["a", "B changed", "c"])  # an earlier line changed: stop feeding
    assert ov.diverged
    done = ov.finish(["a", "B changed", "c"])
    assert done == 0  # nothing reused; the job translates all lines the normal way


def test_overlap_thread_error_is_raised_as_translation_failure() -> None:
    def load():
        raise ValueError("boom")

    ov = OverlapTranslation(load, "vi", lambda e: None)
    ov.start()
    ov.set_language("en")
    with pytest.raises(LocalSubError) as err:
        ov.finish(["a"])
    assert err.value.code == ErrorCode.TRANSLATION_FAILED


def test_nothing_to_translate_when_speech_is_already_target(
    tmp_path, settings, installed_model, chunked
) -> None:
    install_llm(settings)
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            translation_device="GPU",
            formats=("srt",),
        ),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, dev, props: StreamingWhisper(delay=0),
        llm_factory=lambda path, dev, props: FakeLLM(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert "translation" not in record.timings_ms
