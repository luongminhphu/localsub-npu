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

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
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


BUILTIN_MODELS: tuple[ModelSpec, ...] = (
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

# --- Models found on Hugging Face by ``models discover`` -------------------------------
# Only OpenVINO's own pre-converted repos whose names match what the worker can run.
DISCOVERED_FILE = "discovered.json"
_ASR_REPO = re.compile(r"(distil-)?whisper-[a-z0-9.-]+-int[48]-ov", re.IGNORECASE)
# Qwen2.5 base models are not chat-tuned, so only its -Instruct builds; Qwen3+ are chat models.
_LLM_REPO = re.compile(
    r"qwen(2\.5-[0-9.]+b-instruct|[3-9][0-9.]*-[0-9.]+b)-int4(-cw)?-ov", re.IGNORECASE
)
_RUNTIME_BY_TAG = {
    "automatic-speech-recognition": "whisper",
    "text-generation": "llm",
    "image-text-to-text": "vlm",
}


def spec_from_hub(repo: str, pipeline_tag: str | None, approx_mb: int = 0) -> ModelSpec | None:
    """A ModelSpec for an ``OpenVINO/<name>`` repo, or None when the worker cannot run it."""
    owner, _, name = repo.partition("/")
    runtime = _RUNTIME_BY_TAG.get(pipeline_tag or "")
    if owner != "OpenVINO" or runtime is None:
        return None
    if runtime == "whisper":
        if not _ASR_REPO.fullmatch(name):
            return None
        kind = "asr"
    elif _LLM_REPO.fullmatch(name):
        kind = "translation"
    else:
        return None
    model_id = name[: -len("-ov")].lower()
    # INT4 builds that are not channel-wise (-cw) are GPU/CPU only, like the catalog's INT4 models.
    int4_gpu = "int4" in model_id and not model_id.endswith("-cw")
    return ModelSpec(
        model_id,
        kind,
        repo,
        name[: -len("-ov")],
        max(int(approx_mb), 0),
        runtime,
        "Tự tìm thấy trên Hugging Face (OpenVINO); chưa đo trên máy này.",
        "Found on Hugging Face (OpenVINO); not measured on this machine yet.",
        devices=("GPU", "CPU") if int4_gpu or runtime == "vlm" else ALL_DEVICES,
        languages=("en",) if model_id.startswith("distil-") else None,
        experimental=True,
        min_openvino="2026.4" if runtime == "vlm" else None,
        tags=("discovered",),
    )


def discovered_path(model_dir: Path) -> Path:
    return model_dir / DISCOVERED_FILE


def read_discovered(path: Path) -> list[dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def load_discovered(path: Path, builtin: tuple[ModelSpec, ...]) -> tuple[ModelSpec, ...]:
    """Specs saved by ``models discover``, rebuilt (and so re-validated) from repo names."""
    taken = {m.id for m in builtin} | {m.repo.lower() for m in builtin}
    specs = []
    for row in read_discovered(path):
        try:
            repo, tag = str(row["repo"]), row.get("pipeline_tag")
            found = spec_from_hub(repo, tag, row.get("approx_mb", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if found and found.id not in taken and found.repo.lower() not in taken:
            taken |= {found.id, found.repo.lower()}
            specs.append(found)
    return tuple(specs)


# shortcut: read at import from LOCALSUB_MODEL_DIR (the desktop always sets it); a CLI that
# sets the model dir only in .env sees discovered models once that variable is exported.
MODELS: tuple[ModelSpec, ...] = BUILTIN_MODELS + load_discovered(
    discovered_path(Path(os.getenv("LOCALSUB_MODEL_DIR", "./data/models"))), BUILTIN_MODELS
)

BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODELS}


def of_kind(kind: str) -> dict[str, ModelSpec]:
    return {m.id: m for m in MODELS if m.kind == kind}


def spec(model_id: str) -> ModelSpec | None:
    return BY_ID.get(model_id)
