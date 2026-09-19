import pytest

from app.models import SubtitleCue
from app.subtitles import format_srt_timestamp, render_srt, validate_cues


def test_format_srt_timestamp() -> None:
    assert format_srt_timestamp(3_723_004) == "01:02:03,004"


def test_render_srt_normalizes_text() -> None:
    result = render_srt(
        [
            SubtitleCue(index=1, start_ms=0, end_ms=1_200, text=" Xin   chào "),
            SubtitleCue(index=2, start_ms=1_200, end_ms=2_800, text="Hello world"),
        ]
    )

    assert result == (
        "1\n00:00:00,000 --> 00:00:01,200\nXin chào\n\n"
        "2\n00:00:01,200 --> 00:00:02,800\nHello world\n"
    )


def test_validate_cues_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="overlaps"):
        validate_cues(
            [
                SubtitleCue(index=1, start_ms=0, end_ms=2_000, text="One"),
                SubtitleCue(index=2, start_ms=1_999, end_ms=3_000, text="Two"),
            ]
        )


def test_validate_cues_rejects_invalid_indexes() -> None:
    with pytest.raises(ValueError, match="sequential"):
        validate_cues([SubtitleCue(index=2, start_ms=0, end_ms=1, text="One")])
