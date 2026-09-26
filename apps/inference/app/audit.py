from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path, PurePath
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Convert audit payloads (dataclasses, enums, paths, datetimes, tuples) to JSON types.

    Wave 2 regression: audit logging previously crashed after the SRT was written because
    Path, StrEnum and nested dataclasses were passed straight to json.dumps.
    """
    if value is None or isinstance(value, bool | int | float | str):
        if isinstance(value, Enum):
            return value.value
        return value
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(to_jsonable(k)): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [to_jsonable(item) for item in items]
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)[:500]}
    return repr(value)


class AuditLog:
    """Append-only local JSON Lines audit log. Never stores transcript text."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, record: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(to_jsonable(record), ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
