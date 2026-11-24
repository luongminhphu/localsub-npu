import json
import subprocess
from pathlib import Path

from app.config import Settings
from app.errors import ErrorCode
from app.jobs import JobRequest, run_job
from app.models import JobState
from tests.conftest import write_wav
from tests.fakes import FakeLLM
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


def install_llm(settings: Settings) -> None:
    path = settings.model_dir / "qwen3-0.6b-int4"
    path.mkdir(parents=True)
    for name in ("openvino_model.xml", "openvino_tokenizer.xml", "openvino_detokenizer.xml"):
        (path / name).write_text("<xml/>")
        (path / name).with_suffix(".bin").write_bytes(b"\0")


def test_job_with_translation_writes_bilingual_outputs(tmp_path, settings, installed_model):
    install_llm(settings)
    events: list[dict] = []
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="en",
            translation_model="qwen3-0.6b-int4",
            translation_device="CPU",
            formats=("srt", "vtt", "json"),
        ),
        emit=events.append,
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        llm_factory=lambda path, device, props: FakeLLM(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.device and record.device.resolved == "NPU"
    assert record.translation_device and record.translation_device.resolved == "CPU"
    names = sorted(p.name for p in record.outputs)
    assert names == sorted(
        [
            "Họp giao ban.vi.srt",
            "Họp giao ban.en.srt",
            "Họp giao ban.vi.vtt",
            "Họp giao ban.en.vtt",
            "Họp giao ban.localsub.json",
        ]
    )
    en = (settings.output_dir / "Họp giao ban.en.srt").read_text(encoding="utf-8")
    assert "[vi] Xin chào" in en  # fake LLM marker proves the translated track was written
    progress = [e for e in events if e.get("type") == "progress"]
    assert progress[-1]["done"] == progress[-1]["total"] == 2
    completed = [e for e in events if e.get("state") == JobState.COMPLETED][0]
    assert completed["project"]["cues"][0]["translation"] == "[vi] Xin chào"
    audit = (settings.output_dir / "logs" / "jobs.jsonl").read_text(encoding="utf-8")
    assert "Xin chào" not in audit
    entry = json.loads(audit.splitlines()[-1])
    assert entry["translation_parallel"] == 4  # 2 lines: one unit, tuner never leaves level 1
    assert entry["translation_tuning"] is None


def test_job_translation_model_missing_fails_early(tmp_path, settings, installed_model):
    calls: list = []
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="en",
            translation_model="qwen3-0.6b-int4",
        ),
        runtime_probe=probe_npu,
        ffmpeg_runner=lambda cmd, **kw: calls.append(cmd),
    )
    assert record.error_code == ErrorCode.MODEL_NOT_FOUND and calls == []


def test_translate_to_same_language_skips_llm(tmp_path, settings, installed_model):
    install_llm(settings)
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
        ),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        llm_factory=lambda *a: (_ for _ in ()).throw(AssertionError("LLM must not load")),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED
    assert [p.name for p in record.outputs] == ["Họp giao ban.vi.srt"]


def test_explicit_output_path_is_respected(tmp_path, settings, installed_model):
    out = tmp_path / "bench" / "clip.NPU.srt"
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="vi", output=out),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: FakePipeline(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.output_path == out.resolve()


def test_prune_stale_temp_only_removes_old_wavs(tmp_path):
    import os

    from app.jobs import STALE_TEMP_SECONDS, prune_stale_temp

    old, fresh, other = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "keep.txt"
    for f in (old, fresh, other):
        f.write_bytes(b"x")
    os.utime(old, (0, 0))
    os.utime(other, (0, 0))
    assert prune_stale_temp(tmp_path, now=STALE_TEMP_SECONDS + 10) == 1
    assert not old.exists() and fresh.exists() and other.exists()


class JapanesePipeline(FakePipeline):
    def generate(self, samples, **kwargs):
        self.kwargs = kwargs

        class Chunk:
            def __init__(self, start, end, text):
                self.start_ts, self.end_ts, self.text = start, end, text

        class R:
            chunks = [Chunk(0.0, 1.5, "こんにちは。"), Chunk(1.5, 3.0, "ありがとうございます。")]
            texts = ["こんにちは。ありがとうございます。"]
            language = "<|ja|>"

        return R()


def test_auto_detected_japanese_is_translated_to_vietnamese(tmp_path, settings, installed_model):
    install_llm(settings)
    pipeline = JapanesePipeline()
    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="auto",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            translation_device="CPU",
            formats=("srt", "json"),
        ),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: pipeline,
        llm_factory=lambda path, device, props: FakeLLM(),
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.detected_language == "ja"
    names = sorted(p.name for p in record.outputs)
    assert names == sorted(
        ["Họp giao ban.ja.srt", "Họp giao ban.vi.srt", "Họp giao ban.localsub.json"]
    )
    project = json.loads((settings.output_dir / "Họp giao ban.localsub.json").read_text("utf-8"))
    assert project["source_language"] == "ja" and project["target_language"] == "vi"
    assert project["cues"][1]["translation"] == "[vi] ありがとうございます。"


def test_explicit_japanese_language_token(tmp_path, settings, installed_model):
    pipeline = JapanesePipeline()
    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="ja"),
        runtime_probe=probe_npu,
        pipeline_factory=lambda path, device, props: pipeline,
        ffmpeg_runner=fake_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert pipeline.kwargs["language"] == "<|ja|>"
    assert record.outputs[0].name == "Họp giao ban.ja.srt"


def test_audio_extraction_runs_while_whisper_loads(tmp_path, settings: Settings, installed_model):
    import threading

    loading = threading.Event()

    def slow_ffmpeg(cmd, **kwargs):
        # Sequential code would deadlock here: the model only starts loading after FFmpeg.
        assert loading.wait(5), "Whisper did not start loading while FFmpeg ran"
        return fake_ffmpeg(cmd, **kwargs)

    def factory(path, device, props):
        loading.set()
        return FakePipeline()

    record = run_job(
        settings,
        JobRequest(source=make_source(tmp_path), language="vi"),
        runtime_probe=probe_npu,
        pipeline_factory=factory,
        ffmpeg_runner=slow_ffmpeg,
    )
    assert record.state == JobState.COMPLETED, record.error_message
    assert record.source_duration_ms == 3000 and "extract" in record.timings_ms


def test_failed_model_load_waits_for_ffmpeg_before_cleanup(
    tmp_path, settings: Settings, installed_model
):
    import threading
    import time

    done = threading.Event()

    def slow_ffmpeg(cmd, **kwargs):
        time.sleep(0.2)
        result = fake_ffmpeg(cmd, **kwargs)
        done.set()
        return result

    def broken(path, device, props):
        raise RuntimeError("compile failed")

    record = run_job(
        settings,
        JobRequest(
            source=make_source(tmp_path),
            language="vi",
        ),
        runtime_probe=probe_npu,
        pipeline_factory=broken,
        ffmpeg_runner=slow_ffmpeg,
    )
    assert record.state == JobState.FAILED
    assert done.is_set(), "FFmpeg must finish before the job cleans up"
    assert not list((settings.output_dir / ".tmp").glob("*.wav")), "temp audio must be removed"
