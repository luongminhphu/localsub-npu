"""Bilingual subtitle project file shared by the worker and the desktop editor.

Schema (``localsub.project/v1``)::

    {
      "schema": "localsub.project/v1",
      "source_media": "D:/Videos/talk.mp4" | null,
      "source_language": "vi" | "en" | "ja" | null,
      "target_language": "vi" | "en" | null,
      "cues": [{"start_ms": 0, "end_ms": 1200, "text": "...", "translation": "..." | null}]
    }
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.errors import ErrorCode, LocalSubError
from app.models import SubtitleCue
from app.subtitles import render_srt, render_txt, render_vtt, validate_cues

SCHEMA = "localsub.project/v1"
FORMATS = ("srt", "vtt", "txt", "json")


@dataclass(frozen=True)
class ProjectCue:
    start_ms: int
    end_ms: int
    text: str
    translation: str | None = None


@dataclass(frozen=True)
class Project:
    cues: tuple[ProjectCue, ...]
    source_language: str | None = None
    target_language: str | None = None
    source_media: str | None = None

    def track(self, which: str) -> list[SubtitleCue]:
        cues: list[SubtitleCue] = []
        for cue in self.cues:
            text = cue.text if which == "source" else (cue.translation or cue.text)
            cues.append(SubtitleCue(len(cues) + 1, cue.start_ms, cue.end_ms, text))
        return cues

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "source_media": self.source_media,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "cues": [
                {
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "text": c.text,
                    "translation": c.translation,
                }
                for c in self.cues
            ],
        }


def project_from_cues(
    cues: Sequence[SubtitleCue],
    translations: Sequence[str] | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    source_media: str | None = None,
) -> Project:
    if translations is not None and len(translations) != len(cues):
        raise ValueError("translations must align 1:1 with cues")
    return Project(
        cues=tuple(
            ProjectCue(c.start_ms, c.end_ms, c.text, translations[i] if translations else None)
            for i, c in enumerate(cues)
        ),
        source_language=source_language,
        target_language=target_language,
        source_media=source_media,
    )


def parse_project(payload: dict[str, Any]) -> Project:
    if not isinstance(payload, dict) or not isinstance(payload.get("cues"), list):
        raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, "Project must contain 'cues'.")
    cues: list[ProjectCue] = []
    for i, raw in enumerate(payload["cues"], start=1):
        try:
            start = int(raw["start_ms"])
            end = int(raw["end_ms"])
            text = str(raw.get("text") or "")
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalSubError(
                ErrorCode.SUBTITLE_VALIDATION_FAILED, f"Cue {i} is malformed: {exc}"
            ) from exc
        translation = raw.get("translation")
        cues.append(ProjectCue(start, end, text, str(translation) if translation else None))
    project = Project(
        cues=tuple(cues),
        source_language=payload.get("source_language"),
        target_language=payload.get("target_language"),
        source_media=payload.get("source_media"),
    )
    try:
        validate_cues(project.track("source"))
    except ValueError as exc:
        raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, str(exc)) from exc
    return project


def _render(fmt: str, cues: Iterable[SubtitleCue]) -> str:
    return {"srt": render_srt, "vtt": render_vtt, "txt": render_txt}[fmt](cues)


def export_project(
    project: Project,
    output_base: Path,
    formats: Sequence[str],
    suffix_source_language: bool = True,
    include_source: bool = True,
) -> list[Path]:
    """Write ``<base>.<lang>.<fmt>`` files for the source track and, if present, translation."""
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ValueError(f"Unknown formats: {unknown}; choose from {FORMATS}")
    has_translation = any(c.translation for c in project.cues)
    tracks: list[tuple[str, str | None]] = []
    if include_source or not has_translation:
        tracks.append(("source", project.source_language))
    if has_translation:
        tracks.append(("translation", project.target_language))

    written: list[Path] = []
    output_base.parent.mkdir(parents=True, exist_ok=True)

    def target(lang: str | None, ext: str) -> Path:
        suffix = f".{lang}" if lang and lang != "auto" else ""
        return output_base.with_name(f"{output_base.name}{suffix}.{ext}")

    try:
        for fmt in formats:
            if fmt == "json":
                path = output_base.with_name(f"{output_base.name}.localsub.json")
                path.write_text(
                    json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                written.append(path)
                continue
            for track, lang in tracks:
                if track == "translation" and lang == project.source_language:
                    lang = f"{lang}-translated"
                if track == "source" and not suffix_source_language:
                    lang = None
                path = target(lang, fmt)
                path.write_text(_render(fmt, project.track(track)), encoding="utf-8")
                written.append(path)
    except ValueError as exc:
        raise LocalSubError(ErrorCode.SUBTITLE_VALIDATION_FAILED, str(exc)) from exc
    except OSError as exc:
        raise LocalSubError(ErrorCode.OUTPUT_WRITE_FAILED, str(exc)) from exc
    return written
