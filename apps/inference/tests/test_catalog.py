"""Shared model catalog, Qwen3.5 text-only adapter and single-language ASR models."""

from pathlib import Path

import pytest

from app import catalog, model_store, translation, whisper
from app.errors import ErrorCode, LocalSubError
from app.jobs import JobRequest, run_job
from app.models import DeviceResolution, JobState
from tests.fakes import FakeLLM
from tests.test_jobs import fake_ffmpeg, make_source, probe_npu


def test_catalog_is_consistent() -> None:
    ids = [m.id for m in catalog.MODELS]
    assert len(ids) == len(set(ids))
    for m in catalog.MODELS:
        assert m.kind in ("asr", "translation")
        assert m.runtime in ("whisper", "llm", "vlm")
        assert (m.kind == "asr") == (m.runtime == "whisper")
        assert m.repo.startswith("OpenVINO/") and m.approx_mb > 0
        assert m.hint_vi and m.hint_en and m.label
        assert set(m.devices) <= set(catalog.ALL_DEVICES)
    assert set(whisper.MODEL_CATALOG) == set(catalog.of_kind("asr"))
    assert set(translation.TRANSLATION_CATALOG) == set(catalog.of_kind("translation"))
    assert set(model_store.APPROX_SIZE_MB) == set(ids)
    assert whisper.DEFAULT_MODEL in whisper.MODEL_CATALOG
    assert translation.DEFAULT_TRANSLATION_MODEL in translation.TRANSLATION_CATALOG


def test_models_list_carries_catalog_metadata(tmp_path: Path) -> None:
    rows = {r["id"]: r for r in model_store.list_models(tmp_path)}
    q35 = rows["qwen3.5-4b-int4"]
    assert q35["runtime"] == "vlm" and q35["devices"] == ["GPU", "CPU"] and q35["experimental"]
    assert q35["hint"]["vi"] and q35["label"] == "Qwen3.5 4B"
    assert rows["distil-whisper-large-v3-int8"]["languages"] == ["en"]
    assert rows["whisper-small-int8"]["languages"] is None


def test_vlm_models_need_language_model_files() -> None:
    assert "openvino_language_model.xml" in translation.required_files("qwen3.5-9b-int4")
    assert "openvino_model.xml" in translation.required_files("qwen3-8b-int4-cw")
    entry = model_store.entry_for("qwen3.5-2b-int4")
    assert entry.required == translation.VLM_REQUIRED_FILES


class FakeVLMResult:
    def __init__(self, text: str) -> None:
        self.texts = [text]


class FakeContinuousBatching:
    """Mimics ContinuousBatchingPipeline.generate(prompts, images, configs)."""

    def __init__(self) -> None:
        self.llm = FakeLLM()
        self.calls: list[tuple[int, list]] = []

    def get_tokenizer(self):
        return self.llm.get_tokenizer()

    def generate(self, prompts, images, configs):
        assert isinstance(prompts, list) and len(images) == len(prompts) == len(configs)
        assert all(i == [] for i in images)  # text-only: never sends images
        self.calls.append((len(prompts), images))
        return [FakeVLMResult(self.llm._one(p)) for p in prompts]


def test_vlm_adapter_batches_and_translates(monkeypatch) -> None:
    monkeypatch.delenv("LOCALSUB_TRANSLATION_PARALLEL", raising=False)
    monkeypatch.setattr(translation, "_config", lambda n: {"max_new_tokens": n})
    fake = FakeContinuousBatching()
    pipe = translation.VLMTextPipeline(fake)
    tr = translation.LoadedTranslator(
        pipe, "qwen3.5-4b-int4", DeviceResolution("auto", "GPU", ("GPU",)), 0, ("GPU",)
    )
    lines = [f"line {i}" for i in range(40)]
    result = translation.translate_lines(tr, lines, "en", "vi", parallel=4)
    assert result.lines == tuple(f"[vi] line {i}" for i in range(40))
    assert max(n for n, _ in fake.calls) == 4  # several prompts in one generate() call
    single = "Translate to Vietnamese:\n1. a"
    assert pipe.generate(single) == fake.llm._one(single)  # one prompt returns its text


def test_vlm_model_uses_vlm_factory_and_skips_npu(tmp_path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_vlm_factory(path, device, props):
        seen.append(device)
        return translation.VLMTextPipeline(FakeContinuousBatching())

    monkeypatch.setattr(translation, "_vlm_factory", fake_vlm_factory)
    loaded = translation.load_translator(
        tmp_path,
        "qwen3.5-4b-int4",
        "auto",
        translation.candidate_devices("auto"),
        ("NPU", "GPU", "CPU"),
    )
    assert seen == ["GPU"] and loaded.device.resolved == "GPU"
    assert isinstance(loaded.pipeline, translation.VLMTextPipeline)


def test_vlm_model_rejects_explicit_npu(tmp_path) -> None:
    with pytest.raises(LocalSubError) as err:
        translation.load_translator(
            tmp_path, "qwen3.5-4b-int4", "NPU", ["NPU"], ("NPU", "GPU", "CPU")
        )
    assert err.value.code == ErrorCode.DEVICE_UNAVAILABLE
    assert "GPU/CPU" in err.value.message


def test_llm_model_keeps_llm_factory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(translation, "_vlm_factory", lambda *a: pytest.fail("vlm used"))
    monkeypatch.setattr(translation, "_default_factory", lambda p, d, props: FakeLLM())
    loaded = translation.load_translator(
        tmp_path, "qwen3-4b-int4", "auto", ["GPU", "NPU", "CPU"], ("GPU",)
    )
    assert loaded.device.resolved == "GPU"


@pytest.mark.parametrize(
    ("language", "task", "ok"),
    [
        ("en", "transcribe", True),
        ("auto", "transcribe", False),
        ("ja", "transcribe", False),
        ("en", "translate", False),
    ],
)
def test_english_only_asr_language_check(language, task, ok) -> None:
    if ok:
        whisper.check_language_support("distil-whisper-large-v3-int8", language, task)
        return
    with pytest.raises(LocalSubError) as err:
        whisper.check_language_support("distil-whisper-large-v3-int8", language, task)
    assert err.value.code == ErrorCode.INPUT_UNSUPPORTED


def test_multilingual_asr_accepts_any_language() -> None:
    for language in ("auto", "en", "ja", "vi"):
        whisper.check_language_support("whisper-small-int8", language, "translate")


def test_job_with_english_only_model_on_japanese_fails_early(tmp_path, settings) -> None:
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path), language="ja", model_id="distil-whisper-large-v3-int8"
        ),
        runtime_probe=probe_npu,
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.INPUT_UNSUPPORTED
