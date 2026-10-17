"""Rule-based performance analysis over the local job audit log (``jobs.jsonl``).

The same report feeds the desktop "Phân tích (AI)" tab, the ``analyze`` CLI command and the
MCP server, so an AI assistant sees exactly what the user sees. Each finding carries the
evidence (job ids, measured values), a recommendation and ``code_refs`` pointing at the
functions a coding agent should look at first.

Privacy: the audit log never holds transcript text. With ``redact=True`` (the MCP default)
media/output paths become ``media-<hash>.<ext>`` and the user's home folder becomes ``~``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

# Thresholds (milliseconds unless stated). Chosen from real XBOOK logs: cached NPU compiles of
# whisper-large-v3-turbo take ~5-15 s; an uncached Qwen3 8B NPU compile took 410 s.
SLOW_ASR_LOAD_MS = 30_000
SLOW_TRANSLATION_LOAD_MS = 60_000
LOAD_OUTLIER_FACTOR = 3.0  # vs the median load of the same model/device/runtime
RTF_REGRESSION_FACTOR = 1.5  # newest ASR RTF vs median of earlier comparable jobs
SLOW_EXTRACT_SHARE = 0.02  # extraction longer than 2% of media duration
SLOW_EXTRACT_MIN_MS = 20_000
TRANSLATION_TAIL_SHARE = 0.5  # translation after ASR > 50% of total job time
FALLBACK_LINE_SHARE = 0.10
FAILED_SHARE = 0.2

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s\"'<>|]+")


# --------------------------------------------------------------------------- reading


def read_records(log_path: Path, limit: int = DEFAULT_LIMIT) -> tuple[list[dict[str, Any]], int]:
    """Newest-first raw job records from the last ``limit`` lines, plus skipped (torn) lines."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    if not log_path.is_file():
        return [], 0
    tail: deque[str] = deque(maxlen=limit)
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                tail.append(line)
    records: list[dict[str, Any]] = []
    skipped = 0
    for line in tail:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(record, dict) and record.get("job_id"):
            records.append(record)
        else:
            skipped += 1
    records.reverse()
    return records, skipped


# --------------------------------------------------------------------------- redaction


def _hash_name(path: str) -> str:
    name = PureWindowsPath(path).name
    suffix = PureWindowsPath(name).suffix.lower()
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    return f"media-{digest}{suffix}"


def source_kind(path: Any) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    if path.startswith(("\\\\", "//")):
        return "network"
    return "local"


def scrub_text(text: str, redact: bool = True) -> str:
    """Hide absolute paths inside free text (error messages, fallback reasons)."""
    if not redact or not text:
        return text
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        text = text.replace(home, "~")
    return _PATH_RE.sub(lambda m: _hash_name(m.group(0)), text)


def display_name(path: Any, redact: bool) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return _hash_name(path) if redact else PureWindowsPath(path).name


def redact_record(record: dict[str, Any], redact: bool = True) -> dict[str, Any]:
    """Copy of a raw record that is safe to hand to an AI assistant."""
    out = dict(record)
    out["source_kind"] = source_kind(record.get("source_path"))
    if not redact:
        return out
    for key in ("source_path", "output_path"):
        if isinstance(out.get(key), str):
            out[key] = _hash_name(out[key])
    if isinstance(out.get("outputs"), list):
        out["outputs"] = [_hash_name(p) if isinstance(p, str) else p for p in out["outputs"]]
    if isinstance(out.get("error_message"), str):
        out["error_message"] = scrub_text(out["error_message"])
    for key in ("device", "translation_device"):
        dev = out.get(key)
        if isinstance(dev, dict) and isinstance(dev.get("fallback_reason"), str):
            out[key] = {**dev, "fallback_reason": scrub_text(dev["fallback_reason"])}
    return out


# --------------------------------------------------------------------------- helpers


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _timing(record: dict[str, Any], key: str) -> float | None:
    timings = record.get("timings_ms")
    return _num(timings.get(key)) if isinstance(timings, dict) else None


def _resolved(record: dict[str, Any], key: str = "device") -> str | None:
    dev = record.get(key)
    return dev.get("resolved") if isinstance(dev, dict) else None


def median(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _seconds(ms: float | None) -> str:
    return "?" if ms is None else f"{ms / 1000:.1f}s"


def _finding(
    code: str,
    severity: str,
    category: str,
    title: tuple[str, str],
    detail: tuple[str, str],
    recommendation: tuple[str, str],
    job_ids: list[str],
    evidence: dict[str, Any],
    code_refs: list[str],
) -> dict[str, Any]:
    return {
        "id": code,
        "severity": severity,
        "category": category,
        "title": {"vi": title[0], "en": title[1]},
        "detail": {"vi": detail[0], "en": detail[1]},
        "recommendation": {"vi": recommendation[0], "en": recommendation[1]},
        "job_ids": job_ids[:10],
        "evidence": evidence,
        "code_refs": code_refs,
    }


# --------------------------------------------------------------------------- rules


def _rule_slow_load(
    records: list[dict[str, Any]],
    timing_key: str,
    model_key: str,
    device_key: str,
    threshold: float,
    code: str,
    label: tuple[str, str],
    refs: list[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, Any, Any], list[tuple[str, float]]] = defaultdict(list)
    for r in records:
        ms = _timing(r, timing_key)
        if ms is not None:
            key = (r.get(model_key), _resolved(r, device_key), r.get("openvino_version"))
            groups[key].append((r["job_id"], ms))
    findings = []
    for (model, device, version), items in groups.items():
        slow = [(j, ms) for j, ms in items if ms >= threshold]
        med = median(ms for _, ms in items) or 0
        outliers = [(j, ms) for j, ms in items if med and ms >= LOAD_OUTLIER_FACTOR * med]
        if not slow and not (outliers and len(items) >= 3):
            continue
        repeated = len(slow) >= 2
        worst = max((ms for _, ms in slow or outliers), default=0)
        severity = "high" if repeated else "medium"
        vi_label, en_label = label
        if repeated:
            detail = (
                f"{len(slow)}/{len(items)} lần nạp {vi_label} {model} trên {device} "
                f"(OpenVINO {version}) chậm hơn {_seconds(threshold)}, lâu nhất {_seconds(worst)}. "
                "Nạp chậm lặp lại nghĩa là bộ đệm biên dịch (.ov-cache) không được dùng lại.",
                f"{len(slow)}/{len(items)} loads of {en_label} {model} on {device} "
                f"(OpenVINO {version}) exceeded {_seconds(threshold)}, worst {_seconds(worst)}. "
                "Repeated slow loads mean the compile cache (.ov-cache) is not being reused.",
            )
        else:
            detail = (
                f"Nạp {vi_label} {model} trên {device} mất {_seconds(worst)} "
                f"(trung vị {_seconds(med)}). "
                "Lần đầu sau khi cài hoặc đổi OpenVINO là bình thường.",
                f"Loading {en_label} {model} on {device} took {_seconds(worst)} "
                f"(median {_seconds(med)}). Expected once after install or an OpenVINO change.",
            )
        findings.append(
            _finding(
                code,
                severity,
                "load",
                (f"Nạp {vi_label} chậm", f"Slow {en_label} load"),
                detail,
                (
                    "Kiểm tra CACHE_DIR có được truyền khi compile và thư mục .ov-cache "
                    "có ghi được; "
                    "cân nhắc nạp model dịch song song với bước tách audio/nhận dạng.",
                    "Check CACHE_DIR is passed at compile time and .ov-cache is writable; consider "
                    "loading the translator in parallel with audio extraction/ASR.",
                ),
                [j for j, _ in (slow or outliers)],
                {
                    "model": model,
                    "device": device,
                    "openvino_version": version,
                    "loads_ms": [int(ms) for _, ms in items[:10]],
                    "median_ms": int(med),
                    "threshold_ms": int(threshold),
                },
                refs,
            )
        )
    return findings


def _rule_rtf_regression(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, Any], list[tuple[str, float, Any]]] = defaultdict(list)
    for r in records:  # newest first
        rtf = _num(r.get("real_time_factor"))
        if r.get("state") == "completed" and rtf is not None:
            groups[(r.get("model_id"), _resolved(r))].append(
                (r["job_id"], rtf, r.get("openvino_version"))
            )
    findings = []
    for (model, device), items in groups.items():
        if len(items) < 3:
            continue
        newest_id, newest, version = items[0]
        base = median(rtf for _, rtf, _ in items[1:])
        if not base or newest < RTF_REGRESSION_FACTOR * base:
            continue
        factor = round(newest / base, 2)
        findings.append(
            _finding(
                "asr_rtf_regression",
                "high" if factor >= 2 else "medium",
                "asr",
                ("Nhận dạng chậm hơn trước", "Speech recognition slower than before"),
                (
                    f"Job mới nhất với {model} trên {device} có RTF {newest:.3f}, gấp {factor}× "
                    f"trung vị {base:.3f} của {len(items) - 1} job trước.",
                    f"Newest {model} job on {device} has RTF {newest:.3f}, {factor}x the median "
                    f"{base:.3f} of the {len(items) - 1} earlier jobs.",
                ),
                (
                    "So sánh với job nhanh nhất bằng localsub_compare_jobs (phiên bản OpenVINO, "
                    "chế độ nguồn, chunk, số lần phục hồi thiết bị).",
                    "Compare with the fastest job via localsub_compare_jobs (OpenVINO version, "
                    "power mode, chunking, device recoveries).",
                ),
                [newest_id],
                {
                    "model": model,
                    "device": device,
                    "newest_rtf": newest,
                    "median_rtf": round(base, 4),
                    "openvino_version": version,
                    "history": [{"job_id": j, "rtf": v, "openvino": o} for j, v, o in items[:10]],
                },
                ["app/whisper.py:transcribe", "app/whisper.py:split_points", "app/power.py"],
            )
        )
    return findings


def _rule_slow_extract(records: list[dict[str, Any]], redact: bool) -> list[dict[str, Any]]:
    slow = []
    for r in records:
        ms = _timing(r, "extract")
        duration = _num(r.get("source_duration_ms"))
        if ms is None or not duration:
            continue
        if ms >= SLOW_EXTRACT_MIN_MS and ms / duration >= SLOW_EXTRACT_SHARE:
            slow.append((r, ms, duration))
    if not slow:
        return []
    network = sum(1 for r, _, _ in slow if source_kind(r.get("source_path")) == "network")
    worst = max(ms for _, ms, _ in slow)
    return [
        _finding(
            "slow_audio_extraction",
            "medium",
            "io",
            ("Tách audio chậm", "Slow audio extraction"),
            (
                f"{len(slow)} job tách audio mất hơn {SLOW_EXTRACT_SHARE:.0%} thời lượng video "
                f"(lâu nhất {_seconds(worst)}); {network} job đọc từ đường dẫn mạng (UNC). "
                "Ổ mạng ánh xạ (ví dụ Z:) cũng chậm như vậy nhưng không phân biệt được trong log.",
                f"{len(slow)} jobs spent over {SLOW_EXTRACT_SHARE:.0%} of the media duration "
                f"extracting audio (worst {_seconds(worst)}); {network} read from a network (UNC) "
                "path. Mapped network drives (e.g. Z:) look local in the log.",
            ),
            (
                "Chép video về ổ SSD cục bộ trước khi xử lý; trong mã có thể thêm "
                "-map 0:a:0 để ffmpeg "
                "chỉ giải mã một luồng audio.",
                "Copy media to a local SSD first; in code, adding -map 0:a:0 makes ffmpeg decode "
                "only one audio stream.",
            ),
            [r["job_id"] for r, _, _ in slow],
            {
                "jobs": [
                    {
                        "job_id": r["job_id"],
                        "source": display_name(r.get("source_path"), redact),
                        "source_kind": source_kind(r.get("source_path")),
                        "extract_ms": int(ms),
                        "duration_ms": int(d),
                    }
                    for r, ms, d in slow[:10]
                ]
            },
            [
                "app/audio.py:build_ffmpeg_command",
                "app/audio.py:extract_audio",
                "app/jobs.py:_run_job",
            ],
        )
    ]


def _rule_translation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    tail, fallback, idle = [], [], []
    for r in records:
        if r.get("state") != "completed" or not r.get("translate_to"):
            continue
        total = _timing(r, "total")
        after = _timing(r, "translation_after_asr")
        if after is None:
            after = _timing(r, "translation")
        if total and after is not None and after / total >= TRANSLATION_TAIL_SHARE:
            tail.append((r, after, total))
        cues = _num(r.get("cue_count")) or 0
        lines = _num(r.get("translation_fallback_lines")) or 0
        if cues and lines / cues >= FALLBACK_LINE_SHARE:
            fallback.append((r, int(lines), int(cues)))
        if r.get("overlap") is True and not r.get("translation_overlap_lines") and cues:
            idle.append(r)
    if tail:
        findings.append(
            _finding(
                "translation_bottleneck",
                "medium",
                "translation",
                ("Dịch chiếm phần lớn thời gian", "Translation dominates job time"),
                (
                    f"{len(tail)} job có phần dịch sau khi nhận dạng xong chiếm hơn "
                    f"{TRANSLATION_TAIL_SHARE:.0%} tổng thời gian.",
                    f"{len(tail)} jobs spent over {TRANSLATION_TAIL_SHARE:.0%} of total time "
                    "translating after speech recognition finished.",
                ),
                (
                    "Dùng model dịch nhỏ hơn hoặc thiết bị khác (GPU), kiểm tra translation_tuning "
                    "để chọn số batch song song, và bảo đảm dịch chồng (overlap) thực sự chạy.",
                    "Use a smaller translator or another device (GPU), check translation_tuning "
                    "for the parallel batch size and make sure overlap translation really runs.",
                ),
                [r["job_id"] for r, _, _ in tail],
                {
                    "jobs": [
                        {
                            "job_id": r["job_id"],
                            "translation_model": r.get("translation_model"),
                            "translation_device": _resolved(r, "translation_device"),
                            "translation_tail_ms": int(a),
                            "total_ms": int(t),
                            "translation_parallel": r.get("translation_parallel"),
                            "translation_tuning": r.get("translation_tuning"),
                        }
                        for r, a, t in tail[:10]
                    ]
                },
                [
                    "app/translation.py:translate_lines",
                    "app/translation.py:ParallelTuner",
                    "app/jobs.py:OverlapTranslation",
                ],
            )
        )
    if fallback:
        findings.append(
            _finding(
                "translation_fallback_lines",
                "medium",
                "translation",
                ("Nhiều dòng không dịch được", "Many lines left untranslated"),
                (
                    f"{len(fallback)} job có hơn {FALLBACK_LINE_SHARE:.0%} số dòng phải giữ "
                    "nguyên bản gốc vì model dịch trả về sai định dạng hoặc rỗng.",
                    f"{len(fallback)} jobs kept over {FALLBACK_LINE_SHARE:.0%} of lines in the "
                    "source language because the translator returned malformed or empty output.",
                ),
                (
                    "Dùng model dịch lớn hơn (Qwen3 4B/8B) cho tiếng Nhật/Hàn; xem prompt và bộ "
                    "phân tích kết quả khi số dòng trả về lệch.",
                    "Use a larger translator (Qwen3 4B/8B) for Japanese/Korean; review the prompt "
                    "and the response parser when line counts mismatch.",
                ),
                [r["job_id"] for r, _, _ in fallback],
                {
                    "jobs": [
                        {
                            "job_id": r["job_id"],
                            "fallback_lines": n,
                            "cue_count": c,
                            "language": r.get("detected_language") or r.get("language"),
                            "translation_model": r.get("translation_model"),
                        }
                        for r, n, c in fallback[:10]
                    ]
                },
                [
                    "app/translation.py:build_messages",
                    "app/translation.py:parse_numbered",
                    "app/translation.py:plausible",
                ],
            )
        )
    if idle:
        findings.append(
            _finding(
                "overlap_idle",
                "low",
                "translation",
                ("Dịch chồng không có tác dụng", "Overlap translation did nothing"),
                (
                    f"{len(idle)} job bật dịch chồng nhưng không dịch được dòng nào trong lúc "
                    "nhận dạng; toàn bộ phần dịch dồn về cuối.",
                    f"{len(idle)} jobs enabled overlap translation but translated no lines while "
                    "ASR was running; all translation happened at the end.",
                ),
                (
                    "Kiểm tra stable_lines có trả về dòng sau mỗi chunk và model dịch nạp xong "
                    "trước khi nhận dạng kết thúc.",
                    "Check that stable_lines yields lines after each chunk and the translator "
                    "finishes loading before ASR ends.",
                ),
                [r["job_id"] for r in idle],
                {
                    "jobs": [
                        {
                            "job_id": r["job_id"],
                            "load_translation_model_ms": _timing(r, "load_translation_model"),
                            "inference_ms": _timing(r, "inference"),
                            "overlap_reason": r.get("overlap_reason"),
                        }
                        for r in idle[:10]
                    ]
                },
                ["app/jobs.py:OverlapTranslation", "app/jobs.py:stable_lines"],
            )
        )
    return findings


def _rule_device_fallback(records: list[dict[str, Any]], redact: bool) -> list[dict[str, Any]]:
    hits = []
    for r in records:
        for key in ("device", "translation_device"):
            dev = r.get(key)
            if isinstance(dev, dict) and dev.get("fallback_reason"):
                hits.append((r, key, dev))
    if not hits:
        return []
    return [
        _finding(
            "device_fallback",
            "medium",
            "load",
            ("Không chạy được trên thiết bị ưu tiên", "Preferred device not used"),
            (
                f"{len(hits)} lần phải chuyển thiết bị (ví dụ NPU → GPU/CPU) khi nạp model.",
                f"{len(hits)} model loads fell back to another device (e.g. NPU -> GPU/CPU).",
            ),
            (
                "Đọc fallback_reason: nếu là lỗi compile NPU thì kiểm tra driver NPU, giới hạn "
                "MAX_PROMPT_LEN và layout model (INT4-CW cho NPU).",
                "Read fallback_reason: for NPU compile errors check the NPU driver, the "
                "MAX_PROMPT_LEN limit and the model layout (INT4-CW for NPU).",
            ),
            [r["job_id"] for r, _, _ in hits],
            {
                "fallbacks": [
                    {
                        "job_id": r["job_id"],
                        "stage": "asr" if key == "device" else "translation",
                        "requested": dev.get("requested"),
                        "resolved": dev.get("resolved"),
                        "reason": scrub_text(str(dev.get("fallback_reason")), redact),
                    }
                    for r, key, dev in hits[:10]
                ]
            },
            ["app/devices.py:compile_with_fallback", "app/translation.py:npu_properties"],
        )
    ]


def _rule_device_lost(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for r in records:
        recoveries = r.get("asr_recoveries") or []
        lost_error = "DEVICE_LOST" in str(r.get("error_message") or "")
        if recoveries or lost_error:
            hits.append((r, len(recoveries), lost_error))
    if not hits:
        return []
    return [
        _finding(
            "npu_device_lost",
            "high",
            "stability",
            ("Thiết bị NPU/GPU bị mất kết nối", "NPU/GPU device lost"),
            (
                f"{len(hits)} job gặp ZE_RESULT_ERROR_DEVICE_LOST "
                f"({sum(n for _, n, _ in hits)} lần phục hồi, "
                f"{sum(1 for *_, e in hits if e)} job thất bại).",
                f"{len(hits)} jobs hit ZE_RESULT_ERROR_DEVICE_LOST "
                f"({sum(n for _, n, _ in hits)} recoveries, "
                f"{sum(1 for *_, e in hits if e)} failed).",
            ),
            (
                "Cập nhật driver NPU (lỗi đã biết với 32.0.100.5540, openvinotoolkit/openvino"
                "#38403); giữ máy cắm sạc và chế độ hiệu năng; xem asr_recoveries để biết chunk.",
                "Update the NPU driver (known issue with 32.0.100.5540, openvinotoolkit/openvino"
                "#38403); keep the laptop plugged in; asr_recoveries shows the failing chunk.",
            ),
            [r["job_id"] for r, _, _ in hits],
            {
                "jobs": [
                    {
                        "job_id": r["job_id"],
                        "state": r.get("state"),
                        "recoveries": r.get("asr_recoveries") or [],
                        "error_code": r.get("error_code"),
                    }
                    for r, _, _ in hits[:10]
                ]
            },
            ["app/whisper.py:is_device_lost", "app/jobs.py:_recover_asr"],
        )
    ]


def _rule_failures(records: list[dict[str, Any]], redact: bool) -> list[dict[str, Any]]:
    failed = [r for r in records if r.get("state") == "failed"]
    if len(failed) < 2 or len(failed) / len(records) < FAILED_SHARE:
        return []
    codes = Counter(str(r.get("error_code") or "UNKNOWN") for r in failed)
    return [
        _finding(
            "failed_jobs",
            "high",
            "stability",
            ("Tỷ lệ job lỗi cao", "High job failure rate"),
            (
                f"{len(failed)}/{len(records)} job thất bại; mã lỗi: "
                + ", ".join(f"{c} ×{n}" for c, n in codes.most_common()),
                f"{len(failed)}/{len(records)} jobs failed; error codes: "
                + ", ".join(f"{c} x{n}" for c, n in codes.most_common()),
            ),
            (
                "Xem error_message của từng job bằng localsub_get_job và xử lý mã lỗi "
                "phổ biến nhất "
                "trước.",
                "Inspect each job with localsub_get_job and fix the most common error code first.",
            ),
            [r["job_id"] for r in failed],
            {
                "error_codes": dict(codes),
                "examples": [
                    {
                        "job_id": r["job_id"],
                        "error_code": r.get("error_code"),
                        "error_message": scrub_text(str(r.get("error_message") or ""), redact)[
                            :300
                        ],
                    }
                    for r in failed[:5]
                ],
            },
            ["app/jobs.py:_run_job", "app/errors.py"],
        )
    ]


def _rule_power(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = [
        r
        for r in records
        if isinstance(r.get("power"), dict) and (r["power"].get("reason") or "").strip()
    ]
    if not hits:
        return []
    return [
        _finding(
            "power_not_applied",
            "low",
            "power",
            ("Không giữ được máy thức", "Keep-awake not applied"),
            (
                f"{len(hits)} job không áp dụng được chế độ giữ máy thức.",
                f"{len(hits)} jobs could not apply keep-awake.",
            ),
            (
                "Xem power.reason; Windows có thể chặn SetThreadExecutionState trong một số "
                "chính sách nguồn.",
                "Check power.reason; some Windows power policies block SetThreadExecutionState.",
            ),
            [r["job_id"] for r in hits],
            {"reasons": sorted({str(r["power"]["reason"]) for r in hits})[:5]},
            ["app/power.py:keep_awake"],
        )
    ]


def _rule_runtime_change(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_version: dict[str, list[float]] = defaultdict(list)
    for r in records:
        version = r.get("openvino_version")
        rtf = _num(r.get("real_time_factor"))
        if version and r.get("state") == "completed" and rtf is not None:
            by_version[str(version)].append(rtf)
        elif version:
            by_version.setdefault(str(version), [])
    if len(by_version) < 2:
        return []
    summary = {v: {"jobs": len(x), "median_rtf": median(x)} for v, x in by_version.items()}
    return [
        _finding(
            "openvino_version_changed",
            "info",
            "runtime",
            ("Phiên bản OpenVINO thay đổi", "OpenVINO version changed"),
            (
                "Log có nhiều phiên bản OpenVINO; lần nạp đầu sau khi đổi sẽ biên dịch lại.",
                "The log spans several OpenVINO versions; the first load after a change "
                "recompiles.",
            ),
            (
                "So sánh RTF theo phiên bản; nếu bản mới chậm hơn, ghim lại bản cũ trong "
                "constraints.txt.",
                "Compare RTF per version; if the newer one is slower, pin the old one in "
                "constraints.txt.",
            ),
            [],
            {"versions": summary},
            ["apps/inference/constraints.txt", "scripts/build-worker.ps1"],
        )
    ]


# --------------------------------------------------------------------------- report


STAGES = (
    "extract",
    "load_model",
    "inference",
    "load_translation_model",
    "translation",
    "translation_after_asr",
    "render",
    "total",
)


def stage_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in records if r.get("state") == "completed"]
    stages = {}
    for key in STAGES:
        values = [v for r in done if (v := _timing(r, key)) is not None]
        if values:
            stages[key] = {"median_ms": int(median(values) or 0), "max_ms": int(max(values))}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in done:
        groups[f"{r.get('model_id')}@{_resolved(r) or '?'}"].append(r)
    per_model = {
        key: {
            "jobs": len(items),
            "median_rtf": median(
                v for r in items if (v := _num(r.get("real_time_factor"))) is not None
            ),
            "median_load_ms": median(
                v for r in items if (v := _timing(r, "load_model")) is not None
            ),
        }
        for key, items in groups.items()
    }
    return {
        "jobs": len(records),
        "completed": len(done),
        "failed": sum(1 for r in records if r.get("state") == "failed"),
        "stages": stages,
        "per_model": per_model,
    }


def analyze(
    log_path: Path,
    limit: int = DEFAULT_LIMIT,
    job_ids: list[str] | None = None,
    redact: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    records, skipped = read_records(log_path, limit if not job_ids else MAX_LIMIT)
    if job_ids:
        wanted = set(job_ids)
        records = [r for r in records if r.get("job_id") in wanted]
    generated = (now or datetime.now(UTC)).astimezone(UTC)
    report: dict[str, Any] = {
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "log_path": "logs/jobs.jsonl" if redact else str(log_path),
        "log_found": log_path.is_file(),
        "skipped_lines": skipped,
        "jobs_analyzed": len(records),
        "newest_job_at": records[0].get("started_at") if records else None,
        "oldest_job_at": records[-1].get("started_at") if records else None,
        "redacted": redact,
        "thresholds": {
            "slow_asr_load_ms": SLOW_ASR_LOAD_MS,
            "slow_translation_load_ms": SLOW_TRANSLATION_LOAD_MS,
            "rtf_regression_factor": RTF_REGRESSION_FACTOR,
        },
    }
    if not records:
        report.update(status="no_data", findings=[], stats=stage_stats([]))
        return report
    findings: list[dict[str, Any]] = []
    findings += _rule_failures(records, redact)
    findings += _rule_device_lost(records)
    findings += _rule_slow_load(
        records,
        "load_model",
        "model_id",
        "device",
        SLOW_ASR_LOAD_MS,
        "slow_asr_model_load",
        ("model nhận dạng", "speech model"),
        ["app/whisper.py:load_model", "app/devices.py:compile_with_fallback"],
    )
    findings += _rule_slow_load(
        records,
        "load_translation_model",
        "translation_model",
        "translation_device",
        SLOW_TRANSLATION_LOAD_MS,
        "slow_translation_model_load",
        ("model dịch", "translation model"),
        [
            "app/translation.py:load_translator",
            "app/translation.py:npu_properties",
            "app/devices.py:compile_with_fallback",
            "app/jobs.py:_load_translator",
        ],
    )
    findings += _rule_rtf_regression(records)
    findings += _rule_slow_extract(records, redact)
    findings += _rule_translation(records)
    findings += _rule_device_fallback(records, redact)
    findings += _rule_power(records)
    findings += _rule_runtime_change(records)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    report.update(
        status="issues" if any(f["severity"] != "info" for f in findings) else "ok",
        findings=findings,
        stats=stage_stats(records),
        severity_counts=dict(Counter(f["severity"] for f in findings)),
    )
    return report


def compare_jobs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Stage-by-stage delta between two raw records (b minus a)."""
    timings = {}
    for key in STAGES:
        va, vb = _timing(a, key), _timing(b, key)
        if va is None and vb is None:
            continue
        delta = None if va is None or vb is None else int(vb - va)
        ratio = round(vb / va, 3) if va and vb is not None else None
        timings[key] = {"a_ms": va, "b_ms": vb, "delta_ms": delta, "ratio": ratio}
    fields = (
        "state",
        "model_id",
        "translation_model",
        "openvino_version",
        "language",
        "detected_language",
        "translate_to",
        "source_duration_ms",
        "real_time_factor",
        "cue_count",
        "translation_parallel",
        "overlap",
        "translation_overlap_lines",
        "translation_fallback_lines",
    )
    differences = {f: {"a": a.get(f), "b": b.get(f)} for f in fields if a.get(f) != b.get(f)}
    for key in ("device", "translation_device"):
        if _resolved(a, key) != _resolved(b, key):
            differences[key] = {"a": _resolved(a, key), "b": _resolved(b, key)}
    return {
        "a": a.get("job_id"),
        "b": b.get("job_id"),
        "timings": timings,
        "differences": differences,
    }


def optimization_plan(report: dict[str, Any], language: str = "en") -> str:
    """Markdown brief a coding agent can act on: findings -> evidence -> code -> verification."""
    lang = "vi" if language == "vi" else "en"
    vi = lang == "vi"
    lines = [
        "# LocalSub NPU – " + ("kế hoạch tối ưu" if vi else "optimization plan"),
        "",
        f"- generated_at: {report.get('generated_at')} (UTC)",
        f"- jobs_analyzed: {report.get('jobs_analyzed')}",
        f"- newest_job_at: {report.get('newest_job_at')}",
        "",
    ]
    findings = report.get("findings") or []
    if not findings:
        lines.append(
            "Không phát hiện điểm chưa tối ưu trong log hiện có."
            if vi
            else "No optimization opportunities found in the current log."
        )
    for i, f in enumerate(findings, 1):
        lines += [
            f"## {i}. [{f['severity']}] {f['title'][lang]} (`{f['id']}`)",
            "",
            f["detail"][lang],
            "",
            ("**Đề xuất:** " if vi else "**Recommendation:** ") + f["recommendation"][lang],
            "",
            ("**Mã nguồn liên quan:** " if vi else "**Code to inspect:** ")
            + ", ".join(f"`{ref}`" for ref in f["code_refs"]),
            "",
            "```json",
            json.dumps(f["evidence"], ensure_ascii=False, indent=1)[:1500],
            "```",
            "",
        ]
    lines += [
        "## " + ("Quy trình đề xuất cho AI" if vi else "Suggested workflow for the agent"),
        "",
        "1. "
        + (
            "Chọn finding có mức cao nhất; đọc các file trong code_refs của repo "
            "luongminhphu/localsub-npu."
            if vi
            else "Pick the highest-severity finding; read the files in code_refs in the "
            "luongminhphu/localsub-npu repository."
        ),
        "2. "
        + (
            "Viết test tái hiện (pytest trong apps/inference/tests) trước khi sửa."
            if vi
            else "Write a reproducing test (pytest in apps/inference/tests) before changing code."
        ),
        "3. "
        + (
            "Sửa trên nhánh mới, chạy `ruff check`, `pytest`, `npm test` rồi mở pull request; "
            "không tự merge."
            if vi
            else "Patch on a new branch, run `ruff check`, `pytest`, `npm test`, then open a pull "
            "request; never merge it yourself."
        ),
        "4. "
        + (
            "Sau khi người dùng cài bản mới, gọi lại localsub_analyze_performance và "
            "localsub_compare_jobs để xác nhận cải thiện."
            if vi
            else "After the user installs the new build, call localsub_analyze_performance and "
            "localsub_compare_jobs again to confirm the improvement."
        ),
    ]
    return "\n".join(lines) + "\n"
