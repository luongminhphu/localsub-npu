[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path "$PSScriptRoot\.."
$workerPath = Join-Path $repoRoot "apps\inference"

Write-Host "=== LocalSub NPU diagnostics ===" -ForegroundColor Cyan
Write-Host "Windows: $([System.Environment]::OSVersion.VersionString)"
Write-Host "Architecture: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
  Write-Host "FFmpeg: $($ffmpeg.Source)" -ForegroundColor Green
} else {
  Write-Warning "FFmpeg is not on PATH."
}

$venvPython = Join-Path $workerPath ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
  $pythonExe = $venvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $pythonExe = "python"
} else {
  throw "Python is required. Run .\scripts\setup-wave2.ps1 or install Python 3.11+."
}
if (-not $env:LOCALSUB_MODEL_DIR) { $env:LOCALSUB_MODEL_DIR = Join-Path $repoRoot "data\models" }

Push-Location $workerPath
try {
  & $pythonExe -m app.main diagnostics
} finally {
  Pop-Location
}

Write-Host "Open Task Manager > Performance > NPU while .\scripts\transcribe.ps1 runs to observe workload activity." -ForegroundColor Yellow
