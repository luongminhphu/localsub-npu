"""Local subtitle translation with an OpenVINO GenAI LLM (NPU-first).

Cues are translated in small numbered batches so each output line maps back to exactly one
input cue; timestamps are never sent to or changed by the model.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.devices import compile_with_fallback, ensure_ir_present, snapshot
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
LANGUAGE_NAMES = {"en": "English", "vi": "Vietnamese"}
DEFAULT_BATCH_SIZE = 8
CONTEXT_LINES = 2

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
        ("openvino_model.xml", "openvino_tokenizer.xml", "openvino_detokenizer.xml"),
        model_id,
        "models download",
    )


def download_model(model_dir: Path, model_id: str) -> Path:
    target = model_path_for(model_dir, model_id)
    snapshot(TRANSLATION_CATALOG[model_id], target)
    return ensure_model_present(target, model_id)


def npu_properties(device: str) -> dict[str, Any]:
    # NPU compiles static shapes: reserve room for a batch prompt and its translation.
    if device == "NPU":
        return {"MAX_PROMPT_LEN": 1536, "MIN_RESPONSE_LEN": 768}
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
        match = _NUMBERED.match(line)
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


def _generate(pipeline: Any, prompt: str, max_new_tokens: int) -> str:
    try:
        import openvino_genai

        config = openvino_genai.GenerationConfig()
        config.max_new_tokens = max_new_tokens
        config.do_sample = False
        if hasattr(config, "apply_chat_template"):
            config.apply_chat_template = False
        return str(pipeline.generate(prompt, config))
    except ImportError:
        return str(pipeline.generate(prompt, max_new_tokens=max_new_tokens))


def translate_lines(
    translator: LoadedTranslator,
    lines: Sequence[str],
    source: str,
    target: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressSink | None = None,
) -> TranslationResult:
    if source == target:
        return TranslationResult(tuple(lines), 0, 0)
    started = time.perf_counter()
    output: list[str] = []
    fallbacks = 0
    total = len(lines)

    def run(batch: Sequence[str], context: Sequence[str]) -> list[str | None]:
        messages = build_messages(batch, source, target, context)
        tokens = 64 + 48 * len(batch)
        try:
            raw = _generate(translator.pipeline, _prompt(translator.pipeline, messages), tokens)
        except Exception as exc:
            raise LocalSubError(
                ErrorCode.TRANSLATION_FAILED, f"LLM generation failed: {str(exc)[:200]}"
            ) from exc
        return parse_numbered(raw, len(batch))

    for offset in range(0, total, batch_size):
        batch = list(lines[offset : offset + batch_size])
        context = lines[max(0, offset - CONTEXT_LINES) : offset]
        parsed = run(batch, context)
        for i, value in enumerate(parsed):
            if value is None:
                # Retry the single line; if the model still fails keep the source text so the
                # editor can fix it rather than silently dropping a cue.
                retry = run([batch[i]], context)[0]
                if retry is None:
                    fallbacks += 1
                parsed[i] = retry or batch[i]
        output.extend(v or "" for v in parsed)
        if on_progress:
            on_progress(min(offset + batch_size, total), total)

    return TranslationResult(
        tuple(output), fallbacks, int((time.perf_counter() - started) * 1000)
    )
