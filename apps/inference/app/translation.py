"""Local subtitle translation with an OpenVINO GenAI LLM (NPU-first).

Cues are translated in small numbered batches so each output line maps back to exactly one
input cue; timestamps are never sent to or changed by the model.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import catalog
from app.devices import compile_with_fallback, ensure_ir_present
from app.errors import ErrorCode, LocalSubError
from app.models import DeviceResolution

# Pre-converted OpenVINO IR LLMs published by Intel, from the shared model catalog.
# "-cw-" = channel-wise symmetric INT4, the layout recommended for Intel NPU.
TRANSLATION_CATALOG: dict[str, str] = {m: s.repo for m, s in catalog.of_kind("translation").items()}
DEFAULT_TRANSLATION_MODEL = "qwen3-8b-int4-cw"
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese"}
SOURCE_LANGUAGES = ("en", "ja", "ko", "vi")
TARGET_LANGUAGES = ("en", "vi")
DEFAULT_BATCH_SIZE = 8
CONTEXT_LINES = 2
# Batches submitted to one generate() call. CPU/GPU pipelines run a list of prompts with
# continuous batching (measured ~2.4x faster than sequential batches on CPU); the NPU LLM
# pipeline is stateful and single-sequence, so it stays at 1.
DEFAULT_PARALLEL = 4
MAX_PARALLEL = 16
TUNE_LADDER = (4, 8, 16)
TUNE_MIN_GAIN = 0.10  # climb only while the next level is >= 10% faster
# "auto" for translation prefers the GPU: it batches prompts. Running GPU and NPU together
# on one job was measured slower (shared LPDDR5X bandwidth), so there is no hybrid mode.
AUTO_TRANSLATION_DEVICES = ("GPU", "NPU", "CPU")

LLMFactory = Callable[[Path, str, dict[str, Any]], Any]
ProgressSink = Callable[[int, int], None]

# "1. text", "1) text", "[1]: text", "1、text" and, as small models reply, "[1] text",
# "(1) text", "**1.** text" or "- 1. text". A bare number needs a separator so "3 people
# came" is not read as line 3.
_NUMBERED = re.compile(
    r"^\s*(?:[-*\u2022>]\s+)?(?:\*\*|__)?"
    r"(?:[\[(](\d{1,3})[\])](?:\*\*|__)?\s*[.):\-\u3001]?|(\d{1,3})\s*[.):\-\u3001](?:\*\*|__)?)"
    r"\s*(.*)$"
)
_EMPHASIS = re.compile(r"^(?:\*\*|__)\s*|\s*(?:\*\*|__)$")
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
    parallel: int | None = None  # batches per generate() call the run settled on
    tuning: tuple[tuple[int, float], ...] = ()  # (parallel, lines/s) measured while tuning
    # (reason, lines) for lines kept in the source language; reasons sum to fallback_lines.
    fallback_reasons: tuple[tuple[str, int], ...] = ()


# Driver errors after which the device context is unusable: the Intel GPU plugin says that
# after CL_OUT_OF_RESOURCES "any subsequent OpenCL API call will cause program to hang", so
# retrying (even with fewer batches) or destroying the pipeline on that device is unsafe.
DEVICE_FATAL_MARKERS: tuple[str, ...] = (
    "CL_OUT_OF_RESOURCES",
    "CL_MEM_OBJECT_ALLOCATION_FAILURE",
    "CL_OUT_OF_HOST_MEMORY",
    "ZE_RESULT_ERROR_DEVICE_LOST",
    "DEVICE_LOST",
)

# Pipelines whose device context died. They are kept alive (never destroyed) until the
# worker exits with os._exit, because their destructors would call into the broken driver.
ABANDONED_PIPELINES: list[Any] = []


def is_device_fatal(exc: BaseException | None) -> bool:
    """True when ``exc`` (or its cause chain) is a driver error that poisons the device."""
    seen = 0
    while exc is not None and seen < 5:
        text = str(exc)
        if any(marker in text for marker in DEVICE_FATAL_MARKERS):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class TranslatorLost(LocalSubError):
    """The translator's device died mid-run. ``partial`` holds every line translated before
    the failing call (whole units, in order), so the caller can continue elsewhere."""

    def __init__(self, device: str | None, message: str, partial: TranslationResult) -> None:
        super().__init__(ErrorCode.TRANSLATION_FAILED, message)
        self.device = device
        self.partial = partial


def abandon(translator: LoadedTranslator) -> None:
    """Never touch this translator's pipeline again, not even to free it."""
    ABANDONED_PIPELINES.append(translator.pipeline)


REQUIRED_FILES: tuple[str, ...] = (
    "openvino_model.xml",
    "openvino_tokenizer.xml",
    "openvino_detokenizer.xml",
)
# Vision-language IR (Qwen3.5) used text-only: the vision encoder files are never loaded
# by the text path, so they are not required.
VLM_REQUIRED_FILES: tuple[str, ...] = (
    "openvino_language_model.xml",
    "openvino_text_embeddings_model.xml",
    "openvino_tokenizer.xml",
    "openvino_detokenizer.xml",
)
VLM_CACHE_GB = 2  # KV + linear-attention cache for the continuous-batching scheduler


def required_files(model_id: str) -> tuple[str, ...]:
    spec = catalog.spec(model_id)
    return VLM_REQUIRED_FILES if spec and spec.runtime == "vlm" else REQUIRED_FILES


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
        required_files(model_id),
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


def fixed_parallelism(device: str | None) -> int | None:
    """Batches per generate() call when it must not be auto-tuned: the env override, or 1 on
    the NPU (stateful, single-sequence LLM pipeline). ``None`` means auto-tune."""
    raw = os.getenv("LOCALSUB_TRANSLATION_PARALLEL", "").strip()
    if raw.isdigit() and 1 <= int(raw) <= MAX_PARALLEL:
        return int(raw)
    return 1 if device == "NPU" else None


def default_parallelism(device: str | None) -> int:
    """Starting batches per call for the resolved device."""
    fixed = fixed_parallelism(device)
    return fixed if fixed is not None else DEFAULT_PARALLEL


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


class _Texts:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts


class VLMTextPipeline:
    """Text-only adapter over a vision-language IR (Qwen3.5).

    ``VLMPipeline.generate`` takes one prompt, so the continuous-batching pipeline is used
    instead: it accepts a list of prompts with no images, which keeps the batched GPU/CPU
    path (measured on CPU: batched 124 s vs one-by-one with the MTP draft 148 s, 79 lines).
    Prefix caching is off, as OpenVINO GenAI requires for these hybrid-attention models.
    """

    def __init__(self, pipeline: Any) -> None:
        self.pipeline = pipeline

    def get_tokenizer(self) -> Any:
        return self.pipeline.get_tokenizer()

    def generate(self, prompt: Any, config: Any = None, **kwargs: Any) -> Any:
        prompts = list(prompt) if isinstance(prompt, list | tuple) else [prompt]
        if config is None:
            try:
                config = _config(int(kwargs.get("max_new_tokens", 256)))
            except ImportError:  # only in tests without openvino-genai
                config = None
        results = self.pipeline.generate(prompts, [[] for _ in prompts], [config] * len(prompts))
        texts = [str(r.texts[0]) if getattr(r, "texts", None) else "" for r in results]
        return _Texts(texts) if isinstance(prompt, list | tuple) else texts[0]


def _vlm_factory(path: Path, device: str, properties: dict[str, Any]) -> Any:
    try:
        import openvino_genai
    except ImportError as exc:
        raise LocalSubError(
            ErrorCode.OPENVINO_UNAVAILABLE, "openvino-genai is not installed in this environment."
        ) from exc
    scheduler = openvino_genai.SchedulerConfig()
    raw = os.getenv("LOCALSUB_VLM_CACHE_GB", "").strip()
    scheduler.cache_size = int(raw) if raw.isdigit() and int(raw) > 0 else VLM_CACHE_GB
    scheduler.enable_prefix_caching = False
    scheduler.max_num_seqs = MAX_PARALLEL
    pipeline = openvino_genai.ContinuousBatchingPipeline(str(path), scheduler, device, properties)
    return VLMTextPipeline(pipeline)


def devices_for(model_id: str, preference: str) -> list[str]:
    """Candidate devices for ``model_id``; an explicit device the model cannot use fails."""
    candidates = candidate_devices(preference)
    spec = catalog.spec(model_id)
    if spec is None:
        return candidates
    if preference != "auto" and preference not in spec.devices:
        raise LocalSubError(
            ErrorCode.DEVICE_UNAVAILABLE,
            f"{spec.label} runs on {'/'.join(spec.devices)} only, not {preference}.",
        )
    return [d for d in candidates if d in spec.devices]


def load_translator(
    model_path: Path,
    model_id: str,
    requested: str,
    candidates: Sequence[str],
    available_devices: tuple[str, ...],
    cache_dir: Path | None = None,
    factory: LLMFactory | None = None,
) -> LoadedTranslator:
    spec = catalog.spec(model_id)
    if factory is None:
        factory = _vlm_factory if spec and spec.runtime == "vlm" else _default_factory
    if spec is not None:
        if requested != "auto" and requested not in spec.devices:
            devices_for(model_id, requested)  # raises with a clear message
        candidates = [d for d in candidates if d in spec.devices] or list(spec.devices)
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
    if source == "ko":
        # No romanized examples here: small models (0.6B) copied them into every line.
        rules += (
            " Korean subtitle lines often split one sentence and drop the subject: use the "
            "surrounding lines to understand them, but keep one output line per input line. "
            "Render Korean honorifics and speech levels naturally in the target language, and "
            "keep Korean personal and company names."
        )
    body = ""
    if context:
        body += "Previous lines for context only (do not translate):\n"
        body += "\n".join(f"- {c}" for c in context) + "\n\n"
    body += f"Translate to {tgt}:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    return [{"role": "system", "content": rules}, {"role": "user", "content": body}]


def _clean(value: str) -> str:
    return _EMPHASIS.sub("", value.strip()).strip().strip('"').strip()


def parse_numbered(output: str, expected: int) -> list[str | None]:
    text = _THINK.sub("", output).replace("<think>", "").replace("</think>", "")
    result: list[str | None] = [None] * expected
    pending: int | None = None  # "1." alone on a line: its text is on the next line
    for line in text.splitlines():
        # Japanese-aware models may number with full-width digits/punctuation ("１．").
        match = _NUMBERED.match(unicodedata.normalize("NFKC", line))
        if not match:
            value = _clean(line)
            if pending is not None and value:
                if result[pending - 1] is None:
                    result[pending - 1] = value
                pending = None
            continue
        number = int(match.group(1) or match.group(2))
        value = _clean(match.group(3))
        pending = None
        if not 1 <= number <= expected:
            continue
        if not value:
            pending = number
        elif result[number - 1] is None:
            result[number - 1] = value
    if expected == 1 and result[0] is None:
        # One-line prompts are often answered with just the translation; several
        # unnumbered lines are prose, not a translation, so they are not guessed from.
        lines = [_clean(raw) for raw in text.splitlines() if _clean(raw)]
        if len(lines) == 1:
            result[0] = lines[0]
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
# A run of one punctuation mark ("......", "!!!!!!") or one letter ("Ahhhhhh") is how
# subtitles render "……", stacked "！？" and drawn-out sounds; it is collapsed before the
# loop check so only repeated words and numbers ("1. 1. 1.", "no no no ...") count.
_RUN = re.compile(r"([^\w\s]|[^\W\d_])\1+")


def _loops(text: str, source: str) -> bool:
    """A word or number repeated 6+ times that the source line does not repeat itself
    (a laugh or a stammer translated faithfully is not a degenerate loop)."""
    return bool(_LOOP.search(_RUN.sub(r"\1", text))) and not _LOOP.search(source)


FALLBACK_REASONS = ("missing", "placeholder", "loop", "no_letters", "too_long")


def rejection_reason(value: str | None, source: str) -> str | None:
    """Why a parsed value is not a usable translation (one of ``FALLBACK_REASONS``), or
    ``None`` when it is. Reasons are counted per job so the analysis shows which one
    dominates without storing any subtitle text."""
    if value is None:
        return "missing"
    text = value.strip()
    if not text:
        return "missing"
    if _PLACEHOLDER.match(text) or text.upper().startswith("N. "):
        return "placeholder"
    if _loops(text, source):  # "1. 1. 1. 1. ..." or a word repeated 6+ times
        return "loop"
    letters = sum(ch.isalpha() for ch in text)
    if letters == 0 and any(ch.isalpha() for ch in source):
        return "no_letters"
    if len(text) > 4 * len(source) + 40:
        return "too_long"
    return None


def plausible(value: str | None, source: str) -> str | None:
    """Reject echoes of the prompt format and degenerate loops from small models."""
    if value is None or rejection_reason(value, source):
        return None
    return value.strip()


class _Engine:
    """Translates units of batches on one pipeline: one generate() call per unit, then the
    rejected lines of the unit are retried together as single-line prompts."""

    def __init__(self, translator: LoadedTranslator, source: str, target: str, parallel: int):
        self.translator = translator
        self.source = source
        self.target = target
        self.parallel = max(1, min(parallel, MAX_PARALLEL))

    def _run(self, jobs: Sequence[tuple[Sequence[str], Sequence[str]]]) -> list[list[str | None]]:
        """Translate (batch, context) pairs; context comes from source lines, so batches
        are independent and can share one generate() call."""
        pipeline = self.translator.pipeline
        prompts = [
            _prompt(pipeline, build_messages(b, self.source, self.target, c)) for b, c in jobs
        ]
        tokens = 64 + 48 * max(len(b) for b, _ in jobs)
        try:
            raws = _generate_many(pipeline, prompts, tokens)
        except Exception as exc:
            raise LocalSubError(
                ErrorCode.TRANSLATION_FAILED, f"LLM generation failed: {str(exc)[:200]}"
            ) from exc
        return [
            [
                (plausible(v, src), rejection_reason(v, src))
                for v, src in zip(parse_numbered(raw, len(b)), b, strict=True)
            ]
            for raw, (b, _) in zip(raws, jobs, strict=True)
        ]

    def unit(
        self, group: Sequence[tuple[list[str], list[str]]]
    ) -> tuple[list[list[str]], dict[str, int]]:
        """Translate one unit; returns the lines and, per reason, the lines kept in the
        source language after the single-line retry also failed."""
        parsed = [[v for v, _ in values] for values in self._run(group)]
        reasons: dict[str, int] = {}
        # Keep the source text for lines the model still fails so the editor can fix them.
        misses = [
            (b, i) for b, values in enumerate(parsed) for i, v in enumerate(values) if v is None
        ]
        for start_idx in range(0, len(misses), self.parallel):
            chunk = misses[start_idx : start_idx + self.parallel]
            retried = self._run([([group[b][0][i]], group[b][1]) for b, i in chunk])
            for (b, i), values in zip(chunk, retried, strict=True):
                value, reason = values[0]
                if value is None:
                    key = reason or "missing"
                    reasons[key] = reasons.get(key, 0) + 1
                parsed[b][i] = value or group[b][0][i]
        return [[v or "" for v in values] for values in parsed], reasons


def _batches(lines: Sequence[str], batch_size: int) -> list[tuple[int, list[str], list[str]]]:
    return [
        (o, list(lines[o : o + batch_size]), list(lines[max(0, o - CONTEXT_LINES) : o]))
        for o in range(0, len(lines), batch_size)
    ]


class ParallelTuner:
    """Picks how many batches go into one generate() call on GPU/CPU.

    Decode is memory-bandwidth bound: a larger batch reads the weights once for more
    sequences, until compute or KV-cache pressure flattens the gain. The tuner climbs the
    ladder (4 -> 8 -> 16) one full unit at a time, measuring lines per second, and settles on
    the best level as soon as the next level is not at least ``min_gain`` faster. The first
    unit is a warm-up (kernel compilation, caches) and is not measured.
    """

    def __init__(
        self,
        ladder: Sequence[int] = TUNE_LADDER,
        fixed: int | None = None,
        min_gain: float = TUNE_MIN_GAIN,
    ) -> None:
        self.ladder = [p for p in ladder if 1 <= p <= MAX_PARALLEL] or [DEFAULT_PARALLEL]
        self.fixed = fixed
        self.min_gain = min_gain
        self.level = 0
        self.warm = False
        self.best: tuple[int, float] | None = None
        self.settled = fixed is not None or len(self.ladder) == 1
        self.samples: list[tuple[int, float]] = []  # (parallel, lines/s) for the audit log

    @property
    def parallel(self) -> int:
        if self.fixed is not None:
            return self.fixed
        if self.settled and self.best is not None:
            return self.best[0]
        return self.ladder[self.level]

    def observe(self, parallel: int, full_unit: bool, lines: int, seconds: float) -> None:
        if self.settled or not full_unit or seconds <= 0 or lines <= 0:
            return
        if not self.warm:
            self.warm = True
            return
        rate = lines / seconds
        self.samples.append((parallel, round(rate, 3)))
        if self.best is None or rate >= self.best[1] * (1 + self.min_gain):
            self.best = (parallel, rate)
            if self.level + 1 < len(self.ladder):
                self.level += 1
            else:
                self.settled = True
        else:
            self.settled = True

    def back_off(self, parallel: int) -> bool:
        """A call at ``parallel`` failed (e.g. out of GPU memory): settle one level lower.
        Returns False when there is nothing lower to fall back to."""
        lower = [p for p in self.ladder if p < parallel]
        if self.fixed is not None or not lower:
            return False
        self.samples.append((parallel, 0.0))
        self.best = (lower[-1], self.best[1] if self.best else 0.0)
        self.settled = True
        return True


def model_size_bytes(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def available_memory_bytes() -> int | None:
    """Free physical memory (the iGPU and NPU share system RAM on Intel Core Ultra)."""
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullAvailPhys)
            return None
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, AttributeError):
        return None


class LineFeed:
    """Thread-safe, append-only list of source lines that grows while Whisper runs.

    The producer calls ``extend`` as subtitle lines become final and ``close`` when no more
    will come; ``abort`` stops the consumer early (e.g. the transcription failed)."""

    def __init__(self, lines: Sequence[str] = (), closed: bool = False) -> None:
        self._lines = list(lines)
        self._closed = closed
        self._aborted = False
        self._cond = threading.Condition()

    def extend(self, lines: Sequence[str]) -> None:
        with self._cond:
            self._lines.extend(lines)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def cut(self, count: int) -> None:
        """Drop lines from ``count`` on and close: they changed after being queued. A
        consumer already past ``count`` stops at its next wait; callers keep only the
        first ``count`` results."""
        with self._cond:
            del self._lines[max(0, count) :]
            self._closed = True
            self._cond.notify_all()

    def abort(self) -> None:
        with self._cond:
            self._aborted = self._closed = True
            self._cond.notify_all()

    @property
    def aborted(self) -> bool:
        return self._aborted

    def __len__(self) -> int:
        with self._cond:
            return len(self._lines)

    def wait(self, count: int) -> tuple[list[str], bool]:
        """Block until ``count`` lines exist or the feed is closed; returns a snapshot."""
        with self._cond:
            self._cond.wait_for(lambda: len(self._lines) >= count or self._closed)
            return list(self._lines), self._closed


def translate_lines(
    translator: LoadedTranslator,
    lines: Sequence[str],
    source: str,
    target: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressSink | None = None,
    parallel: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> TranslationResult:
    """Translate ``lines``. ``parallel`` fixes the batches per call; ``None`` auto-tunes on
    GPU/CPU (``LOCALSUB_TRANSLATION_PARALLEL`` also fixes it) and uses 1 on the NPU."""
    if source == target:
        return TranslationResult(tuple(lines), 0, 0)
    return translate_feed(
        translator,
        LineFeed(lines, closed=True),
        source,
        target,
        batch_size,
        on_progress,
        parallel,
        clock,
    )


def translate_feed(
    translator: LoadedTranslator,
    feed: LineFeed,
    source: str,
    target: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressSink | None = None,
    parallel: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> TranslationResult:
    """Translate lines as they arrive in ``feed``, in order, until it is closed.

    A unit of ``tuner.parallel`` full batches is sent as soon as enough lines exist; the
    short tail is sent once the feed closes. Context lines come from the source, so the
    result is identical to translating the finished list. ``inference_ms`` counts only the
    time spent translating, not waiting for lines. ``on_progress(done, known)`` reports the
    lines known so far as the total while the feed is still open."""
    device = translator.device.resolved if translator.device else None
    if parallel is None:
        tuner = ParallelTuner(fixed=fixed_parallelism(device))
    else:
        tuner = ParallelTuner(fixed=max(1, min(parallel, MAX_PARALLEL)))
    engine = _Engine(translator, source, target, tuner.parallel)
    output: list[str] = []
    fallbacks = 0
    reasons: dict[str, int] = {}
    busy = 0.0
    while True:
        size = tuner.parallel
        lines, closed = feed.wait(len(output) + size * batch_size)
        if feed.aborted:
            break
        pending = len(lines) - len(output)
        if pending <= 0:
            if closed:
                break
            continue
        full = pending // batch_size
        count = min(size, full) if full else 1
        offset = len(output)
        batches = [
            (
                list(lines[o : o + batch_size]),
                list(lines[max(0, o - CONTEXT_LINES) : o]),
            )
            for o in range(offset, min(len(lines), offset + count * batch_size), batch_size)
        ]
        engine.parallel = size
        t0 = clock()
        try:
            values, missed = engine.unit(batches)
        except LocalSubError as exc:
            if is_device_fatal(exc):
                busy += clock() - t0
                raise TranslatorLost(
                    device,
                    f"{device or 'device'} failed while translating line {len(output) + 1}: "
                    f"{exc.message}",
                    TranslationResult(
                        tuple(output),
                        fallbacks,
                        int(busy * 1000),
                        parallel=size,
                        tuning=tuple(tuner.samples),
                        fallback_reasons=tuple(sorted(reasons.items())),
                    ),
                ) from exc
            if tuner.back_off(size):
                continue  # retry the same batches with fewer per call
            raise
        elapsed = clock() - t0
        busy += elapsed
        done = sum(len(v) for v in values)
        tuner.observe(size, len(batches) == size, done, elapsed)
        fallbacks += sum(missed.values())
        for key, n in missed.items():
            reasons[key] = reasons.get(key, 0) + n
        for v in values:
            output.extend(v)
        if on_progress:
            on_progress(len(output), max(len(output), len(feed)))
    return TranslationResult(
        tuple(output),
        fallbacks,
        int(busy * 1000),
        parallel=tuner.parallel,
        tuning=tuple(tuner.samples),
        fallback_reasons=tuple(sorted(reasons.items())),
    )
