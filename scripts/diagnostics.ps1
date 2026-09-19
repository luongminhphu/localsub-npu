[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path "$PSScriptRoot\.."
$workerPath = Join-Path $repoRoot "apps\inference"
Write-Host "=== LocalSub NPU diagnostics ===" -ForegroundColor Cyan
Write-Host "Windows: $([System.Environment]::OSVersion.VersionString)"
Write-Host "Architecture: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) { Write-Host "FFmpeg: $($ffmpeg.Source)" -ForegroundColor Green } else { Write-Warning "FFmpeg is not on PATH." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.11+ is required." }
Push-Location $workerPath
try { python -m app.main diagnostics } finally { Pop-Location }
Write-Host "Observe Task Manager > Performance > NPU during an actual future inference workload." -ForegroundColor Yellow
