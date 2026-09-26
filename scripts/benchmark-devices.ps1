<#
.SYNOPSIS
  Run the same media on NPU, GPU and CPU and compare load time, inference time and real-time factor.
.EXAMPLE
  .\scripts\benchmark-devices.ps1 -Source ".\fixtures\vi-60s.mp4" -Language vi
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Source,
  [ValidateSet("auto", "en", "vi")][string]$Language = "auto",
  [string]$Model = "whisper-small-int8",
  [string[]]$Devices = @("NPU", "GPU", "CPU")
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$worker = Join-Path $repoRoot "apps\inference"
$venvPython = Join-Path $worker ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Run .\scripts\setup-wave2.ps1 first." }

$sourcePath = (Resolve-Path $Source).Path
if (-not $env:LOCALSUB_MODEL_DIR) { $env:LOCALSUB_MODEL_DIR = Join-Path $repoRoot "data\models" }
$benchDir = Join-Path $repoRoot "data\benchmark"
New-Item -ItemType Directory -Force -Path $benchDir | Out-Null
$env:LOCALSUB_OUTPUT_DIR = $benchDir

$rows = @()
Push-Location $worker
try {
  foreach ($device in $Devices) {
    Write-Host "--- $device ---" -ForegroundColor Cyan
    $out = Join-Path $benchDir ("{0}.{1}.srt" -f [IO.Path]::GetFileNameWithoutExtension($sourcePath), $device)
    $json = & $venvPython -m app.main transcribe $sourcePath --language $Language --model $Model --device $device --output $out 2>$null
    $summary = ($json -join "`n") | ConvertFrom-Json
    $rows += [pscustomobject]@{
      Device        = $device
      State         = $summary.state
      Resolved      = $summary.device_resolved
      LoadMs        = $summary.timings_ms.load_model
      InferenceMs   = $summary.timings_ms.inference
      RTF           = $summary.real_time_factor
      Cues          = $summary.cue_count
      Error         = $summary.error_code
    }
  }
} finally {
  Pop-Location
}

$rows | Format-Table -AutoSize
$csv = Join-Path $benchDir ("benchmark-{0}.csv" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csv
Write-Host "Saved $csv. First NPU run includes model compilation; run twice to see the cached load time." -ForegroundColor Yellow
