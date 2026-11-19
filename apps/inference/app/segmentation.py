from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.models import SubtitleCue
from app.subtitles import normalize_text

MAX_CUE_CHARS = 84  # ~2 lines x 42 chars, common broadcast guidance
MAX_CUE_MS = 7_000
MIN_CUE_MS = 500
MIN_ESTIMATED_MS = 1_000  # when Whisper omits an end timestamp
MS_PER_CHAR = 70  # ~15 characters per second reading speed
# Japanese has no spaces and denser characters: ~2 lines x 16 full-width chars, ~7 chars/s.
MAX_CUE_CHARS_CJK = 32
MS_PER_CHAR_CJK = 150
# Korean separates words with spaces, so cues still split on words, but Hangul syllables are
# dense: Netflix's Korean guide allows 2 lines x 16 characters (spaces count 0.5) and up to
# 12 characters per second for adults.
MAX_CUE_CHARS_KO = 36
MS_PER_CHAR_KO = 85
# Chinese uses the CJK limit (no spaces, 2 lines x 16 characters) but reads faster than
# Japanese: Netflix's Simplified/Traditional Chinese guides allow 9 characters per second.
MS_PER_CHAR_ZH = 110

_CJK = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
# In Japanese, Whisper often separates sentences with a plain space instead of 。
# Chinese clauses end with full-width ，；：; Chinese quotes close with ” or 」.
_BREAK_AFTER = re.compile(r"(?:[.!?;:,。、，；：！？…」』）”]|^\s)$")


def is_cjk(text: str) -> bool:
    """True when at least 30% of non-space characters are kana/kanji."""
    dense = text.replace(" ", "")
    return bool(dense) and len(_CJK.findall(dense)) >= 0.3 * len(dense)


def is_chinese(text: str) -> bool:
    """CJK text without kana: Chinese (Simplified or Traditional) rather than Japanese."""
    return is_cjk(text) and not _KANA.search(text)


def is_hangul(text: str) -> bool:
    """True when at least 30% of non-space characters are Hangul (Korean)."""
    dense = text.replace(" ", "")
    return bool(dense) and len(_HANGUL.findall(dense)) >= 0.3 * len(dense)


def max_chars_for(text: str, default: int = MAX_CUE_CHARS) -> int:
    if is_cjk(text):
        return min(default, MAX_CUE_CHARS_CJK)
    if is_hangul(text):
        return min(default, MAX_CUE_CHARS_KO)
    return default


def ms_per_char_for(text: str) -> int:
    if is_cjk(text):
        return MS_PER_CHAR_ZH if is_chinese(text) else MS_PER_CHAR_CJK
    return MS_PER_CHAR_KO if is_hangul(text) else MS_PER_CHAR


@dataclass(frozen=True)
class TimedText:
    start_ms: int
    end_ms: int | None
    text: str


def _split_text(text: str, pieces: int, max_chars: int | None = None) -> list[str]:
    """Split into at least ``pieces`` parts, adding parts until each fits ``max_chars``."""
    units = len(text) if is_cjk(text) else len(text.split(" "))
    result = _split_balanced(text, pieces, max_chars)
    while max_chars and pieces < units and any(len(p) > max_chars for p in result):
        pieces += 1
        result = _split_balanced(text, pieces, max_chars)
    return result


def _split_balanced(text: str, pieces: int, max_chars: int | None = None) -> list[str]:
    """Split text into ``pieces`` balanced parts, preferring punctuation boundaries.

    Space-separated languages split on words; Japanese (no spaces) splits on characters.
    A punctuation break up to 40% away from the balanced length wins over an exact split,
    as long as the piece stays within ``max_chars``.
    """
    cjk = is_cjk(text)
    units = list(text) if cjk else text.split(" ")
    joiner = "" if cjk else " "
    pieces = max(1, min(pieces, len(units)))
    if pieces == 1:
        return [text]
    cap = max_chars or len(text)
    result: list[str] = []
    rest = units
    for remaining in range(pieces, 1, -1):
        target = len(joiner.join(rest)) / remaining
        lengths = [len(joiner.join(rest[: i + 1])) for i in range(len(rest) - remaining + 1)]
        upper = min(cap, target * 1.4)
        total = len(joiner.join(rest))
        punct = [
            i
            for i, n in enumerate(lengths)
            if _BREAK_AFTER.search(rest[i])
            and target * 0.6 <= n <= upper
            and total - n - len(joiner) <= cap * (remaining - 1)  # the rest must still fit
        ]
        if punct:
            cut = min(punct, key=lambda i: abs(lengths[i] - target))
        else:
            cut = next((i for i, n in enumerate(lengths) if n >= target), len(lengths) - 1)
            while cut > 0 and lengths[cut] > cap:
                cut -= 1
        result.append(joiner.join(rest[: cut + 1]).strip())
        rest = rest[cut + 1 :]
    result.append(joiner.join(rest).strip())
    return [piece for piece in result if piece]


def build_cues(
    segments: Iterable[TimedText],
    audio_duration_ms: int | None = None,
    max_chars: int = MAX_CUE_CHARS,
    max_cue_ms: int = MAX_CUE_MS,
) -> list[SubtitleCue]:
    """Turn raw Whisper chunks into valid, non-overlapping, readable subtitle cues."""
    cleaned: list[TimedText] = []
    for seg in segments:
        text = normalize_text(seg.text)
        if not text:
            continue
        cleaned.append(TimedText(max(0, seg.start_ms), seg.end_ms, text))
    cleaned.sort(key=lambda s: s.start_ms)

    raw: list[tuple[int, int, str]] = []
    for i, seg in enumerate(cleaned):
        end = seg.end_ms
        if end is None or end <= seg.start_ms:
            next_start = cleaned[i + 1].start_ms if i + 1 < len(cleaned) else None
            per_char = ms_per_char_for(seg.text)
            estimate = max(MIN_ESTIMATED_MS, per_char * len(seg.text))
            fallback = seg.start_ms + min(MAX_CUE_MS, estimate)
            end = next_start if next_start and next_start > seg.start_ms else fallback
        if audio_duration_ms is not None and audio_duration_ms > seg.start_ms:
            end = min(end, audio_duration_ms)

        duration = end - seg.start_ms
        limit = max_chars_for(seg.text, max_chars)
        n_by_chars = -(-len(seg.text) // limit)
        n_by_time = -(-duration // max_cue_ms)
        wanted = max(n_by_chars, n_by_time)
        pieces = _split_text(seg.text, wanted, limit)
        total_chars = sum(len(p) for p in pieces) or 1
        # Time is shared by character count, so an uneven punctuation split can push one
        # piece past max_cue_ms; keep splitting until every share fits.
        while (
            len(pieces) < len(seg.text)
            and max(len(p) for p in pieces) * duration / total_chars > max_cue_ms
        ):
            # Split only the longest piece so sentence boundaries already found survive.
            longest = max(range(len(pieces)), key=lambda k: len(pieces[k]))
            halves = _split_text(pieces[longest], 2, limit)
            if len(halves) < 2:  # cannot split further (e.g. one long word)
                break
            pieces[longest : longest + 1] = halves
            total_chars = sum(len(p) for p in pieces) or 1

        cursor = seg.start_ms
        for j, piece in enumerate(pieces):
            if j == len(pieces) - 1:
                piece_end = end
            else:
                piece_end = cursor + round(duration * len(piece) / total_chars)
            raw.append((cursor, piece_end, piece))
            cursor = piece_end

    cues: list[SubtitleCue] = []
    previous_end = 0
    for start, end, text in raw:
        start = max(start, previous_end)
        if end <= start:
            end = start + MIN_CUE_MS
        cues.append(SubtitleCue(index=len(cues) + 1, start_ms=start, end_ms=end, text=text))
        previous_end = end
    return cues
