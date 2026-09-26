import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.audit import AuditLog, to_jsonable
from app.errors import ErrorCode
from app.models import DeviceResolution, JobState


@dataclass
class Nested:
    path: Path
    state: JobState
    device: DeviceResolution
    items: tuple[Path, ...] = ()
    meta: dict = field(default_factory=dict)


def test_to_jsonable_handles_paths_enums_and_nested_dataclasses(tmp_path: Path) -> None:
    value = Nested(
        path=tmp_path / "a.srt",
        state=JobState.COMPLETED,
        device=DeviceResolution("auto", "NPU", ("CPU", "NPU"), None),
        items=(tmp_path / "x", tmp_path / "y"),
        meta={JobState.FAILED: ErrorCode.MODEL_NOT_FOUND, "at": datetime(2026, 9, 26, tzinfo=UTC)},
    )
    result = to_jsonable(value)
    # Must round-trip through json.dumps without a custom encoder.
    text = json.dumps(result)
    assert json.loads(text) == {
        "path": str(tmp_path / "a.srt"),
        "state": "completed",
        "device": {
            "requested": "auto",
            "resolved": "NPU",
            "available_devices": ["CPU", "NPU"],
            "fallback_reason": None,
        },
        "items": [str(tmp_path / "x"), str(tmp_path / "y")],
        "meta": {"failed": "MODEL_NOT_FOUND", "at": "2026-09-26T00:00:00+00:00"},
    }


def test_audit_log_appends_json_lines(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "logs" / "jobs.jsonl")
    log.write({"state": JobState.QUEUED, "path": tmp_path})
    log.write({"state": JobState.COMPLETED})
    lines = (tmp_path / "logs" / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["state"] for line in lines] == ["queued", "completed"]
