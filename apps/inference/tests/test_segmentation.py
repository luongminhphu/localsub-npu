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


def test_japanese_segment_splits_on_characters_and_punctuation() -> None:
    from app.segmentation import MAX_CUE_CHARS_CJK, is_cjk

    text = (
        "本日は決算説明会にご参加いただきありがとうございます。"
        "まず第三四半期の業績について説明します。"
    )
    assert is_cjk(text)
    cues = build_cues([TimedText(0, 9_000, text)])
    assert len(cues) >= 2
    assert all(len(c.text) <= MAX_CUE_CHARS_CJK for c in cues)
    assert "".join(c.text for c in cues) == text  # no characters lost, no spaces added
    assert cues[0].text.endswith("。")  # prefers the sentence boundary
    assert cues[0].start_ms == 0 and cues[-1].end_ms == 9_000
    render_srt(cues)


def test_japanese_missing_end_uses_slower_reading_speed() -> None:
    from app.segmentation import MS_PER_CHAR_CJK

    cues = build_cues([TimedText(0, None, "こんにちは世界")], audio_duration_ms=60_000)
    assert cues[0].end_ms == max(1_000, MS_PER_CHAR_CJK * 7)


def test_latin_text_is_not_cjk() -> None:
    from app.segmentation import is_cjk, max_chars_for

    assert not is_cjk("Xin chào các bạn, hôm nay VN-Index tăng 12 điểm")
    assert max_chars_for("Hello there") == MAX_CUE_CHARS
    assert is_cjk("Toyota の決算は好調です")  # mixed text with a Latin name


def test_split_respects_limits_on_many_generated_texts() -> None:
    import random

    from app.segmentation import max_chars_for

    rng = random.Random(7)
    ja_words = [
        "決算",
        "は",
        "好調",
        "です",
        "。",
        "売上",
        "が",
        "増加",
        "し",
        "、",
        "利益",
        "率",
        "も",
        "改善",
    ]
    en_words = ["revenue", "grew", "strongly,", "margins", "improved.", "the", "bank", "said"]
    for _ in range(300):
        words = ja_words if rng.random() < 0.5 else en_words
        joiner = "" if words is ja_words else " "
        text = joiner.join(rng.choice(words) for _ in range(rng.randint(3, 60)))
        duration = rng.randint(1_000, 40_000)
        cues = build_cues([TimedText(0, duration, text)])
        limit = max_chars_for(text)
        assert all(len(c.text) <= limit for c in cues), text
        if len(text) * 500 >= duration:  # enough text to fill the time at a readable pace
            assert all(c.end_ms - c.start_ms <= MAX_CUE_MS for c in cues), text
        assert joiner.join(c.text for c in cues).replace(" ", "") == text.replace(" ", "")


def test_japanese_space_is_a_sentence_break() -> None:
    text = (
        "本日は決算説明会にご参加いただき誠にありがとうございます"
        " まず第3四半期の業績についてご説明します"
    )
    cues = build_cues([TimedText(0, 11_000, text)])
    assert cues[0].text == "本日は決算説明会にご参加いただき誠にありがとうございます"
    assert cues[1].text.startswith("まず")
