"""Rule-based analyzer over jobs.jsonl. Record shapes follow real XBOOK logs (0.8.0-0.10.0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import analysis


def job(job_id: str, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "job_id": job_id,
        "source_path": r"C:\Users\phu\Videos\market-briefing.mp4",
        "model_id": "whisper-large-v3-turbo-int8",
        "language": "auto",
        "task": "transcribe",
        "state": "completed",
        "device": {
            "requested": "auto",
            "resolved": "NPU",
            "available_devices": ["CPU", "GPU", "NPU"],
            "fallback_reason": None,
        },
        "translate_to": None,
        "openvino_version": "2026.4.0-1",
        "source_duration_ms": 600_000,
        "timings_ms": {"extract": 3_000, "load_model": 8_000, "inference": 60_000, "total": 75_000},
        "real_time_factor": 0.1,
        "cue_count": 120,
        "started_at": "2026-10-15T08:00:00+00:00",
    }
    for key, value in overrides.items():
        if key == "timings":
            record["timings_ms"] = {**record["timings_ms"], **value}
        else:
            record[key] = value
    return record


def write_log(tmp_path: Path, records: list[dict[str, Any]], extra: str = "") -> Path:
    """Records are given newest first (like the report); the log is append order."""
    path = tmp_path / "logs" / "jobs.jsonl"
    path.parent.mkdir(parents=True)
    lines = [json.dumps(r) for r in reversed(records)]
    path.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return path


def ids(report: dict[str, Any]) -> list[str]:
    return [f["id"] for f in report["findings"]]


def test_missing_log_is_no_data(tmp_path: Path) -> None:
    report = analysis.analyze(tmp_path / "nope.jsonl")
    assert report["status"] == "no_data"
    assert report["findings"] == [] and report["jobs_analyzed"] == 0
    assert report["log_found"] is False


def test_healthy_jobs_have_no_findings(tmp_path: Path) -> None:
    log = write_log(tmp_path, [job(f"j{i}") for i in range(5)])
    report = analysis.analyze(log)
    assert report["status"] == "ok" and report["findings"] == []
    assert report["stats"]["stages"]["load_model"]["median_ms"] == 8_000
    assert report["generated_at"].endswith("Z")


def test_torn_last_line_is_skipped(tmp_path: Path) -> None:
    log = write_log(tmp_path, [job("a")], extra='{"job_id": "b", "sta')
    report = analysis.analyze(log)
    assert report["jobs_analyzed"] == 1 and report["skipped_lines"] == 1


def test_repeated_slow_translation_load_is_high(tmp_path: Path) -> None:
    rows = [
        job(
            f"t{i}",
            translate_to="vi",
            translation_model="qwen3-8b-int4-cw",
            translation_device={"requested": "auto", "resolved": "NPU", "fallback_reason": None},
            timings={"load_translation_model": 410_000, "translation": 90_000},
        )
        for i in range(2)
    ]
    report = analysis.analyze(write_log(tmp_path, rows))
    finding = next(f for f in report["findings"] if f["id"] == "slow_translation_model_load")
    assert finding["severity"] == "high"
    assert finding["evidence"]["model"] == "qwen3-8b-int4-cw"
    assert "app/devices.py:compile_with_fallback" in finding["code_refs"]
    assert set(finding["job_ids"]) == {"t0", "t1"}


def test_single_slow_asr_load_is_medium(tmp_path: Path) -> None:
    rows = [job("new", timings={"load_model": 95_000}), job("a"), job("b")]
    report = analysis.analyze(write_log(tmp_path, rows))
    finding = next(f for f in report["findings"] if f["id"] == "slow_asr_model_load")
    assert finding["severity"] == "medium" and finding["job_ids"] == ["new"]


def test_rtf_regression_against_earlier_jobs(tmp_path: Path) -> None:
    rows = [job("slow", real_time_factor=0.31)] + [job(f"o{i}") for i in range(3)]
    report = analysis.analyze(write_log(tmp_path, rows))
    finding = next(f for f in report["findings"] if f["id"] == "asr_rtf_regression")
    assert finding["severity"] == "high"  # 3.1x
    assert finding["evidence"]["median_rtf"] == 0.1


def test_rtf_needs_three_comparable_jobs(tmp_path: Path) -> None:
    rows = [job("slow", real_time_factor=0.5), job("o1")]
    assert "asr_rtf_regression" not in ids(analysis.analyze(write_log(tmp_path, rows)))


def test_slow_extraction_flags_network_source(tmp_path: Path) -> None:
    rows = [
        job(
            "smb",
            source_path=r"\\192.168.1.8\smb\STAR-888.mp4",
            source_duration_ms=7_200_000,
            timings={"extract": 235_000},
        )
    ]
    report = analysis.analyze(write_log(tmp_path, rows))
    finding = next(f for f in report["findings"] if f["id"] == "slow_audio_extraction")
    entry = finding["evidence"]["jobs"][0]
    assert entry["source_kind"] == "network"
    assert entry["source"].startswith("media-") and entry["source"].endswith(".mp4")
    assert "STAR-888" not in json.dumps(report)


def test_unredacted_report_keeps_file_name(tmp_path: Path) -> None:
    rows = [job("smb", source_duration_ms=600_000, timings={"extract": 60_000})]
    report = analysis.analyze(write_log(tmp_path, rows), redact=False)
    entry = report["findings"][0]["evidence"]["jobs"][0]
    assert entry["source"] == "market-briefing.mp4"


def test_translation_rules(tmp_path: Path) -> None:
    rows = [
        job(
            "tr",
            translate_to="vi",
            translation_model="qwen3-0.6b-int4",
            overlap=True,
            translation_overlap_lines=0,
            translation_fallback_lines=30,
            cue_count=100,
            timings={"translation_after_asr": 200_000, "total": 300_000},
        )
    ]
    found = ids(analysis.analyze(write_log(tmp_path, rows)))
    assert {"translation_bottleneck", "translation_fallback_lines", "overlap_idle"} <= set(found)


def test_device_lost_and_failures(tmp_path: Path) -> None:
    rows = [
        job(
            "f1",
            state="failed",
            error_code="TRANSCRIPTION_FAILED",
            error_message=r"ZE_RESULT_ERROR_DEVICE_LOST in C:\Users\phu\x.mp4",
        ),
        job("f2", state="failed", error_code="MODEL_COMPILE_FAILED", error_message="boom"),
        job("r", asr_recoveries=[{"at_ms": 300000, "from": "NPU", "to": "NPU"}]),
    ]
    report = analysis.analyze(write_log(tmp_path, rows))
    assert ids(report)[:2] == ["failed_jobs", "npu_device_lost"]  # high first
    lost = report["findings"][1]
    assert {"f1", "r"} == set(lost["job_ids"])
    assert r"C:\Users" not in json.dumps(report)


def test_device_fallback_and_runtime_change(tmp_path: Path) -> None:
    fallback = {
        "requested": "auto",
        "resolved": "GPU",
        "fallback_reason": "NPU compile failed: out of memory",
    }
    rows = [
        job("fb", device=fallback, openvino_version="2026.4.0-1"),
        job("old", openvino_version="2025.4.1"),
    ]
    found = ids(analysis.analyze(write_log(tmp_path, rows)))
    assert "device_fallback" in found and "openvino_version_changed" in found


def test_job_ids_filter(tmp_path: Path) -> None:
    rows = [job("a", timings={"load_model": 99_000}), job("b")]
    report = analysis.analyze(write_log(tmp_path, rows), job_ids=["b"])
    assert report["jobs_analyzed"] == 1 and report["findings"] == []


def test_compare_jobs_delta() -> None:
    a = job("a")
    b = job("b", timings={"load_model": 20_000}, openvino_version="2026.5.0")
    out = analysis.compare_jobs(a, b)
    assert out["timings"]["load_model"] == {
        "a_ms": 8000.0,
        "b_ms": 20000.0,
        "delta_ms": 12000,
        "ratio": 2.5,
    }
    assert out["differences"]["openvino_version"] == {"a": "2026.4.0-1", "b": "2026.5.0"}


def test_optimization_plan_markdown(tmp_path: Path) -> None:
    rows = [job("new", timings={"load_model": 95_000}), job("a"), job("b")]
    report = analysis.analyze(write_log(tmp_path, rows), now=datetime(2026, 10, 17, tzinfo=UTC))
    text = analysis.optimization_plan(report, "vi")
    assert "slow_asr_model_load" in text and "app/whisper.py:load_model" in text
    assert "không tự merge" in text
    empty = analysis.optimization_plan(analysis.analyze(tmp_path / "x.jsonl"), "en")
    assert "No optimization opportunities" in empty


def test_redact_record_hides_paths() -> None:
    record = job("x", outputs=[r"C:\Users\phu\out\a.vi.srt"], output_path=r"C:\o\a.srt")
    out = analysis.redact_record(record)
    text = json.dumps(out)
    assert "market-briefing" not in text and "phu" not in text
    assert out["source_kind"] == "local"
    assert analysis.redact_record(record, redact=False)["source_path"] == record["source_path"]
