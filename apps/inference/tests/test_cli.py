import io
import json
import sys
from pathlib import Path

from app import main as cli
from app.project import project_from_cues
from tests.test_project import CUES


def test_export_command_reads_stdin(tmp_path: Path, monkeypatch, capsys) -> None:
    payload = project_from_cues(CUES, ["Xin chào", "Chào"], "en", "vi").to_dict()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    code = cli.main(["export", "--output-base", str(tmp_path / "o"), "--formats", "srt,txt"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    names = [Path(p).name for p in out["outputs"]]
    assert names == ["o.en.srt", "o.vi.srt", "o.en.txt", "o.vi.txt"]


def test_export_command_reports_validation_error(tmp_path: Path, monkeypatch, capsys) -> None:
    bad = {"cues": [{"start_ms": 3, "end_ms": 1, "text": "x"}]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(bad)))
    assert cli.main(["export", "--output-base", str(tmp_path / "o")]) == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error_code"] == "SUBTITLE_VALIDATION_FAILED"


def test_models_list_includes_both_kinds(capsys, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALSUB_MODEL_DIR", str(tmp_path))
    assert cli.main(["models", "list"]) == 0
    kinds = {m["kind"] for m in json.loads(capsys.readouterr().out)}
    assert kinds == {"asr", "translation"}


def test_formats_argument_validation() -> None:
    assert cli._formats("srt, VTT,srt") == ("srt", "vtt")
