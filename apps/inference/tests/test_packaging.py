"""The Windows build must ship a pinned OpenVINO runtime (v0.8.0's), not "latest"."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def pins() -> dict[str, str]:
    out = {}
    for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            out[name.strip().lower()] = version.strip()
    return out


def test_runtime_is_pinned_to_v080_versions() -> None:
    p = pins()
    assert p["openvino"] == "2026.4.0"
    assert p["openvino-genai"] == "2026.4.0.0"
    assert p["openvino-tokenizers"] == "2026.4.0.0"
    assert all(v for v in p.values())  # every line is an exact pin


def test_build_and_setup_scripts_use_the_constraints() -> None:
    for script in ("build-worker.ps1", "setup-wave2.ps1"):
        text = (REPO / "scripts" / script).read_text(encoding="utf-8")
        assert "-c constraints.txt" in text, script


def test_pyproject_range_allows_the_pin() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"openvino-genai>=2026.4,<2027"' in text


def test_version_chip_matches_package_versions() -> None:
    import json
    import re
    import tomllib

    desktop = REPO / "apps" / "desktop"
    version = json.loads((desktop / "package.json").read_text(encoding="utf-8"))["version"]
    worker = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert worker["project"]["version"] == version
    tauri = json.loads((desktop / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert tauri["version"] == version
    chips = re.findall(
        r'"app.chipDesktop": "Desktop · v([0-9.]+)"',
        (desktop / "src" / "lib" / "i18n.ts").read_text(encoding="utf-8"),
    )
    assert chips == [version, version]
