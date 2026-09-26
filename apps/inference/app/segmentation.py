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


@dataclass(frozen=True)
class TimedText:
    start_ms: int
    end_ms: int | None
    text: str


def _split_text(text: str, pieces: int) -> list[str]:
    """Split text into ``pieces`` word-balanced parts, preferring punctuation boundaries."""
    words = text.split(" ")
    pieces = max(1, min(pieces, len(words)))
    if pieces == 1:
        return [text]
    target = len(text) / pieces
    result: list[str] = []
    current: list[str] = []
    for i, word in enumerate(words):
        current.append(word)
        length = len(" ".join(current))
        remaining_words = len(words) - i - 1
        remaining_pieces = pieces - len(result) - 1
        at_punct = bool(re.search(r"[.!?;:,]$", word))
        if remaining_pieces and remaining_words >= remaining_pieces and (
            length >= target or (at_punct and length >= target * 0.6)
        ):
            result.append(" ".join(current))
            current = []
    if current:
        result.append(" ".join(current))
    return result


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
            estimate = max(MIN_ESTIMATED_MS, MS_PER_CHAR * len(seg.text))
            fallback = seg.start_ms + min(MAX_CUE_MS, estimate)
            end = next_start if next_start and next_start > seg.start_ms else fallback
        if audio_duration_ms is not None and audio_duration_ms > seg.start_ms:
            end = min(end, audio_duration_ms)

        duration = end - seg.start_ms
        n_by_chars = -(-len(seg.text) // max_chars)
        n_by_time = -(-duration // max_cue_ms)
        pieces = _split_text(seg.text, max(n_by_chars, n_by_time))

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
