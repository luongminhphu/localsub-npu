"""Local model store: status, resumable downloads with progress, and removal.

Downloads go straight into ``<model_dir>/<model_id>`` because Hugging Face resumes partial
files there (``.cache/huggingface/download/*.incomplete``). A model only counts as installed
once every required IR file has its weights and no partial file remains (see ``ir_status``).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import catalog as model_catalog
from app import translation, whisper
from app.audit import AuditLog, to_jsonable
from app.devices import MARKER_FILE, ir_status
from app.errors import ErrorCode, LocalSubError

# Approximate download sizes, shown before a download starts (no network needed).
APPROX_SIZE_MB: dict[str, int] = {m.id: m.approx_mb for m in model_catalog.MODELS}
DISK_HEADROOM = 1.10  # keep 10% spare on top of the model size

EventSink = Callable[[dict[str, Any]], None]
Snapshot = Callable[[str, Path], None]
RepoSize = Callable[[str], int | None]


@dataclass(frozen=True)
class CatalogEntry:
    model_id: str
    kind: str  # "asr" | "translation"
    repo: str
    required: tuple[str, ...]


def catalog() -> dict[str, CatalogEntry]:
    entries = {
        m: CatalogEntry(m, "asr", repo, whisper.REQUIRED_FILES)
        for m, repo in whisper.MODEL_CATALOG.items()
    }
    entries.update(
        {
            m: CatalogEntry(m, "translation", repo, translation.required_files(m))
            for m, repo in translation.TRANSLATION_CATALOG.items()
        }
    )
    return entries


def entry_for(model_id: str) -> CatalogEntry:
    entries = catalog()
    if model_id not in entries:
        known = ", ".join(sorted(entries))
        raise LocalSubError(
            ErrorCode.MODEL_NOT_FOUND, f"Unknown model '{model_id}'. Known: {known}"
        )
    return entries[model_id]


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def status(model_dir: Path, model_id: str) -> str:
    entry = entry_for(model_id)
    return ir_status(model_dir / model_id, entry.required)


def installed_models(model_dir: Path) -> list[str]:
    return [m for m in catalog() if status(model_dir, m) == "installed"]


def list_models(model_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for m, entry in catalog().items():
        path = model_dir / m
        state = ir_status(path, entry.required)
        rows.append(
            {
                "id": m,
                "kind": entry.kind,
                "source": entry.repo,
                "status": state,
                "installed": state == "installed",
                "size_bytes": folder_size(path) if state != "missing" else 0,
                "approx_size_mb": APPROX_SIZE_MB.get(m),
                "path": path,
                **(spec.to_dict() if (spec := model_catalog.spec(m)) else {}),
            }
        )
    return rows


def _hf_snapshot(repo: str, target: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise LocalSubError(
            ErrorCode.OPENVINO_UNAVAILABLE, 'Install extras: pip install -e ".[openvino]"'
        ) from exc
    snapshot_download(repo_id=repo, local_dir=str(target))


def _hf_repo_size(repo: str) -> int | None:
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
        sizes = [s.size or 0 for s in (info.siblings or [])]
        return sum(sizes) or None
    except Exception:  # size is only for progress; never fail the download on it
        return None


@dataclass(frozen=True)
class DownloadResult:
    model_id: str
    kind: str
    path: Path
    status: str
    size_bytes: int
    already_installed: bool


def _audit(model_dir: Path, record: dict[str, Any]) -> None:
    record = {"at": datetime.now(UTC), **record}
    try:
        AuditLog(model_dir / "logs" / "models.jsonl").write(record)
    except OSError:
        pass  # auditing must not break model management


def download(
    model_dir: Path,
    model_id: str,
    emit: EventSink | None = None,
    snapshot: Snapshot = _hf_snapshot,
    repo_size: RepoSize = _hf_repo_size,
    poll_seconds: float = 1.0,
    disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free,
) -> DownloadResult:
    entry = entry_for(model_id)
    target = model_dir / model_id
    send = emit or (lambda _event: None)

    if ir_status(target, entry.required) == "installed":
        size = folder_size(target)
        send(
            {
                "type": "download",
                "state": "completed",
                "model_id": model_id,
                "downloaded_bytes": size,
                "total_bytes": size,
                "already_installed": True,
            }
        )
        return DownloadResult(model_id, entry.kind, target, "installed", size, True)

    model_dir.mkdir(parents=True, exist_ok=True)
    total = repo_size(entry.repo)
    if total is None:
        approx = APPROX_SIZE_MB.get(model_id)
        total_estimate = approx * 1024 * 1024 if approx else None
    else:
        total_estimate = total
    if total_estimate:
        needed = int(total_estimate * DISK_HEADROOM) - folder_size(target)
        free = disk_free(model_dir)
        if needed > free:
            raise LocalSubError(
                ErrorCode.INSUFFICIENT_DISK,
                f"Need about {needed // 2**20} MB free in {model_dir}, "
                f"only {free // 2**20} MB left.",
            )

    (target / MARKER_FILE).unlink(missing_ok=True)
    started = time.perf_counter()
    send(
        {
            "type": "download",
            "state": "downloading",
            "model_id": model_id,
            "downloaded_bytes": folder_size(target),
            "total_bytes": total,
        }
    )
    stop = threading.Event()

    def report() -> None:
        while not stop.wait(poll_seconds):
            send(
                {
                    "type": "download",
                    "state": "downloading",
                    "model_id": model_id,
                    "downloaded_bytes": folder_size(target),
                    "total_bytes": total,
                }
            )

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        snapshot(entry.repo, target)
    except LocalSubError:
        raise
    except Exception as exc:
        _audit(
            model_dir,
            {
                "action": "download",
                "model_id": model_id,
                "state": "failed",
                "error": type(exc).__name__,
            },
        )
        raise LocalSubError(
            ErrorCode.DOWNLOAD_FAILED,
            f"Download of '{model_id}' stopped: {str(exc)[:200]}. Run it again to resume.",
        ) from exc
    finally:
        stop.set()
        reporter.join(timeout=2)

    if ir_status(target, entry.required) != "missing":
        _finish(target, entry)
    state = ir_status(target, entry.required)
    size = folder_size(target)
    elapsed = int((time.perf_counter() - started) * 1000)
    _audit(
        model_dir,
        {
            "action": "download",
            "model_id": model_id,
            "state": state,
            "size_bytes": size,
            "duration_ms": elapsed,
        },
    )
    if state != "installed":
        raise LocalSubError(
            ErrorCode.MODEL_INCOMPLETE,
            f"Download of '{model_id}' finished but required files are missing. Run it again.",
        )
    send(
        {
            "type": "download",
            "state": "completed",
            "model_id": model_id,
            "downloaded_bytes": size,
            "total_bytes": total or size,
            "already_installed": False,
        }
    )
    return DownloadResult(model_id, entry.kind, target, state, size, False)


def _finish(target: Path, entry: CatalogEntry) -> None:
    """After a successful snapshot: drop orphaned partial files, then write the marker."""
    for name in entry.required:
        xml = target / name
        if not xml.is_file() or not xml.with_suffix(".bin").is_file():
            return
    cache = target / ".cache" / "huggingface" / "download"
    if cache.is_dir():
        for orphan in cache.glob("*.incomplete"):
            orphan.unlink(missing_ok=True)  # left by an interrupted earlier attempt
    marker = {"model_id": entry.model_id, "repo": entry.repo, "completed_at": datetime.now(UTC)}
    (target / MARKER_FILE).write_text(json.dumps(to_jsonable(marker)), encoding="utf-8")


def remove(model_dir: Path, model_id: str) -> dict[str, Any]:
    """Delete one catalog model folder. Only paths directly under ``model_dir`` are touched."""
    entry_for(model_id)  # allow-list: unknown ids never reach the filesystem
    root = model_dir.resolve()
    target = (model_dir / model_id).resolve()
    if target.parent != root:
        raise LocalSubError(ErrorCode.MODEL_NOT_FOUND, f"Refusing to delete outside {root}")
    if not target.exists():
        return {"model_id": model_id, "removed": False, "freed_bytes": 0}
    freed = folder_size(target)
    shutil.rmtree(target)
    _audit(model_dir, {"action": "remove", "model_id": model_id, "freed_bytes": freed})
    return {"model_id": model_id, "removed": True, "freed_bytes": freed}
