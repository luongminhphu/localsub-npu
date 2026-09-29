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
        '<think>hmm</think>\n1. Xin chào\n2) Tạm biệt\n[3]: "Cảm ơn"\nnote: ignore\n9. out of range'
    )
    assert translation.parse_numbered(raw, 3) == ["Xin chào", "Tạm biệt", "Cảm ơn"]


def test_parse_numbered_missing_is_none() -> None:
    assert translation.parse_numbered("1. A\n3. C", 3) == ["A", None, "C"]


def test_translate_batches_keep_alignment_and_progress() -> None:
    llm = FakeLLM()
    progress: list[tuple[int, int]] = []
    lines = [f"line {i}" for i in range(1, 11)]
    result = translation.translate_lines(
        make_translator(llm),
        lines,
        "en",
        "vi",
        batch_size=4,
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


def test_japanese_prompt_names_language_and_adds_fragment_rule() -> None:
    messages = translation.build_messages(["こんにちは"], "ja", "vi")
    system = messages[0]["content"]
    assert "from Japanese to Vietnamese" in system
    assert "fragments of one sentence" in system
    en = translation.build_messages(["Hello"], "en", "vi")[0]["content"]
    assert "fragments" not in en


def test_parse_numbered_accepts_full_width_numbering() -> None:
    raw = "１．Xin chào\n２） Cảm ơn"
    assert translation.parse_numbered(raw, 2) == ["Xin chào", "Cảm ơn"]


def test_translate_japanese_lines_to_english() -> None:
    llm = FakeLLM()
    result = translation.translate_lines(
        make_translator(llm), ["こんにちは", "ありがとう"], "ja", "en"
    )
    assert list(result.lines) == ["[vi] こんにちは", "[vi] ありがとう"]
    assert result.fallback_lines == 0
    assert "Translate to English" in llm.prompts[0]


@pytest.mark.parametrize(
    "bad",
    [
        "N. text",
        "text",
        "N. 1. 1. 1.",
        "1. 1. 1. 1. 1. 1. 1. 1.",
        "rất rất rất rất rất rất rất",
        "123",
    ],
)
def test_plausible_rejects_placeholders_and_loops(bad: str) -> None:
    assert translation.plausible(bad, "本日は決算説明会です") is None


@pytest.mark.parametrize(
    ("value", "source"),
    [("Doanh thu tăng 12%.", "売上高は12%増加しました。"), ("Toyota", "トヨタ"), ("12", "12")],
)
def test_plausible_keeps_normal_translations(value: str, source: str) -> None:
    assert translation.plausible(value, source) == value


def test_degenerate_output_is_retried_then_falls_back() -> None:
    class LoopLLM(FakeLLM):
        def generate(self, prompt, config=None, **_kwargs):
            self.prompts.append(prompt)
            return "1. 1. 1. 1. 1. 1. 1. 1."

    llm = LoopLLM()
    result = translation.translate_lines(make_translator(llm), ["こんにちは"], "ja", "vi")
    assert list(result.lines) == ["こんにちは"] and result.fallback_lines == 1
    assert len(llm.prompts) == 2  # batch + single-line retry


def _gpu_translator(llm: FakeLLM) -> translation.LoadedTranslator:
    return translation.LoadedTranslator(
        llm, "qwen3-0.6b-int4", DeviceResolution("auto", "GPU", ("GPU",)), 0, ("GPU",)
    )


def test_parallel_batches_share_one_generate_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(translation, "_config", lambda tokens: object())
    llm = FakeLLM()
    progress: list[tuple[int, int]] = []
    lines = [f"line {i}" for i in range(1, 11)]
    result = translation.translate_lines(
        _gpu_translator(llm), lines, "en", "vi", batch_size=2, parallel=3,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert list(result.lines) == [f"[vi] line {i}" for i in range(1, 11)]
    assert llm.batched_calls == 2  # 5 batches -> groups of 3 + 2
    assert progress == [(6, 10), (10, 10)]
    # Context for later batches still comes from the preceding source lines.
    assert "- line 2" in llm.prompts[1]


def test_parallel_retries_rejected_lines_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(translation, "_config", lambda tokens: object())
    llm = FakeLLM(drop={1})
    result = translation.translate_lines(
        _gpu_translator(llm), ["a", "b", "c", "d"], "en", "vi", batch_size=2, parallel=2
    )
    assert list(result.lines) == ["[vi] a", "[vi] b", "[vi] c", "[vi] d"]
    # One grouped first pass + one grouped retry of both dropped lines.
    assert llm.batched_calls == 2
    assert result.fallback_lines == 0


def test_list_generate_failure_falls_back_to_sequential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(translation, "_config", lambda tokens: object())

    class Stateful(FakeLLM):
        def generate(self, prompt, config=None, **kwargs):
            if isinstance(prompt, list):
                raise RuntimeError("list input not supported")
            return super().generate(prompt, config, **kwargs)

    llm = Stateful()
    result = translation.translate_lines(
        _gpu_translator(llm), ["a", "b", "c"], "en", "vi", batch_size=1, parallel=3
    )
    assert list(result.lines) == ["[vi] a", "[vi] b", "[vi] c"]
    assert len(llm.prompts) == 3


def test_parallelism_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    assert translation.default_parallelism("NPU") == 1
    assert translation.default_parallelism("GPU") == translation.DEFAULT_PARALLEL
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "8")
    assert translation.default_parallelism("NPU") == 8
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "999")
    assert translation.default_parallelism("CPU") == translation.DEFAULT_PARALLEL


def test_auto_translation_prefers_gpu_and_npu_uses_best_perf() -> None:
    assert translation.candidate_devices("auto") == ["GPU", "NPU", "CPU"]
    assert translation.candidate_devices("NPU") == ["NPU"]
    assert translation.npu_properties("NPU")["GENERATE_HINT"] == "BEST_PERF"
    assert translation.npu_properties("GPU") == {}


class Clock:
    """Fake clock: each generate() call on the wrapped LLM costs ``cost(n_prompts)`` s."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TimedLLM(FakeLLM):
    def __init__(self, clock: Clock, cost, fail_at: int | None = None) -> None:
        super().__init__()
        self.clock = clock
        self.cost = cost
        self.fail_at = fail_at
        self.sizes: list[int] = []

    def generate(self, prompt, config=None, **kwargs):
        n = len(prompt) if isinstance(prompt, list) else 1
        if self.fail_at is not None and n >= self.fail_at:
            raise RuntimeError("out of GPU memory")
        self.sizes.append(n)
        self.clock.now += self.cost(n)
        return super().generate(prompt, config, **kwargs)


def gpu_translator(llm: FakeLLM) -> translation.LoadedTranslator:
    return translation.LoadedTranslator(
        llm, "qwen3-8b-int4-cw", DeviceResolution("GPU", "GPU", ("GPU",)), 0, ("GPU",)
    )


LONG = [f"line {i}" for i in range(1, 801)]  # 100 batches of 8


@pytest.fixture
def batched(monkeypatch):
    monkeypatch.setattr(translation, "_config", lambda tokens: object())


def test_tuner_climbs_while_batching_pays_off(monkeypatch, batched) -> None:
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    clock = Clock()
    llm = TimedLLM(clock, cost=lambda n: 1.0 + 0.1 * n)  # bandwidth bound: batching is cheap
    result = translation.translate_lines(gpu_translator(llm), LONG, "en", "vi", clock=clock)
    assert list(result.lines) == [f"[vi] {x}" for x in LONG]
    assert result.parallel == 16
    assert [p for p, _ in result.tuning] == [4, 8, 16]
    assert llm.sizes[:4] == [4, 4, 8, 16] and set(llm.sizes[4:-1]) == {16}


def test_tuner_stops_when_next_level_is_not_faster(monkeypatch, batched) -> None:
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    clock = Clock()
    llm = TimedLLM(clock, cost=lambda n: 0.5 * n if n <= 4 else 0.6 * n)  # compute bound
    result = translation.translate_lines(gpu_translator(llm), LONG, "en", "vi", clock=clock)
    assert result.parallel == 4
    assert [p for p, _ in result.tuning] == [4, 8]
    assert set(llm.sizes[3:-1]) == {4}


def test_tuner_settles_lower_when_a_bigger_batch_fails(monkeypatch, batched) -> None:
    # A batch of 16 fails (e.g. GPU out of memory): that unit is run prompt by prompt, which
    # measures slow, so the tuner settles on 8 and no line is lost.
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    clock = Clock()
    llm = TimedLLM(clock, cost=lambda n: 1.0 + 0.1 * n, fail_at=16)
    result = translation.translate_lines(gpu_translator(llm), LONG, "en", "vi", clock=clock)
    assert list(result.lines) == [f"[vi] {x}" for x in LONG]
    assert result.parallel == 8 and [p for p, _ in result.tuning] == [4, 8, 16]
    assert max(llm.sizes) == 8


def test_env_and_npu_pin_parallelism(monkeypatch, batched) -> None:
    clock = Clock()
    monkeypatch.setenv("LOCALSUB_TRANSLATION_PARALLEL", "2")
    llm = TimedLLM(clock, cost=lambda n: 1.0)
    result = translation.translate_lines(gpu_translator(llm), LONG[:80], "en", "vi", clock=clock)
    assert result.parallel == 2 and result.tuning == () and set(llm.sizes) == {2}
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL")
    npu = FakeLLM()
    result = translation.translate_lines(make_translator(npu), LONG[:80], "en", "vi")
    assert result.parallel == 1 and npu.batched_calls == 0


def test_failure_at_minimum_level_still_raises(monkeypatch, batched) -> None:
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    llm = TimedLLM(Clock(), cost=lambda n: 1.0, fail_at=1)
    with pytest.raises(LocalSubError) as err:
        translation.translate_lines(gpu_translator(llm), LONG[:40], "en", "vi")
    assert err.value.code == ErrorCode.TRANSLATION_FAILED
