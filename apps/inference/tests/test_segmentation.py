from app.segmentation import MAX_CUE_CHARS, MAX_CUE_MS, TimedText, build_cues
from app.subtitles import render_srt


def test_drops_empty_and_normalizes() -> None:
    cues = build_cues([TimedText(0, 1000, "  Xin   chào "), TimedText(1000, 2000, "   ")])
    assert [(c.index, c.text) for c in cues] == [(1, "Xin chào")]


def test_long_segment_split_within_limits_and_contiguous() -> None:
    text = "And so my fellow Americans ask not what your country can do for you, " * 3
    cues = build_cues([TimedText(0, 20_000, text)])
    assert len(cues) >= 3
    assert all(len(c.text) <= MAX_CUE_CHARS for c in cues)
    assert all(c.end_ms - c.start_ms <= MAX_CUE_MS for c in cues)
    assert cues[0].start_ms == 0 and cues[-1].end_ms == 20_000
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end_ms == b.start_ms
    render_srt(cues)  # must validate


def test_repairs_overlap_missing_and_inverted_end() -> None:
    cues = build_cues(
        [
            TimedText(0, 3000, "one"),
            TimedText(2500, 4000, "two"),  # overlaps previous
            TimedText(5000, None, "three"),  # missing end
            TimedText(9000, 9000, "four"),  # zero length at end
        ],
        audio_duration_ms=12_000,
    )
    render_srt(cues)
    assert cues[1].start_ms == 3000
    assert cues[2].end_ms == 9000
    assert cues[3].end_ms > cues[3].start_ms


def test_clamps_to_audio_duration() -> None:
    cues = build_cues([TimedText(0, 30_000, "short")], audio_duration_ms=4_000)
    assert cues[-1].end_ms == 4_000
