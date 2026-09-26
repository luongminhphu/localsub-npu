"""Real OpenVINO Whisper run. Skipped unless LOCALSUB_IT_MEDIA and a model are provided.

    LOCALSUB_IT_MEDIA=C:\\path\\clip.mp4 LOCALSUB_IT_MODEL=whisper-base-int8 pytest -k integration
"""

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.jobs import JobRequest, run_job
from app.models import JobState

MEDIA = os.getenv("LOCALSUB_IT_MEDIA")


@pytest.mark.skipif(not MEDIA, reason="set LOCALSUB_IT_MEDIA to run the hardware integration test")
def test_integration_real_whisper(tmp_path: Path) -> None:
    pytest.importorskip("openvino_genai")
    base = Settings.from_env()
    settings = Settings(
        device_preference=base.device_preference,
        ffmpeg_path=base.ffmpeg_path,
        output_dir=tmp_path,
        model_dir=base.model_dir,
        log_level="INFO",
    )
    record = run_job(
        settings,
        JobRequest(
            source=Path(MEDIA or ""),
            model_id=os.getenv("LOCALSUB_IT_MODEL", "whisper-base-int8"),
        ),
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.cue_count and record.cue_count > 0
    print(record.device, record.timings_ms, record.real_time_factor)


LLM = os.getenv("LOCALSUB_IT_LLM")


@pytest.mark.skipif(not LLM, reason="set LOCALSUB_IT_LLM=<model id> to run the LLM test")
def test_integration_real_translation() -> None:
    pytest.importorskip("openvino_genai")
    import openvino as ov

    from app import translation

    settings = Settings.from_env()
    model_id = LLM or ""
    path = translation.ensure_model_present(
        translation.model_path_for(settings.model_dir, model_id), model_id
    )
    translator = translation.load_translator(
        path, model_id, settings.device_preference, settings.preferred_devices(),
        tuple(ov.Core().available_devices), cache_dir=settings.model_dir / ".ov-cache",
    )
    lines = ["Good morning, everyone.", "The market opened higher today."]
    result = translation.translate_lines(translator, lines, "en", "vi")
    assert len(result.lines) == 2 and all(result.lines)
    print(translator.device, result.lines, result.inference_ms)
