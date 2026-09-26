# PyInstaller spec for the LocalSub NPU worker (one-folder build).
# Build: pyinstaller --noconfirm --clean apps/inference/packaging/localsub-worker.spec
# Output: dist/localsub-worker/localsub-worker(.exe) plus _internal/
import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], ["app.main"]
for package in ("openvino", "openvino_genai", "openvino_tokenizers", "huggingface_hub"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "worker_entry.py")],
    pathex=[os.path.join(SPECPATH, "..")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="localsub-worker",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="localsub-worker", upx=False)
