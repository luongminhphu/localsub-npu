<#
.SYNOPSIS
  Generate an SRT subtitle from a local video/audio file with OpenVINO Whisper.
.EXAMPLE
  .\scripts\transcribe.ps1 -Source "D:\Videos\meeting.mp4" -Language vi -Device NPU
.EXAMPLE
  .\scripts\transcribe.ps1 -Source ".\talk.mp4" -Language vi -Task translate   # Vietnamese speech -> English SRT
.EXAMPLE
  .\scripts\transcribe.ps1 -Source ".\webinar.mp4" -Language en -TranslateTo vi -Formats srt,vtt,json
  # English SRT + Vietnamese SRT (local LLM) + VTT + editable project JSON
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Source,
  [ValidateSet("auto", "en", "ja", "ko", "vi")][string]$Language = "auto",
  [ValidateSet("transcribe", "translate")][string]$Task = "transcribe",
  [ValidateSet("auto", "NPU", "GPU", "CPU")][string]$Device = "auto",
  [string]$Model = "whisper-small-int8",
  [string]$Output,
  [ValidateSet("vi", "en")][string]$TranslateTo,
  [string]$TranslationModel = "qwen3-8b-int4-cw",
  [ValidateSet("auto", "NPU", "GPU", "CPU")][string]$TranslationDevice = "auto",
  [string[]]$Formats = @("srt")
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$worker = Join-Path $repoRoot "apps\inference"
$venvPython = Join-Path $worker ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Run .\scripts\setup-wave2.ps1 first." }

$sourcePath = (Resolve-Path $Source).Path
if (-not $env:LOCALSUB_MODEL_DIR) { $env:LOCALSUB_MODEL_DIR = Join-Path $repoRoot "data\models" }
if (-not $env:LOCALSUB_OUTPUT_DIR) { $env:LOCALSUB_OUTPUT_DIR = Join-Path $repoRoot "data\output" }

$arguments = @("-m", "app.main", "transcribe", $sourcePath, "--language", $Language, "--task", $Task,
  "--device", $Device, "--model", $Model)
if ($Output) { $arguments += @("--output", $Output) }
if ($TranslateTo) {
  $arguments += @("--translate-to", $TranslateTo, "--translation-model", $TranslationModel,
    "--translation-device", $TranslationDevice)
}
$arguments += @("--formats", ($Formats -join ","))

Write-Host "Tip: open Task Manager > Performance > NPU 0 to watch utilization during inference." -ForegroundColor Yellow
Push-Location $worker
try {
  & $venvPython @arguments
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
