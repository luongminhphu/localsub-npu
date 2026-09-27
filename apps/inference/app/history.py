"""Read recent job records from the local audit log for the desktop monitoring panel.

Only metadata is returned (file name, devices, timings, error code); the audit log never
contains transcript text, and full source paths are reduced to the file name.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path, PureWindowsPath
from typing import Any

MAX_LIMIT = 200


def _device(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "requested": value.get("requested"),
        "resolved": value.get("resolved"),
        "fallback_reason": value.get("fallback_reason"),
    }


def _name(path: Any) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return PureWindowsPath(path).name  # handles both C:\x\y.mp4 and /x/y.mp4


def summarize(record: dict[str, Any]) -> dict[str, Any]:
    timings = record.get("timings_ms") if isinstance(record.get("timings_ms"), dict) else {}
    return {
        "job_id": record.get("job_id"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "state": record.get("state"),
        "source_name": _name(record.get("source_path")),
        "language": record.get("language"),
        "detected_language": record.get("detected_language"),
        "translate_to": record.get("translate_to"),
        "model_id": record.get("model_id"),
        "translation_model": record.get("translation_model"),
        "device": _device(record.get("device")),
        "translation_device": _device(record.get("translation_device")),
        "source_duration_ms": record.get("source_duration_ms"),
        "total_ms": timings.get("total"),
        "real_time_factor": record.get("real_time_factor"),
        "total_rtf": _ratio(timings.get("total"), record.get("source_duration_ms")),
        "asr_ms": timings.get("inference"),
        "translation_ms": timings.get("translation"),
        "cue_count": record.get("cue_count"),
        "translation_fallback_lines": record.get("translation_fallback_lines"),
        "error_code": record.get("error_code"),
    }


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if not isinstance(numerator, int | float) or not isinstance(denominator, int | float):
        return None
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def recent_jobs(log_path: Path, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_LIMIT))
    if not log_path.is_file():
        return {"log_path": log_path, "jobs": [], "skipped_lines": 0, "stats": _stats([])}
    tail: deque[str] = deque(maxlen=limit)
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                tail.append(line)
    jobs: list[dict[str, Any]] = []
    skipped = 0
    for line in tail:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1  # a torn last line (worker killed mid-write) must not hide history
            continue
        if isinstance(record, dict):
            jobs.append(summarize(record))
        else:
            skipped += 1
    jobs.reverse()  # newest first
    return {"log_path": log_path, "jobs": jobs, "skipped_lines": skipped, "stats": _stats(jobs)}


def _stats(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    done = [j for j in jobs if j["state"] == "completed"]
    failed = [j for j in jobs if j["state"] == "failed"]
    # Total RTF (whole job) is what the user waits for; ASR-only RTF hid translation time.
    rtfs = [j["total_rtf"] for j in done if isinstance(j["total_rtf"], int | float)]
    npu = [j for j in done if (j["device"] or {}).get("resolved") == "NPU"]
    fallbacks = [j for j in jobs if (j["device"] or {}).get("fallback_reason")]
    return {
        "total": len(jobs),
        "completed": len(done),
        "failed": len(failed),
        "npu_share": round(len(npu) / len(done), 3) if done else None,
        "fallbacks": len(fallbacks),
        "median_rtf": _median(rtfs),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    value = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return round(value, 4)
