<#
.SYNOPSIS
  Prepare the LocalSub NPU Python worker for real Whisper inference on Windows 11.
.EXAMPLE
  .\scripts\setup-wave2.ps1 -Model whisper-small-int8
.EXAMPLE
  .\scripts\setup-wave2.ps1 -TranslationModel qwen3-8b-int4-cw   # also fetch the EN<->VI LLM (~5 GB)
#>
[CmdletBinding()]
param(
  [string]$Model = "whisper-small-int8",
  [string]$PythonLauncher = "py",
  [string]$PythonVersion = "3.11",
  [string]$TranslationModel,
  [switch]$SkipModelDownload
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$worker = Join-Path $repoRoot "apps\inference"
$venvPython = Join-Path $worker ".venv\Scripts\python.exe"

Write-Host "=== LocalSub NPU Wave 2 setup ===" -ForegroundColor Cyan

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning "FFmpeg not found on PATH. Install with: winget install --id Gyan.FFmpeg -e  (then reopen the terminal)"
}

if (-not (Test-Path $venvPython)) {
  Write-Host "Creating virtual environment (Python $PythonVersion)..."
  if (Get-Command $PythonLauncher -ErrorAction SilentlyContinue) {
    & $PythonLauncher "-$PythonVersion" -m venv (Join-Path $worker ".venv")
  } else {
    python -m venv (Join-Path $worker ".venv")
  }
  if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
}

Push-Location $worker
try {
  & $venvPython -m pip install --upgrade pip
  & $venvPython -m pip install -e ".[dev,openvino]"
  if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

  if (-not $env:LOCALSUB_MODEL_DIR) { $env:LOCALSUB_MODEL_DIR = Join-Path $repoRoot "data\models" }
  if (-not $env:LOCALSUB_OUTPUT_DIR) { $env:LOCALSUB_OUTPUT_DIR = Join-Path $repoRoot "data\output" }

  if (-not $SkipModelDownload) {
    Write-Host "Downloading $Model into $env:LOCALSUB_MODEL_DIR ..."
    & $venvPython -m app.main models download $Model
    if ($LASTEXITCODE -ne 0) { throw "Model download failed." }
    if ($TranslationModel) {
      Write-Host "Downloading translation model $TranslationModel ..."
      & $venvPython -m app.main models download $TranslationModel
      if ($LASTEXITCODE -ne 0) { throw "Translation model download failed." }
    }
  }

  & $venvPython -m app.main diagnostics
} finally {
  Pop-Location
}

Write-Host "Setup complete. Next: .\scripts\transcribe.ps1 -Source <video> -Language vi -Device NPU" -ForegroundColor Green
