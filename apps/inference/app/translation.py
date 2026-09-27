"""Local subtitle translation with an OpenVINO GenAI LLM (NPU-first).

Cues are translated in small numbered batches so each output line maps back to exactly one
input cue; timestamps are never sent to or changed by the model.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.devices import compile_with_fallback, ensure_ir_present
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution

# Pre-converted OpenVINO IR LLMs published by Intel. "-cw-" = channel-wise symmetric INT4,
# the layout recommended for Intel NPU.
TRANSLATION_CATALOG: dict[str, str] = {
    "qwen3-8b-int4-cw": "OpenVINO/Qwen3-8B-int4-cw-ov",
    "qwen2.5-7b-instruct-int4": "OpenVINO/Qwen2.5-7B-Instruct-int4-ov",
    "qwen3-4b-int4": "OpenVINO/Qwen3-4B-int4-ov",
    "qwen2.5-1.5b-instruct-int4": "OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov",
    "qwen3-0.6b-int4": "OpenVINO/Qwen3-0.6B-int4-ov",
}
DEFAULT_TRANSLATION_MODEL = "qwen3-8b-int4-cw"
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "vi": "Vietnamese"}
SOURCE_LANGUAGES = ("en", "ja", "vi")
TARGET_LANGUAGES = ("en", "vi")
DEFAULT_BATCH_SIZE = 8
CONTEXT_LINES = 2
# Batches submitted to one generate() call. CPU/GPU pipelines run a list of prompts with
# continuous batching (measured ~2.4x faster than sequential batches on CPU); the NPU LLM
# pipeline is stateful and single-sequence, so it stays at 1.
DEFAULT_PARALLEL = 4
MAX_PARALLEL = 16
# "auto" for translation prefers the GPU: it batches prompts and leaves the NPU free for
# Whisper. Users can still pick NPU explicitly.
AUTO_TRANSLATION_DEVICES = ("GPU", "NPU", "CPU")

LLMFactory = Callable[[Path, str, dict[str, Any]], Any]
ProgressSink = Callable[[int, int], None]

_NUMBERED = re.compile(r"^\s*(?:\[)?(\d{1,3})(?:\])?\s*[.):\-]\s*(.*)$")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass(frozen=True)
class LoadedTranslator:
    pipeline: Any
    model_id: str
    device: DeviceResolution
    load_ms: int
    attempts: tuple[str, ...]


@dataclass(frozen=True)
class TranslationResult:
    lines: tuple[str, ...]
    fallback_lines: int
    inference_ms: int


REQUIRED_FILES: tuple[str, ...] = (
    "openvino_model.xml",
    "openvino_tokenizer.xml",
    "openvino_detokenizer.xml",
)


def model_path_for(model_dir: Path, model_id: str) -> Path:
    if model_id not in TRANSLATION_CATALOG:
        known = ", ".join(sorted(TRANSLATION_CATALOG))
        raise LocalSubError(
            ErrorCode.MODEL_NOT_FOUND, f"Unknown translation model '{model_id}'. Known: {known}"
        )
    return model_dir / model_id


def ensure_model_present(path: Path, model_id: str) -> Path:
    return ensure_ir_present(
        path,
        REQUIRED_FILES,
        model_id,
        "models download",
    )


def download_model(model_dir: Path, model_id: str) -> Path:
    from app import model_store

    return model_store.download(model_dir, model_id).path


def candidate_devices(preference: str) -> list[str]:
    if preference == "auto":
        return list(AUTO_TRANSLATION_DEVICES)
    return [preference]


def default_parallelism(device: str | None) -> int:
    """Prompts per generate() call for the resolved device (env override for tuning)."""
    raw = os.getenv("LOCALSUB_TRANSLATION_PARALLEL", "").strip()
    if raw.isdigit() and 1 <= int(raw) <= MAX_PARALLEL:
        return int(raw)
    return 1 if device == "NPU" else DEFAULT_PARALLEL


def npu_properties(device: str) -> dict[str, Any]:
    # NPU compiles static shapes: reserve room for a batch prompt and its translation.
    # BEST_PERF trades a slower first compile (cached afterwards) for faster token decode.
    if device == "NPU":
        return {"MAX_PROMPT_LEN": 1536, "MIN_RESPONSE_LEN": 768, "GENERATE_HINT": "BEST_PERF"}
    return {}


def _default_factory(path: Path, device: str, properties: dict[str, Any]) -> Any:
    try:
        import openvino_genai
    except ImportError as exc:
        raise LocalSubError(
            ErrorCode.OPENVINO_UNAVAILABLE, "openvino-genai is not installed in this environment."
        ) from exc
    return openvino_genai.LLMPipeline(str(path), device, **properties)


def load_translator(
    model_path: Path,
    model_id: str,
    requested: str,
    candidates: Sequence[str],
    available_devices: tuple[str, ...],
    cache_dir: Path | None = None,
    factory: LLMFactory = _default_factory,
) -> LoadedTranslator:
    compiled = compile_with_fallback(
        model_path,
        requested,
        candidates,
        available_devices,
        factory,
        cache_dir,
        properties_for=npu_properties,
    )
    return LoadedTranslator(
        compiled.pipeline, model_id, compiled.device, compiled.load_ms, compiled.attempts
    )


def build_messages(
    lines: Sequence[str], source: str, target: str, context: Sequence[str] = ()
) -> list[dict[str, str]]:
    src = LANGUAGE_NAMES.get(source, source)
    tgt = LANGUAGE_NAMES.get(target, target)
    rules = (
        f"You translate video subtitles from {src} to {tgt}. "
        f"Return exactly {len(lines)} lines, numbered 1 to {len(lines)}, in the form 'N. text'. "
        "Translate each line on its own line, keep names, tickers and numbers unchanged, "
        "keep it short enough for subtitles, and add no notes or explanations."
    )
    if source == "ja":
        rules += (
            " Japanese subtitle lines are often fragments of one sentence: use the surrounding "
            "lines to understand them, but keep one output line per input line. Drop honorifics "
            "such as -san only when unnatural, and keep Japanese personal and company names."
        )
    body = ""
    if context:
        body += "Previous lines for context only (do not translate):\n"
        body += "\n".join(f"- {c}" for c in context) + "\n\n"
    body += f"Translate to {tgt}:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    return [{"role": "system", "content": rules}, {"role": "user", "content": body}]


def parse_numbered(output: str, expected: int) -> list[str | None]:
    text = _THINK.sub("", output).replace("<think>", "").replace("</think>", "")
    result: list[str | None] = [None] * expected
    for line in text.splitlines():
        # Japanese-aware models may number with full-width digits/punctuation ("１．").
        match = _NUMBERED.match(unicodedata.normalize("NFKC", line))
        if not match:
            continue
        number = int(match.group(1))
        value = match.group(2).strip().strip('"').strip()
        if 1 <= number <= expected and value and result[number - 1] is None:
            result[number - 1] = value
    return result


def _prompt(pipeline: Any, messages: list[dict[str, str]]) -> str:
    tokenizer = pipeline.get_tokenizer()
    try:
        return tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, extra_context={"enable_thinking": False}
        )
    except TypeError:  # older openvino-genai without extra_context
        return tokenizer.apply_chat_template(messages, add_generation_prompt=True)


def _config(max_new_tokens: int) -> Any:
    import openvino_genai

    config = openvino_genai.GenerationConfig()
    config.max_new_tokens = max_new_tokens
    config.do_sample = False
    if hasattr(config, "apply_chat_template"):
        config.apply_chat_template = False
    return config


def _generate(pipeline: Any, prompt: str, max_new_tokens: int) -> str:
    try:
        config = _config(max_new_tokens)
    except ImportError:
        return str(pipeline.generate(prompt, max_new_tokens=max_new_tokens))
    return str(pipeline.generate(prompt, config))


def _generate_many(pipeline: Any, prompts: Sequence[str], max_new_tokens: int) -> list[str]:
    """Run several prompts in one call when the pipeline supports it, else one by one."""
    if len(prompts) > 1:
        try:
            config = _config(max_new_tokens)
            result = pipeline.generate(list(prompts), config)
            texts = list(getattr(result, "texts", None) or [])
            if len(texts) == len(prompts):
                return [str(t) for t in texts]
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 - e.g. a stateful pipeline rejecting a list
            pass  # fall through: the sequential path reports real generation errors
    return [_generate(pipeline, p, max_new_tokens) for p in prompts]


_PLACEHOLDER = re.compile(r"^(?:N\s*[.)]\s*)?(?:text|translation)?\s*[.]?$", re.IGNORECASE)
_LOOP = re.compile(r"(\S{1,12}?)(?:\s*\1){5,}")


def plausible(value: str | None, source: str) -> str | None:
    """Reject echoes of the prompt format and degenerate loops from small models."""
    if value is None:
        return None
    text = value.strip()
    if not text or _PLACEHOLDER.match(text) or text.upper().startswith("N. "):
        return None
    if _LOOP.search(text):  # "1. 1. 1. 1. ..." or a word repeated 6+ times
        return None
    letters = sum(ch.isalpha() for ch in text)
    if letters == 0 and any(ch.isalpha() for ch in source):
        return None
    if len(text) > 4 * len(source) + 40:
        return None
    return text


def translate_lines(
    translator: LoadedTranslator,
    lines: Sequence[str],
    source: str,
    target: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressSink | None = None,
    parallel: int | None = None,
) -> TranslationResult:
    if source == target:
        return TranslationResult(tuple(lines), 0, 0)
    started = time.perf_counter()
    if parallel is None:
        parallel = default_parallelism(translator.device.resolved)
    parallel = max(1, min(parallel, MAX_PARALLEL))
    output: list[str] = []
    fallbacks = 0
    total = len(lines)

    def run(jobs: Sequence[tuple[Sequence[str], Sequence[str]]]) -> list[list[str | None]]:
        """Translate (batch, context) pairs; context comes from source lines, so batches
        are independent and can share one generate() call."""
        pipeline = translator.pipeline
        prompts = [_prompt(pipeline, build_messages(b, source, target, c)) for b, c in jobs]
        tokens = 64 + 48 * max(len(b) for b, _ in jobs)
        try:
            raws = _generate_many(pipeline, prompts, tokens)
        except Exception as exc:
            raise LocalSubError(
                ErrorCode.TRANSLATION_FAILED, f"LLM generation failed: {str(exc)[:200]}"
            ) from exc
        return [
            [plausible(v, src) for v, src in zip(parse_numbered(raw, len(b)), b, strict=True)]
            for raw, (b, _) in zip(raws, jobs, strict=True)
        ]

    offsets = list(range(0, total, batch_size))
    for g in range(0, len(offsets), parallel):
        group = [
            (list(lines[o : o + batch_size]), list(lines[max(0, o - CONTEXT_LINES) : o]))
            for o in offsets[g : g + parallel]
        ]
        parsed = run(group)
        # Retry every rejected line of the group together (single-line prompts), then keep
        # the source text for lines the model still fails so the editor can fix them.
        misses = [
            (b, i) for b, values in enumerate(parsed) for i, v in enumerate(values) if v is None
        ]
        for start_idx in range(0, len(misses), parallel):
            chunk = misses[start_idx : start_idx + parallel]
            retried = run([([group[b][0][i]], group[b][1]) for b, i in chunk])
            for (b, i), values in zip(chunk, retried, strict=True):
                if values[0] is None:
                    fallbacks += 1
                parsed[b][i] = values[0] or group[b][0][i]
        for values in parsed:
            output.extend(v or "" for v in values)
        if on_progress:
            on_progress(len(output), total)

    return TranslationResult(tuple(output), fallbacks, int((time.perf_counter() - started) * 1000))
