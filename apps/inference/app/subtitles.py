from __future__ import annotations

import re
from collections.abc import Iterable

from app.models import SubtitleCue


def format_srt_timestamp(milliseconds: int) -> str:
    if milliseconds < 0:
        raise ValueError("milliseconds must be non-negative")

    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_cues(cues: Iterable[SubtitleCue]) -> list[SubtitleCue]:
    normalized: list[SubtitleCue] = []
    previous_end = -1

    for expected_index, cue in enumerate(cues, start=1):
        if cue.index != expected_index:
            raise ValueError("cue indexes must be sequential and start at 1")
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
            raise ValueError(f"cue {cue.index} has an invalid time range")
        if cue.start_ms < previous_end:
            raise ValueError(f"cue {cue.index} overlaps the preceding cue")

        text = normalize_text(cue.text)
        if not text:
            raise ValueError(f"cue {cue.index} text cannot be empty")

        normalized.append(
            SubtitleCue(
                index=cue.index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=text,
            )
        )
        previous_end = cue.end_ms

    return normalized


def render_srt(cues: Iterable[SubtitleCue]) -> str:
    lines: list[str] = []
    for cue in validate_cues(cues):
        lines.extend(
            [
                str(cue.index),
                f"{format_srt_timestamp(cue.start_ms)} --> {format_srt_timestamp(cue.end_ms)}",
                cue.text,
                "",
            ]
        )

    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def format_vtt_timestamp(milliseconds: int) -> str:
    return format_srt_timestamp(milliseconds).replace(",", ".")


def render_vtt(cues: Iterable[SubtitleCue]) -> str:
    lines = ["WEBVTT", ""]
    for cue in validate_cues(cues):
        lines.extend(
            [
                str(cue.index),
                f"{format_vtt_timestamp(cue.start_ms)} --> {format_vtt_timestamp(cue.end_ms)}",
                cue.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_txt(cues: Iterable[SubtitleCue]) -> str:
    return "".join(f"{cue.text}\n" for cue in validate_cues(cues))


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def parse_srt(content: str) -> list[SubtitleCue]:
    """Parse SRT (or VTT-style timings) into cues, re-indexing sequentially."""
    cues: list[SubtitleCue] = []
    blocks = re.split(r"\r?\n\s*\r?\n", content.lstrip("\ufeff").strip())
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                g = match.groups()
                text = normalize_text(" ".join(lines[i + 1 :]))
                if text:
                    cues.append(
                        SubtitleCue(
                            index=len(cues) + 1,
                            start_ms=_to_ms(*g[:4]),
                            end_ms=_to_ms(*g[4:]),
                            text=text,
                        )
                    )
                break
    return cues
