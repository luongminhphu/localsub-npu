"""Entry point for the frozen (PyInstaller) worker shipped inside the desktop installer.

The desktop app runs ``localsub-worker.exe <args>`` exactly like ``python -m app.main <args>``.
"""

from __future__ import annotations

import multiprocessing
import sys

from app.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
