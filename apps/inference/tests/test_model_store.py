from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import history, model_store
from app.devices import MARKER_FILE, ensure_ir_present, ir_status
from app.errors import ErrorCode, LocalSubError
from app.whisper import REQUIRED_FILES

MODEL = "whisper-tiny-int8"


def _write_ir(path: Path, with_bin: bool = True) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        (path / name).write_text("<xml/>")
        if with_bin:
            (path / name).with_suffix(".bin").write_bytes(b"\0" * 10)


def _fake_snapshot(with_bin: bool = True, orphan: bool = False):
    def snap(repo: str, target: Path) -> None:
        _write_ir(target, with_bin)
        if orphan:
            cache = target / ".cache" / "huggingface" / "download"
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "x.incomplete").write_bytes(b"\0")

    return snap


def test_status_missing_incomplete_installed(tmp_path):
    path = tmp_path / MODEL
    assert ir_status(path, REQUIRED_FILES) == "missing"
    _write_ir(path, with_bin=False)
    assert ir_status(path, REQUIRED_FILES) == "incomplete"
    _write_ir(path)
    assert ir_status(path, REQUIRED_FILES) == "installed"  # legacy folder, no partial files


def test_legacy_folder_with_partial_file_is_incomplete(tmp_path):
    path = tmp_path / MODEL
    _write_ir(path)
    cache = path / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "a.incomplete").write_bytes(b"\0")
    assert ir_status(path, REQUIRED_FILES) == "incomplete"
    with pytest.raises(LocalSubError) as err:
        ensure_ir_present(path, REQUIRED_FILES, MODEL, "models download")
    assert err.value.code == ErrorCode.MODEL_INCOMPLETE


def test_download_emits_progress_writes_marker_and_cleans_orphans(tmp_path):
    events: list[dict] = []
    result = model_store.download(
        tmp_path,
        MODEL,
        emit=events.append,
        snapshot=_fake_snapshot(orphan=True),
        repo_size=lambda repo: 60,
        poll_seconds=0.01,
        disk_free=lambda p: 10**9,
    )
    assert result.status == "installed" and not result.already_installed
    assert (tmp_path / MODEL / MARKER_FILE).is_file()
    assert not list((tmp_path / MODEL).rglob("*.incomplete"))
    assert events[0]["state"] == "downloading" and events[-1]["state"] == "completed"
    audit = (tmp_path / "logs" / "models.jsonl").read_text().splitlines()
    assert json.loads(audit[-1])["state"] == "installed"


def test_download_skips_when_installed(tmp_path):
    _write_ir(tmp_path / MODEL)

    def boom(repo, target):
        raise AssertionError("must not download")

    result = model_store.download(tmp_path, MODEL, snapshot=boom)
    assert result.already_installed


def test_download_incomplete_raises(tmp_path):
    with pytest.raises(LocalSubError) as err:
        model_store.download(
            tmp_path,
            MODEL,
            snapshot=_fake_snapshot(with_bin=False),
            repo_size=lambda r: None,
            disk_free=lambda p: 10**9,
        )
    assert err.value.code == ErrorCode.MODEL_INCOMPLETE
    assert not (tmp_path / MODEL / MARKER_FILE).exists()


def test_network_error_maps_to_download_failed(tmp_path):
    def fail(repo, target):
        raise ConnectionError("reset by peer")

    with pytest.raises(LocalSubError) as err:
        model_store.download(
            tmp_path, MODEL, snapshot=fail, repo_size=lambda r: 1, disk_free=lambda p: 10**9
        )
    assert err.value.code == ErrorCode.DOWNLOAD_FAILED


def test_insufficient_disk(tmp_path):
    with pytest.raises(LocalSubError) as err:
        model_store.download(
            tmp_path,
            MODEL,
            snapshot=_fake_snapshot(),
            repo_size=lambda r: 10**9,
            disk_free=lambda p: 10,
        )
    assert err.value.code == ErrorCode.INSUFFICIENT_DISK


def test_unknown_model_rejected_before_filesystem(tmp_path):
    for bad in ("../etc", "nope"):
        with pytest.raises(LocalSubError):
            model_store.remove(tmp_path, bad)


def test_remove_deletes_folder_and_audits(tmp_path):
    _write_ir(tmp_path / MODEL)
    out = model_store.remove(tmp_path, MODEL)
    assert out["removed"] and out["freed_bytes"] > 0
    assert not (tmp_path / MODEL).exists()
    assert model_store.remove(tmp_path, MODEL)["removed"] is False


def test_list_models_reports_status(tmp_path):
    _write_ir(tmp_path / MODEL)
    rows = {r["id"]: r for r in model_store.list_models(tmp_path)}
    assert rows[MODEL]["status"] == "installed" and rows[MODEL]["size_bytes"] > 0
    assert rows["whisper-base-int8"]["status"] == "missing"
    assert model_store.installed_models(tmp_path) == [MODEL]


def test_history_newest_first_skips_torn_lines_and_hides_paths(tmp_path):
    log = tmp_path / "jobs.jsonl"
    rows = [
        {
            "job_id": "a",
            "state": "completed",
            "source_path": "C:\\Videos\\one.mp4",
            "device": {"requested": "auto", "resolved": "NPU"},
            "real_time_factor": 0.2,
            "timings_ms": {"total": 1000},
        },
        {"job_id": "b", "state": "failed", "source_path": "/x/two.mkv", "error_code": "X"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + '\n{"job_id": "c", "sta')
    out = history.recent_jobs(log, limit=10)
    assert [j["job_id"] for j in out["jobs"]] == ["b", "a"]
    assert out["jobs"][1]["source_name"] == "one.mp4" and out["jobs"][1]["total_ms"] == 1000
    assert out["skipped_lines"] == 1
    assert out["stats"] == {
        "total": 2,
        "completed": 1,
        "failed": 1,
        "npu_share": 1.0,
        "fallbacks": 0,
        "median_rtf": 0.2,
    }
    assert "text" not in json.dumps(out["jobs"])


def test_history_missing_log_and_limit_clamp(tmp_path):
    assert history.recent_jobs(tmp_path / "none.jsonl")["jobs"] == []
    log = tmp_path / "j.jsonl"
    log.write_text(
        "\n".join(json.dumps({"job_id": str(i), "state": "completed"}) for i in range(5))
    )
    assert len(history.recent_jobs(log, limit=0)["jobs"]) == 1
    assert history.recent_jobs(log, limit=2)["jobs"][0]["job_id"] == "4"
