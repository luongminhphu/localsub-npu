import json
import subprocess
from pathlib import Path

from app.config import Settings
from app.errors import ErrorCode
from app.jobs import JobRequest, run_job
from app.models import JobState
from tests.conftest import write_wav
from tests.test_whisper import FakePipeline


def fake_ffmpeg(cmd, **_kwargs):
    write_wav(Path(cmd[-1]), seconds=3.0)
    return subprocess.CompletedProcess(cmd, 0, "", "")


def probe_npu():
    return "2026.4.0-test", ("CPU", "GPU", "NPU")


def make_source(tmp_path: Path) -> Path:
    src = tmp_path / "Họp giao ban.mp4"
    src.write_bytes(b"fake")
    return src


def test_job_end_to_end_writes_srt_and_audit(tmp_path, settings: Settings, installed_model):
    events: list[dict] = []
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="vi"),
        emit=events.append,
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.device and record.device.resolved == "NPU"
    assert record.output_path and record.output_path.name == "Họp giao ban.vi.srt"
    srt = record.output_path.read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,250\nXin chào\n")
    assert "00:00:01,250 --> 00:00:02,250" in srt  # missing end estimated from reading speed
    assert [e["state"] for e in events if "state" in e][-1] == JobState.COMPLETED
    assert not list((settings.output_dir / ".tmp").glob("*.wav")), "temp audio must be removed"

    audit = (settings.output_dir / "logs" / "jobs.jsonl").read_text(encoding="utf-8")
    entry = json.loads(audit.splitlines()[-1])
    assert entry["state"] == "completed"
    assert entry["device"]["resolved"] == "NPU"
    assert entry["source_duration_ms"] == 3000
    assert "Xin chào" not in audit, "transcript text must not be logged"


def test_job_missing_model_fails_before_extraction(tmp_path, settings: Settings):
    calls: list = []
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path)),
        runtime_probe=probe_npu,
        ffmpeg_runner=lambda cmd, **kw: calls.append(cmd),
    )
    assert record.state == JobState.FAILED
    assert record.error_code == ErrorCode.MODEL_NOT_FOUND
    assert calls == []
    entry = json.loads((settings.output_dir / "logs" / "jobs.jsonl").read_text().splitlines()[-1])
    assert entry["error_code"] == "MODEL_NOT_FOUND"


def test_translate_task_names_output_en(tmp_path, settings: Settings, installed_model):
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="vi", task="translate"),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.output_path and record.output_path.name.endswith(".en.srt")
