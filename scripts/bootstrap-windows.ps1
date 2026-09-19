[CmdletBinding()]
param(
  [switch]$InstallPythonDependencies,
  [switch]$InstallNodeDependencies
)

$ErrorActionPreference = "Stop"

function Test-Command([string]$Name) {
  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -eq $command) {
    Write-Warning "$Name was not found on PATH."
    return $false
  }
  Write-Host "$Name: $($command.Source)" -ForegroundColor Green
  return $true
}

Write-Host "LocalSub NPU Windows bootstrap" -ForegroundColor Cyan
$pythonOk = Test-Command "python"
$nodeOk = Test-Command "node"
$npmOk = Test-Command "npm"
$ffmpegOk = Test-Command "ffmpeg"
$cargoOk = Test-Command "cargo"

if (-not $ffmpegOk) {
  Write-Warning "Install FFmpeg and add it to PATH, or set FFMPEG_PATH in .env."
}

if ($InstallPythonDependencies -and $pythonOk) {
  Push-Location "$PSScriptRoot\..\apps\inference"
  try {
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  }
  finally {
    Pop-Location
  }
}

if ($InstallNodeDependencies -and $npmOk) {
  Push-Location "$PSScriptRoot\..\apps\desktop"
  try {
    npm install
  }
  finally {
    Pop-Location
  }
}

if (-not $cargoOk) {
  Write-Warning "Rust/Cargo is required only to build the Tauri desktop executable."
}

Write-Host "Bootstrap checks completed." -ForegroundColor Cyan
