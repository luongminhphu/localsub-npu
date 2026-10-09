<#
.SYNOPSIS
  Build the frozen LocalSub NPU worker (PyInstaller one-folder) used by the Windows installer.
.DESCRIPTION
  Creates apps\inference\.venv-build (Python 3.11), installs the worker with OpenVINO and
  PyInstaller, freezes it to apps\inference\dist\localsub-worker\ and runs a smoke test.
  Then build the installer from apps\desktop:
    npx tauri build --bundles nsis --config src-tauri/tauri.bundle.conf.json
.EXAMPLE
  .\scripts\build-worker.ps1
#>
[CmdletBinding()]
param(
  [string]$PythonLauncher = "py",
  [string]$PythonVersion = "3.11",
  [string]$Python   # use this interpreter instead of creating .venv-build (CI)
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$worker = Join-Path $repoRoot "apps\inference"

Push-Location $worker
try {
  if (-not $Python) {
    $Python = Join-Path $worker ".venv-build\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
      Write-Host "Creating build environment (Python $PythonVersion)..."
      & $PythonLauncher "-$PythonVersion" -m venv .venv-build
      if ($LASTEXITCODE -ne 0) { throw "Cannot create .venv-build (is Python $PythonVersion installed?)" }
    }
  }
  & $Python -m pip install --upgrade pip | Out-Null
  & $Python -m pip install -c constraints.txt ".[openvino]" "pyinstaller>=6.16,<7"
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

  & $Python -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath dist --workpath build packaging\localsub-worker.spec
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

  $exe = Join-Path $worker "dist\localsub-worker\localsub-worker.exe"
  if (-not (Test-Path $exe)) { throw "Frozen worker not found: $exe" }

  Write-Host "Smoke test: diagnostics + models list"
  $env:LOCALSUB_MODEL_DIR = Join-Path $env:TEMP "localsub-smoke\models"
  $env:LOCALSUB_OUTPUT_DIR = Join-Path $env:TEMP "localsub-smoke\output"
  & $exe diagnostics | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "diagnostics failed" }
  $models = & $exe models list | Out-String
  if ($LASTEXITCODE -ne 0 -or $models -notmatch "whisper-small-int8") { throw "models list failed" }

  $size = (Get-ChildItem -Recurse (Split-Path $exe) | Measure-Object Length -Sum).Sum / 1MB
  Write-Host ("Frozen worker ready: {0} ({1:N0} MB)" -f (Split-Path $exe), $size) -ForegroundColor Green
}
finally {
  Pop-Location
}
