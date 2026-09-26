import subprocess
from pathlib import Path

import pytest

from app import audio
from app.errors import ErrorCode, LocalSubError
from tests.conftest import write_wav


def test_ffmpeg_command_is_argument_array_with_16k_mono(tmp_path: Path) -> None:
    src = tmp_path / "clip with spaces & quotes'.mp4"
    cmd = audio.build_ffmpeg_command("ffmpeg", src, tmp_path / "o.wav")
    assert cmd[0] == "ffmpeg"
    assert str(src) in cmd  # path passed as a single argument, never shell-joined
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert "-nostdin" in cmd


def test_validate_source_rejects_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    with pytest.raises(LocalSubError) as err:
        audio.validate_source(bad)
    assert err.value.code == ErrorCode.INPUT_UNSUPPORTED


def test_validate_source_missing_file(tmp_path: Path) -> None:
    with pytest.raises(LocalSubError) as err:
        audio.validate_source(tmp_path / "missing.mp4")
    assert err.value.code == ErrorCode.INPUT_NOT_FOUND


def test_resolve_ffmpeg_missing() -> None:
    with pytest.raises(LocalSubError) as err:
        audio.resolve_ffmpeg("definitely-not-ffmpeg-xyz")
    assert err.value.code == ErrorCode.FFMPEG_UNAVAILABLE


def test_extract_audio_reports_ffmpeg_failure(tmp_path: Path) -> None:
    def failing_runner(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "line1\nInvalid data found")

    with pytest.raises(LocalSubError) as err:
        audio.extract_audio("ffmpeg", tmp_path / "a.mp4", tmp_path / "o.wav", runner=failing_runner)
    assert err.value.code == ErrorCode.AUDIO_EXTRACTION_FAILED
    assert "Invalid data found" in err.value.message


def test_load_wav_samples_and_duration(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "a.wav", seconds=1.5)
    assert audio.wav_duration_ms(path) == 1500
    assert len(audio.load_wav_samples(path)) == 24_000


def test_load_wav_rejects_wrong_rate(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "a.wav", seconds=0.1, rate=44_100)
    with pytest.raises(LocalSubError):
        audio.load_wav_samples(path)
