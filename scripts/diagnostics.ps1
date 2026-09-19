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

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  throw "Python is required. Install Python 3.11+ and ensure it is on PATH."
}

Push-Location $workerPath
try {
  python -m app.main diagnostics
} finally {
  Pop-Location
}

Write-Host "Open Task Manager > Performance > NPU while a future inference job runs to observe workload activity." -ForegroundColor Yellow
