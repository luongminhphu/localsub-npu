"""Korean as a spoken language: recognition, cue splitting and KO→VI/EN translation."""

import json

import pytest

from app import translation, whisper
from app.jobs import JobRequest, run_job
from app.models import JobState
from app.segmentation import MAX_CUE_CHARS_KO, TimedText, build_cues, is_cjk, is_hangul
from tests.fakes import FakeLLM
from tests.test_jobs import fake_ffmpeg, install_llm, make_source, probe_npu
from tests.test_whisper import FakePipeline

KO_LONG = (
    "오늘 회의에서는 다음 분기 예산과 인력 계획을 논의하겠습니다. "
    "먼저 재무팀에서 지난 분기 실적을 간단히 보고해 주시고, "
    "그다음에 각 부서별로 필요한 인원과 예상 비용을 정리해 주세요."
)


class KoreanPipeline(FakePipeline):
    def generate(self, samples, **kwargs):
        self.kwargs = kwargs

        class Chunk:
            def __init__(self, start, end, text):
                self.start_ts, self.end_ts, self.text = start, end, text

        class R:
            chunks = [Chunk(0.0, 1.5, "안녕하세요."), Chunk(1.5, 3.0, "감사합니다.")]
            texts = ["안녕하세요. 감사합니다."]
            language = "<|ko|>"

        return R()


def test_korean_is_a_spoken_and_source_language_but_not_a_target() -> None:
    assert "ko" in whisper.LANGUAGES
    assert "ko" in translation.SOURCE_LANGUAGES
    assert translation.LANGUAGE_NAMES["ko"] == "Korean"
    assert "ko" not in translation.TARGET_LANGUAGES


def test_hangul_detection_is_separate_from_japanese() -> None:
    assert is_hangul("안녕하세요 반갑습니다")
    assert not is_cjk("안녕하세요 반갑습니다")  # Korean keeps word-based splitting
    assert not is_hangul("こんにちは") and not is_hangul("Hello there")


def test_long_korean_segment_splits_on_words_within_limit() -> None:
    cues = build_cues([TimedText(0, 12_000, KO_LONG)])
    assert len(cues) >= 3
    assert all(len(c.text) <= MAX_CUE_CHARS_KO for c in cues)
    # Split on spaces: joining the pieces with spaces restores the original words.
    assert " ".join(c.text for c in cues).split() == KO_LONG.split()
    assert all(a.end_ms <= b.start_ms for a, b in zip(cues, cues[1:], strict=False))


def test_korean_missing_end_uses_korean_reading_speed() -> None:
    text = "감사합니다 정말 감사합니다"  # 13 chars
    cues = build_cues([TimedText(0, None, text)])
    assert cues[0].end_ms - cues[0].start_ms == 85 * len(text)


def test_korean_prompt_names_language_and_adds_speech_level_rule() -> None:
    system = translation.build_messages(["안녕하세요"], "ko", "vi")[0]["content"]
    assert "from Korean to Vietnamese" in system
    assert "speech levels" in system and "-ssi" not in system
    assert "speech levels" not in translation.build_messages(["Hi"], "en", "vi")[0]["content"]


def test_cli_accepts_korean_source_only_as_source() -> None:
    from app import main as cli

    parser = cli.build_parser()
    assert parser.parse_args(["transcribe", "x.mp4", "--language", "ko"]).language == "ko"
    args = parser.parse_args(["translate", "a.ko.srt", "--from", "ko", "--to", "vi"])
    assert (args.source_language, args.target_language) == ("ko", "vi")
    with pytest.raises(SystemExit):
        parser.parse_args(["translate", "a.srt", "--from", "en", "--to", "ko"])


@pytest.mark.parametrize("language", ["auto", "ko"])
def test_korean_job_is_transcribed_and_translated(tmp_path, settings, installed_model, language):
    install_llm(settings)
    pipeline = KoreanPipeline()
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language=language,
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            translation_device="CPU",
            formats=("srt", "json"),
        ),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: pipeline,
        llm_factory=lambda path, device, props: FakeLLM(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.detected_language == "ko"
    if language == "ko":
        assert pipeline.kwargs["language"] == "<|ko|>"
    names = sorted(p.name for p in record.outputs)
    assert names == sorted(
        ["Họp giao ban.ko.srt", "Họp giao ban.vi.srt", "Họp giao ban.localsub.json"]
    )
    project = json.loads((settings.output_dir / "Họp giao ban.localsub.json").read_text("utf-8"))
    assert project["source_language"] == "ko" and project["target_language"] == "vi"
    assert project["cues"][1]["translation"] == "[vi] 감사합니다."
