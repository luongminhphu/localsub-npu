"""Chinese (Mandarin) as a spoken language: recognition, cue splitting, ZH→VI/EN translation."""

import json

import pytest

from app import translation, whisper
from app.jobs import JobRequest, run_job
from app.models import JobState
from app.segmentation import (
    MAX_CUE_CHARS_CJK,
    MS_PER_CHAR_CJK,
    MS_PER_CHAR_ZH,
    TimedText,
    build_cues,
    is_chinese,
    is_cjk,
    ms_per_char_for,
)
from tests.fakes import FakeLLM
from tests.test_jobs import fake_ffmpeg, install_llm, make_source, probe_npu
from tests.test_whisper import FakePipeline

# One long sentence with Chinese commas and no spaces, as Whisper returns it.
ZH_LONG = (
    "今天的会议我们主要讨论下个季度的预算和人员安排，"
    "首先请财务部简单汇报一下上个季度的业绩，"
    "然后各个部门再整理一下需要的人数和预计的费用。"
)


class ChinesePipeline(FakePipeline):
    def generate(self, samples, **kwargs):
        self.kwargs = kwargs

        class Chunk:
            def __init__(self, start, end, text):
                self.start_ts, self.end_ts, self.text = start, end, text

        class R:
            chunks = [Chunk(0.0, 1.5, "大家好。"), Chunk(1.5, 3.0, "谢谢大家。")]
            texts = ["大家好。谢谢大家。"]
            language = "<|zh|>"

        return R()


def test_chinese_is_a_spoken_and_source_language_but_not_a_target() -> None:
    assert "zh" in whisper.LANGUAGES
    assert "zh" in translation.SOURCE_LANGUAGES
    assert translation.LANGUAGE_NAMES["zh"] == "Chinese"
    assert "zh" not in translation.TARGET_LANGUAGES


def test_chinese_is_cjk_but_told_apart_from_japanese() -> None:
    assert is_cjk("大家好，谢谢大家") and is_chinese("大家好，谢谢大家")
    assert is_chinese("大家好，謝謝大家")  # Traditional characters too
    assert is_cjk("こんにちは、田中です") and not is_chinese("こんにちは、田中です")
    assert not is_chinese("会議は明日です")  # kana present: Japanese
    assert not is_chinese("Hello there") and not is_chinese("안녕하세요")


def test_long_chinese_segment_splits_at_fullwidth_commas_within_limit() -> None:
    cues = build_cues([TimedText(0, 15_000, ZH_LONG)])
    assert len(cues) >= 3
    assert all(len(c.text) <= MAX_CUE_CHARS_CJK for c in cues)
    assert "".join(c.text for c in cues) == ZH_LONG  # nothing lost, no spaces added
    # Clause boundaries (，) are preferred over cutting inside a clause.
    assert cues[0].text.endswith("，")
    assert all(a.end_ms <= b.start_ms for a, b in zip(cues, cues[1:], strict=False))


def test_chinese_reading_speed_is_faster_than_japanese() -> None:
    assert ms_per_char_for("谢谢大家的支持") == MS_PER_CHAR_ZH < MS_PER_CHAR_CJK
    assert ms_per_char_for("ありがとうございます") == MS_PER_CHAR_CJK
    text = "谢谢大家的支持"
    cues = build_cues([TimedText(0, None, text)])
    assert cues[0].end_ms - cues[0].start_ms == max(1_000, MS_PER_CHAR_ZH * len(text))


def test_chinese_prompt_names_language_and_adds_rules() -> None:
    vi = translation.build_messages(["大家好"], "zh", "vi")[0]["content"]
    assert "from Chinese to Vietnamese" in vi
    assert "Simplified or Traditional" in vi and "Sino-Vietnamese" in vi
    en = translation.build_messages(["大家好"], "zh", "en")[0]["content"]
    assert "pinyin" in en and "Sino-Vietnamese" not in en
    assert "Traditional" not in translation.build_messages(["Hi"], "en", "vi")[0]["content"]


def test_cli_accepts_chinese_only_as_source() -> None:
    from app import main as cli

    parser = cli.build_parser()
    assert parser.parse_args(["transcribe", "x.mp4", "--language", "zh"]).language == "zh"
    args = parser.parse_args(["translate", "a.zh.srt", "--from", "zh", "--to", "vi"])
    assert (args.source_language, args.target_language) == ("zh", "vi")
    with pytest.raises(SystemExit):
        parser.parse_args(["translate", "a.srt", "--from", "en", "--to", "zh"])


@pytest.mark.parametrize("language", ["auto", "zh"])
def test_chinese_job_is_transcribed_and_translated(tmp_path, settings, installed_model, language):
    """Auto-detected Chinese used to fail with "Cannot translate from detected language"."""
    install_llm(settings)
    pipeline = ChinesePipeline()
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
    assert record.detected_language == "zh"
    if language == "zh":
        assert pipeline.kwargs["language"] == "<|zh|>"
    names = sorted(p.name for p in record.outputs)
    assert names == sorted(
        ["Họp giao ban.zh.srt", "Họp giao ban.vi.srt", "Họp giao ban.localsub.json"]
    )
    project = json.loads((settings.output_dir / "Họp giao ban.localsub.json").read_text("utf-8"))
    assert project["source_language"] == "zh" and project["target_language"] == "vi"
    assert project["cues"][1]["translation"] == "[vi] 谢谢大家。"
