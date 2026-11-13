"""Single source of truth for the models the app can download and run.

Adding a model = adding one ``ModelSpec`` here. The worker derives its Whisper / LLM
catalogs, approximate download sizes and CLI choices from this list, and the desktop app
builds its model pickers from ``models list`` (labels, hints, languages, devices), so no
UI or Rust change is needed for a new pre-converted OpenVINO model.

``runtime`` says how the folder is loaded:

* ``whisper`` - ``WhisperPipeline``
* ``llm``     - ``LLMPipeline`` (text-only LLM IR, batched prompts on CPU/GPU)
* ``vlm``     - ``ContinuousBatchingPipeline`` on a vision-language IR, used text-only
  (Qwen3.5 ships only as image-text-to-text; the vision parts are never run)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALL_DEVICES = ("NPU", "GPU", "CPU")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str  # "asr" | "translation"
    repo: str
    label: str
    approx_mb: int
    runtime: str  # "whisper" | "llm" | "vlm"
    hint_vi: str
    hint_en: str
    devices: tuple[str, ...] = ALL_DEVICES  # devices it is known to run on
    languages: tuple[str, ...] | None = None  # spoken languages (ASR); None = multilingual
    experimental: bool = False  # not yet measured on the target laptop
    min_openvino: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "runtime": self.runtime,
            "hint": {"vi": self.hint_vi, "en": self.hint_en},
            "devices": list(self.devices),
            "languages": list(self.languages) if self.languages else None,
            "experimental": self.experimental,
            "min_openvino": self.min_openvino,
            "tags": list(self.tags),
        }


MODELS: tuple[ModelSpec, ...] = (
    # --- Speech recognition (Whisper) -------------------------------------------------
    ModelSpec(
        "whisper-small-int8",
        "asr",
        "OpenVINO/whisper-small-int8-ov",
        "Whisper small",
        250,
        "whisper",
        "Mặc định khuyến nghị cho NPU.",
        "Recommended default for the NPU.",
        tags=("recommended",),
    ),
    ModelSpec(
        "whisper-base-int8",
        "asr",
        "OpenVINO/whisper-base-int8-ov",
        "Whisper base",
        120,
        "whisper",
        "Nhanh hơn, kém chính xác hơn một chút.",
        "Faster, slightly less accurate.",
    ),
    ModelSpec(
        "whisper-tiny-int8",
        "asr",
        "OpenVINO/whisper-tiny-int8-ov",
        "Whisper tiny",
        70,
        "whisper",
        "Chỉ để thử nhanh.",
        "Smoke tests only.",
    ),
    ModelSpec(
        "whisper-medium-int8",
        "asr",
        "OpenVINO/whisper-medium-int8-ov",
        "Whisper medium",
        800,
        "whisper",
        "Chính xác hơn; chạy GPU hoặc CPU.",
        "Better accuracy; GPU or CPU.",
    ),
    ModelSpec(
        "whisper-large-v3-turbo-int8",
        "asr",
        "OpenVINO/whisper-large-v3-turbo-int8-ov",
        "Whisper large-v3 turbo",
        850,
        "whisper",
        "Cân bằng tốc độ/độ chính xác tốt nhất trên Arc GPU.",
        "Best speed/accuracy on the Arc GPU.",
    ),
    ModelSpec(
        "whisper-large-v3-turbo-int4",
        "asr",
        "OpenVINO/whisper-large-v3-turbo-int4-ov",
        "Whisper large-v3 turbo (INT4)",
        550,
        "whisper",
        "Bản turbo nén INT4, nhẹ hơn bản INT8; GPU/CPU. Thử nghiệm: so chất lượng với INT8.",
        "Turbo with INT4 weights, smaller than INT8; GPU/CPU. Experimental: compare with INT8.",
        # INT4_ASYM, group 128: the NPU plugin prefers symmetric channel-wise INT4.
        devices=("GPU", "CPU"),
        experimental=True,
        min_openvino="2026.1",
    ),
    ModelSpec(
        "whisper-large-v3-int8",
        "asr",
        "OpenVINO/whisper-large-v3-int8-ov",
        "Whisper large-v3",
        1600,
        "whisper",
        "Chính xác nhất, chậm nhất.",
        "Highest accuracy, slowest.",
    ),
    ModelSpec(
        "distil-whisper-large-v3-int8",
        "asr",
        "OpenVINO/distil-whisper-large-v3-int8-ov",
        "Distil-Whisper large-v3 (EN)",
        780,
        "whisper",
        "Chỉ tiếng Anh: gần bằng large-v3 nhưng nhanh hơn nhiều (2 lớp decoder).",
        "English only: close to large-v3 accuracy, much faster (2 decoder layers).",
        languages=("en",),
        experimental=True,
        tags=("english-only",),
    ),
    # --- Translation (LLM) ------------------------------------------------------------
    ModelSpec(
        "qwen3-8b-int4-cw",
        "translation",
        "OpenVINO/Qwen3-8B-int4-cw-ov",
        "Qwen3 8B",
        5000,
        "llm",
        "Chất lượng tốt nhất; layout INT4-CW cho NPU.",
        "Best quality; INT4-CW layout for the NPU.",
        tags=("recommended",),
    ),
    ModelSpec(
        "qwen3-14b-int4",
        "translation",
        "OpenVINO/Qwen3-14B-int4-ov",
        "Qwen3 14B",
        8300,
        "llm",
        "Lớn hơn Qwen3 8B: có thể dịch tốt hơn nhưng chậm hơn; nên có RAM 32 GB; GPU/CPU.",
        "Larger than Qwen3 8B: may translate better, but slower; 32 GB RAM advised; GPU/CPU.",
        devices=("GPU", "CPU"),
        experimental=True,
    ),
    ModelSpec(
        "qwen2.5-7b-instruct-int4",
        "translation",
        "OpenVINO/Qwen2.5-7B-Instruct-int4-ov",
        "Qwen2.5 7B",
        4500,
        "llm",
        "Chất lượng tốt; chạy tốt nhất trên Arc GPU.",
        "Good quality; best on the Arc GPU.",
    ),
    ModelSpec(
        "qwen3-4b-int4",
        "translation",
        "OpenVINO/Qwen3-4B-int4-ov",
        "Qwen3 4B",
        2300,
        "llm",
        "Cân bằng tốc độ và chất lượng.",
        "Balanced speed and quality.",
    ),
    ModelSpec(
        "qwen2.5-1.5b-instruct-int4",
        "translation",
        "OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov",
        "Qwen2.5 1.5B",
        1000,
        "llm",
        "Bản nháp nhanh; cần soát kỹ.",
        "Fast draft; review carefully.",
    ),
    ModelSpec(
        "qwen3-0.6b-int4",
        "translation",
        "OpenVINO/Qwen3-0.6B-int4-ov",
        "Qwen3 0.6B",
        500,
        "llm",
        "Chỉ để thử nhanh; dịch kém.",
        "Smoke tests only; poor translations.",
    ),
    ModelSpec(
        "qwen3.5-9b-int4",
        "translation",
        "OpenVINO/Qwen3.5-9B-int4-ov",
        "Qwen3.5 9B",
        6300,
        "vlm",
        "Thế hệ mới (09/2026), đa ngôn ngữ tốt hơn; GPU/CPU. Thử nghiệm: đo tốc độ trước.",
        "Newer generation (09/2026), stronger multilingual; GPU/CPU. Experimental: benchmark.",
        devices=("GPU", "CPU"),
        experimental=True,
        min_openvino="2026.4",
    ),
    ModelSpec(
        "qwen3.5-4b-int4",
        "translation",
        "OpenVINO/Qwen3.5-4B-int4-ov",
        "Qwen3.5 4B",
        3600,
        "vlm",
        "Thế hệ mới, cân bằng; GPU/CPU. Thử nghiệm: đo tốc độ trước.",
        "Newer generation, balanced; GPU/CPU. Experimental: benchmark first.",
        devices=("GPU", "CPU"),
        experimental=True,
        min_openvino="2026.4",
    ),
    ModelSpec(
        "qwen3.5-2b-int4",
        "translation",
        "OpenVINO/Qwen3.5-2B-int4-ov",
        "Qwen3.5 2B",
        2100,
        "vlm",
        "Thế hệ mới, nhẹ; GPU/CPU. Thử nghiệm: đo tốc độ trước.",
        "Newer generation, light; GPU/CPU. Experimental: benchmark first.",
        devices=("GPU", "CPU"),
        experimental=True,
        min_openvino="2026.4",
    ),
)

BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODELS}


def of_kind(kind: str) -> dict[str, ModelSpec]:
    return {m.id: m for m in MODELS if m.kind == kind}


def spec(model_id: str) -> ModelSpec | None:
    return BY_ID.get(model_id)
