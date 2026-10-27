"""Response shapes small translators produce that used to be counted as untranslated lines.

Each case was a line kept in the source language even though the model had answered it:
the batch parse missed it, the single-line retry missed it again, and the job reported it
in ``translation_fallback_lines`` (15-36% of the lines of recent Japanese jobs).
"""

from __future__ import annotations

import pytest

from app import translation
from app.models import DeviceResolution
from tests.fakes import FakeLLM


def make_translator(llm: FakeLLM) -> translation.LoadedTranslator:
    return translation.LoadedTranslator(
        llm, "qwen3.5-4b-int4", DeviceResolution("auto", "GPU", ("GPU",)), 0, ("GPU",)
    )


class ScriptedLLM(FakeLLM):
    """Answers every prompt with ``reply(lines)`` where ``lines`` are the numbered inputs."""

    def __init__(self, reply) -> None:
        super().__init__()
        self.reply = reply

    def _one(self, prompt):
        self.prompts.append(prompt)
        body = prompt.split("Translate to", 1)[1]
        import re

        lines = re.findall(r"^\d+\. (.*)$", body, flags=re.M)
        return self.reply(lines)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[1] Hello\n[2] Bye", ["Hello", "Bye"]),  # bracket is the separator
        ("(1) Hello\n(2) Bye", ["Hello", "Bye"]),
        ("**1.** Hello\n**2.** Bye", ["Hello", "Bye"]),  # markdown bold numbers
        ("**1. Hello**\n**2. Bye**", ["Hello", "Bye"]),
        ("- 1. Hello\n- 2. Bye", ["Hello", "Bye"]),  # bullet before the number
        ("1、こんにちは\n2、さようなら", ["こんにちは", "さようなら"]),  # ideographic comma
        ("1.\nHello\n2.\nBye", ["Hello", "Bye"]),  # number alone, text on the next line
    ],
)
def test_parse_numbered_accepts_common_formats(raw: str, expected: list[str]) -> None:
    assert translation.parse_numbered(raw, len(expected)) == expected


def test_parse_numbered_single_unnumbered_reply() -> None:
    # A one-line prompt is often answered with just the translation.
    assert translation.parse_numbered("<think>\n\n</think>\nAh, wait!", 1) == ["Ah, wait!"]
    assert translation.parse_numbered('"Thank you."', 1) == ["Thank you."]


def test_parse_numbered_does_not_guess_from_prose() -> None:
    # Several unnumbered lines are an explanation, not a translation.
    assert translation.parse_numbered("Sure, here it is:\nAh, wait!", 1) == [None]
    # Unnumbered text never fills a multi-line batch.
    assert translation.parse_numbered("Hello\nBye", 2) == [None, None]
    # Bare numbers inside a sentence are not line numbers.
    assert translation.parse_numbered("3 people came", 3) == [None, None, None]


def test_parse_numbered_keeps_existing_formats() -> None:
    raw = '<think>hmm</think>\n1. Xin chào\n2) Tạm biệt\n[3]: "Cảm ơn"\n9. out of range'
    assert translation.parse_numbered(raw, 3) == ["Xin chào", "Tạm biệt", "Cảm ơn"]


def test_markdown_numbered_batch_is_not_a_fallback() -> None:
    llm = ScriptedLLM(lambda lines: "\n".join(f"**{i}.** [en] {t}" for i, t in enumerate(lines, 1)))
    result = translation.translate_lines(
        make_translator(llm), ["あっ", "待って", "はい"], "ja", "en", parallel=1
    )
    assert list(result.lines) == ["[en] あっ", "[en] 待って", "[en] はい"]
    assert result.fallback_lines == 0
    assert len(llm.prompts) == 1  # no single-line retries


def test_unnumbered_single_line_retry_is_kept() -> None:
    def reply(lines: list[str]) -> str:
        if len(lines) > 1:  # the batch drops line 2 ...
            return "\n".join(f"{i}. [en] {t}" for i, t in enumerate(lines, 1) if i != 2)
        return f"[en] {lines[0]}"  # ... and the retry answers without a number

    llm = ScriptedLLM(reply)
    result = translation.translate_lines(
        make_translator(llm), ["あっ", "待って", "はい"], "ja", "en", parallel=1
    )
    assert list(result.lines) == ["[en] あっ", "[en] 待って", "[en] はい"]
    assert result.fallback_lines == 0


def test_fallback_reasons_are_counted() -> None:
    def reply(lines: list[str]) -> str:
        if len(lines) > 1:
            return "1. [en] ok"  # lines 2 and 3 missing from the batch
        if lines[0] == "B":
            return "1. 1. 1. 1. 1. 1. 1. 1."  # degenerate loop
        return "Sure, here it is:\nsomething"  # prose: not parsed

    llm = ScriptedLLM(reply)
    result = translation.translate_lines(make_translator(llm), ["A", "B", "C"], "en", "vi")
    assert list(result.lines) == ["[en] ok", "B", "C"]
    assert result.fallback_lines == 2
    assert dict(result.fallback_reasons) == {"loop": 1, "missing": 1}


@pytest.mark.parametrize(
    ("value", "source", "reason"),
    [
        (None, "あ", "missing"),
        ("N. text", "あ", "placeholder"),
        ("1. 1. 1. 1. 1. 1. 1. 1.", "本日は", "loop"),
        ("123", "本日は", "no_letters"),
        ("This answer explains the line instead of translating it at length.", "はい", "too_long"),
        ("Yes.", "はい", None),
    ],
)
def test_rejection_reason(value: str | None, source: str, reason: str | None) -> None:
    assert translation.rejection_reason(value, source) == reason
    assert (translation.plausible(value, source) is None) == (reason is not None)


def test_job_audit_records_fallback_reasons(tmp_path, settings, installed_model) -> None:
    import json

    from app.jobs import JobRequest, run_job
    from app.models import JobState
    from tests.test_jobs import fake_ffmpeg, install_llm, make_source, probe_npu
    from tests.test_whisper import FakePipeline

    install_llm(settings)
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="en",
            translation_model="qwen3-0.6b-int4",
            translation_device="CPU",
        ),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        llm_factory=lambda path, device, props: FakeLLM(drop={1}, drop_always=True),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.translation_fallback_lines == 1
    assert record.translation_fallback_reasons == {"missing": 1}
    audit = (settings.output_dir / "logs" / "jobs.jsonl").read_text(encoding="utf-8")
    entry = json.loads(audit.splitlines()[-1])
    assert entry["translation_fallback_reasons"] == {"missing": 1}
    assert "Xin chào" not in audit  # reasons only, never subtitle text


def test_analysis_and_compare_show_fallback_reasons(tmp_path) -> None:
    from app import analysis
    from tests.test_analysis import job, write_log

    a = job(
        "a",
        translate_to="en",
        cue_count=100,
        translation_fallback_lines=30,
        translation_fallback_reasons={"missing": 25, "loop": 5},
    )
    report = analysis.analyze(write_log(tmp_path, [a]))
    finding = next(f for f in report["findings"] if f["id"] == "translation_fallback_lines")
    assert finding["evidence"]["jobs"][0]["fallback_reasons"] == {"missing": 25, "loop": 5}
    b = {**a, "job_id": "b", "translation_fallback_reasons": {"missing": 2}}
    diff = analysis.compare_jobs(a, b)["differences"]
    assert diff["translation_fallback_reasons"] == {
        "a": {"missing": 25, "loop": 5},
        "b": {"missing": 2},
    }
