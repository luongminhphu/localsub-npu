from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.config import Settings
from app.diagnostics import build_diagnostic_report


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalSub NPU local worker utilities")
    parser.add_argument("command", choices=["diagnostics"])
    args = parser.parse_args()
    if args.command == "diagnostics":
        settings = Settings.from_env()
        payload = asdict(build_diagnostic_report(settings))
        payload["output_dir"] = str(settings.output_dir)
        payload["model_dir"] = str(settings.model_dir)
        print(json.dumps(payload, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
