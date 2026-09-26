from pathlib import Path

import pytest

from app import translation
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution
from tests.fakes import FakeLLM


def make_translator(llm: FakeLLM) -> translation.LoadedTranslator:
    return translation.LoadedTranslator(
        llm, "qwen3-0.6b-int4", DeviceResolution("auto", "NPU", ("NPU",)), 0, ("NPU",)
    )


def test_parse_numbered_handles_think_and_formats() -> None:
    raw = (
        "<think>hmm</think>\n1. Xin chào\n2) Tạm biệt\n"
        '[3]: "Cảm ơn"\nnote: ignore\n9. out of range'
    )
    assert translation.parse_numbered(raw, 3) == ["Xin chào", "Tạm biệt", "Cảm ơn"]


def test_parse_numbered_missing_is_none() -> None:
    assert translation.parse_numbered("1. A\n3. C", 3) == ["A", None, "C"]


def test_translate_batches_keep_alignment_and_progress() -> None:
    llm = FakeLLM()
    progress: list[tuple[int, int]] = []
    lines = [f"line {i}" for i in range(1, 11)]
    result = translation.translate_lines(
        make_translator(llm), lines, "en", "vi", batch_size=4,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert list(result.lines) == [f"[vi] line {i}" for i in range(1, 11)]
    assert progress == [(4, 10), (8, 10), (10, 10)]
    assert len(llm.prompts) == 3
    assert "Previous lines for context only" in llm.prompts[1]
    assert result.fallback_lines == 0


def test_missing_line_is_retried_individually() -> None:
    llm = FakeLLM(drop={2})
    result = translation.translate_lines(make_translator(llm), ["a", "b", "c"], "en", "vi")
    assert list(result.lines) == ["[vi] a", "[vi] b", "[vi] c"]
    assert result.fallback_lines == 0
    assert len(llm.prompts) == 2


def test_unrecoverable_line_keeps_source_text_and_counts_fallback() -> None:
    llm = FakeLLM(drop={1}, drop_always=True)
    result = translation.translate_lines(make_translator(llm), ["a", "b"], "en", "vi")
    assert list(result.lines) == ["a", "[vi] b"]
    assert result.fallback_lines == 1


def test_same_language_is_noop() -> None:
    llm = FakeLLM()
    result = translation.translate_lines(make_translator(llm), ["a"], "vi", "vi")
    assert result.lines == ("a",) and llm.prompts == []


def test_prompt_names_languages_and_count() -> None:
    messages = translation.build_messages(["a", "b"], "en", "vi")
    assert "English to Vietnamese" in messages[0]["content"]
    assert "exactly 2 lines" in messages[0]["content"]


def test_npu_gets_static_shape_properties(tmp_path: Path) -> None:
    seen: dict = {}

    def factory(path, device, props):
        seen[device] = props
        return FakeLLM()

    loaded = translation.load_translator(
        tmp_path, "m", "auto", ["NPU", "GPU", "CPU"], ("CPU", "NPU"), factory=factory
    )
    assert loaded.device.resolved == "NPU"
    assert seen["NPU"]["MAX_PROMPT_LEN"] >= 1024


def test_generation_error_is_wrapped() -> None:
    class Broken(FakeLLM):
        def generate(self, prompt, config=None, **kwargs):
            raise RuntimeError("boom")

    with pytest.raises(LocalSubError) as err:
        translation.translate_lines(make_translator(Broken()), ["a"], "en", "vi")
    assert err.value.code == ErrorCode.TRANSLATION_FAILED


def test_translation_model_missing_hint(tmp_path: Path) -> None:
    with pytest.raises(LocalSubError) as err:
        translation.ensure_model_present(tmp_path / "x", "qwen3-8b-int4-cw")
    assert "models download qwen3-8b-int4-cw" in err.value.message
