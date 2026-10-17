"""Overlap translation must not throw away finished work (job 71e360046e6b, 2026-10-17).

The audit log showed ``translation_overlap_lines = 0`` on every "speech on NPU, translation
on GPU" job: the GPU translated while Whisper ran, then ``finish`` discarded the whole run
because one earlier subtitle line had changed, and the job translated everything again.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import translation, whisper
from app.jobs import JobRequest, OverlapTranslation, run_job, stable_lines
from app.models import DeviceResolution, JobState
from app.segmentation import TimedText, build_cues
from tests.conftest import write_wav
from tests.fakes import FakeLLM
from tests.test_jobs import install_llm, make_source, probe_npu

CHUNKS = 4
PIECE_S = 12.0
# A long Japanese line with no end timestamp: its cue length (and so how it is split)
# depends on where the next segment starts.
LONG_JA = "これは長い字幕の一行です、まだ続きます"


@dataclass
class Chunk:
    start_ts: float
    end_ts: float
    text: str


class OvershootWhisper:
    """Each 12 s piece ends with an open-ended line and a timestamp past the piece end.

    Whisper pads every window to 30 s, so on a short final window it can emit a start
    time beyond the audio it was given; the next piece then has earlier segments."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, samples, **kwargs):
        call = self.calls
        self.calls += 1

        class R:
            chunks = [
                *(Chunk(i * 1.0, i * 1.0 + 0.8, f" chunk{call} line{i}") for i in range(5)),
                Chunk(5.0, -1.0, f"{LONG_JA}{call}"),  # no end: borrows the next start
                Chunk(12.5, -1.0, f" chunk{call} tail"),  # past the 12 s piece
            ]
            texts = ["x"]
            language = "<|ja|>"

        return R()


def fake_ffmpeg(cmd, **_kwargs):
    write_wav(Path(cmd[-1]), seconds=CHUNKS * PIECE_S)
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def chunked(monkeypatch):
    def split(samples, sample_rate, chunk_seconds=300.0, **_):
        step = max(1, len(samples) // CHUNKS)
        bounds = [i * step for i in range(CHUNKS)] + [len(samples)]
        return list(zip(bounds, bounds[1:], strict=False))

    monkeypatch.setattr(whisper, "split_points", split)
    monkeypatch.setattr(whisper, "_is_silent", lambda piece: False)
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "1")
    monkeypatch.setattr(translation, "_config", lambda tokens: object())


def test_overlap_keeps_its_work_when_whisper_overshoots_a_piece(
    tmp_path, settings, installed_model, chunked
) -> None:
    install_llm(settings)
    llm = FakeLLM()
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="ja",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            translation_device="GPU",
            formats=("srt",),
        ),
        emit=lambda e: None,
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, dev, props: OvershootWhisper(),
        llm_factory=lambda path, dev, props: llm,
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.overlap is True
    assert record.translation_overlap_diverged_at is None
    # Every cue came from the overlapped run, and each batch was translated exactly once.
    assert record.translation_overlap_lines == record.cue_count
    assert len(llm.prompts) == -(-record.cue_count // translation.DEFAULT_BATCH_SIZE)


def test_stable_lines_respect_the_piece_horizon() -> None:
    piece = [
        TimedText(0, 800, "một"),
        TimedText(5000, None, LONG_JA),  # borrows the next start
        TimedText(12500, None, "đuôi"),  # past the 12 s piece end
    ]
    after = [*piece, TimedText(12000, 13000, "tiếp")]  # next piece starts at 12 s
    first = stable_lines(piece, None, horizon_ms=12000)
    later = stable_lines(after, None, horizon_ms=24000)
    assert later[: len(first)] == first
    final = [c.text for c in build_cues(after, None)]
    assert final[: len(first)] == first
    assert first == ["một"]  # the open-ended line waits for the next piece


def test_stable_lines_release_closed_segments_before_the_horizon() -> None:
    segs = [TimedText(0, 800, "một"), TimedText(1000, 1800, "hai")]
    assert stable_lines(segs, None, horizon_ms=12000) == ["một", "hai"]
    assert stable_lines(segs, None) == ["một"]  # no horizon: newest still held back


def _translator(llm: FakeLLM) -> translation.LoadedTranslator:
    return translation.LoadedTranslator(
        llm, "m", DeviceResolution("GPU", "GPU", ("GPU",)), 0, ("GPU",)
    )


def test_divergence_keeps_whole_batches_before_the_changed_line() -> None:
    lines = [f"l{i}" for i in range(40)]
    ov = OverlapTranslation(lambda: _translator(FakeLLM()), "vi", lambda e: None)
    ov.start()
    ov.set_language("en")
    ov.offer(lines)
    final = [*lines[:20], "l20 changed", *lines[21:]]
    done = ov.finish(final)
    batch = translation.DEFAULT_BATCH_SIZE
    assert done == (20 // batch) * batch  # 16 lines reused, not 0
    assert ov.diverged_at == 20
    assert ov.result is not None
    assert list(ov.result.lines[:done]) == [f"[vi] l{i}" for i in range(done)]


def test_divergence_mid_run_stops_feeding_past_the_reusable_prefix() -> None:
    lines = [f"l{i}" for i in range(24)]
    ov = OverlapTranslation(lambda: _translator(FakeLLM()), "vi", lambda e: None)
    ov.offer(lines)
    ov.offer([*lines[:10], "changed", *lines[11:]])
    assert ov.diverged and ov.diverged_at == 10
    assert len(ov.feed) == 8  # cut back to the last whole batch before line 10
