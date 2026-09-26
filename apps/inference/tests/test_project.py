import json
from pathlib import Path

import pytest

from app.errors import ErrorCode, LocalSubError
from app.models import SubtitleCue
from app.project import SCHEMA, export_project, parse_project, project_from_cues
from app.subtitles import parse_srt, render_srt, render_txt, render_vtt

CUES = [
    SubtitleCue(1, 0, 1_500, "Hello there"),
    SubtitleCue(2, 1_500, 3_000, "Good morning"),
]


def test_vtt_and_txt_rendering() -> None:
    vtt = render_vtt(CUES)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.500\nHello there\n")
    assert render_txt(CUES) == "Hello there\nGood morning\n"


def test_parse_srt_roundtrip_with_bom_and_crlf() -> None:
    text = "\ufeff" + render_srt(CUES).replace("\n", "\r\n")
    assert parse_srt(text) == CUES


def test_export_bilingual_names_and_json(tmp_path: Path) -> None:
    project = project_from_cues(CUES, ["Xin chào", "Chào buổi sáng"], "en", "vi", "a.mp4")
    written = export_project(project, tmp_path / "talk", ["srt", "vtt", "json"])
    names = [p.name for p in written]
    assert names == [
        "talk.en.srt", "talk.vi.srt", "talk.en.vtt", "talk.vi.vtt", "talk.localsub.json"
    ]
    assert "Chào buổi sáng" in (tmp_path / "talk.vi.srt").read_text(encoding="utf-8")
    data = json.loads((tmp_path / "talk.localsub.json").read_text(encoding="utf-8"))
    assert data["schema"] == SCHEMA and data["cues"][0]["translation"] == "Xin chào"


def test_export_without_source_suffix_and_translation_only(tmp_path: Path) -> None:
    project = project_from_cues(CUES, ["a", "b"], "en", "vi")
    names = [p.name for p in export_project(project, tmp_path / "x", ["srt"], False)]
    assert names == ["x.srt", "x.vi.srt"]
    names = [p.name for p in export_project(project, tmp_path / "y", ["srt"], include_source=False)]
    assert names == ["y.vi.srt"]


def test_parse_project_roundtrip_and_validation() -> None:
    project = project_from_cues(CUES, None, "vi", None)
    assert parse_project(project.to_dict()) == project
    with pytest.raises(LocalSubError) as err:
        parse_project({"cues": [{"start_ms": 10, "end_ms": 5, "text": "x"}]})
    assert err.value.code == ErrorCode.SUBTITLE_VALIDATION_FAILED
    with pytest.raises(LocalSubError):
        parse_project({"cues": [{"end_ms": 5}]})
    with pytest.raises(LocalSubError):
        parse_project({"nope": 1})
