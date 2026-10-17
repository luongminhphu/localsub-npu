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

  Write-Host "Smoke test: analyze + MCP server (stdio)"
  $analysis = & $exe analyze --limit 5 | Out-String
  if ($LASTEXITCODE -ne 0 -or $analysis -notmatch '"status"') { throw "analyze failed" }
  $requests = @(
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{}}}',
    '{"jsonrpc":"2.0","method":"notifications/initialized"}',
    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"localsub_diagnostics","arguments":{}}}',
    '{"jsonrpc":"2.0","id":3,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
  ) -join "`n"
  # Windows PowerShell 5.1 turns native stderr into errors under "Stop"; the server logs to stderr.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { $mcpOut = $requests | & $exe mcp 2>$null } finally { $ErrorActionPreference = $previous }
  if ($LASTEXITCODE -ne 0) { throw "mcp server exited with $LASTEXITCODE" }
  $lines = @($mcpOut | Where-Object { $_.Trim() })
  if ($lines.Count -ne 3) { throw "mcp: expected 3 responses, got $($lines.Count): $($lines -join ' | ')" }
  foreach ($line in $lines) {
    $msg = $line | ConvertFrom-Json  # throws if anything else leaked onto stdout
    if (-not $msg.result) { throw "mcp error response: $line" }
  }
  Write-Host "MCP ok: $((($lines[0] | ConvertFrom-Json).result.serverInfo.version))"

  Write-Host "Smoke test: MCP server over Streamable HTTP (Perplexity connector)"
  $env:LOCALSUB_MCP_TOKEN = "smoke-" + [guid]::NewGuid().ToString("N")
  $port = 18765
  $httpErr = Join-Path $env:TEMP "localsub-mcp-http.err"
  $httpOut = Join-Path $env:TEMP "localsub-mcp-http.out"
  $proc = Start-Process -FilePath $exe -ArgumentList @("mcp", "--http", "--port", "$port") -PassThru `
    -NoNewWindow -RedirectStandardError $httpErr -RedirectStandardOutput $httpOut
  try {
    $url = "http://127.0.0.1:$port/mcp"
    $ready = $false
    for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
      Start-Sleep -Milliseconds 500
      if ($proc.HasExited) { throw "mcp --http exited early: $(Get-Content $httpErr -Raw)" }
      try {
        $client = New-Object Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $port)
        $client.Close()
        $ready = $true
      } catch { }
    }
    if (-not $ready) { throw "mcp --http did not start listening on $port" }
    $body = '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
    $headers = @{
      "MCP-Protocol-Version" = "2026-07-28"
      "Mcp-Method"           = "server/discover"
      "Accept"               = "application/json, text/event-stream"
    }
    $status = 0
    try {
      Invoke-WebRequest -Uri $url -Method Post -Body $body -ContentType "application/json" -Headers $headers -UseBasicParsing | Out-Null
      $status = 200
    } catch {
      $status = [int]$_.Exception.Response.StatusCode
    }
    if ($status -ne 401) { throw "mcp http: expected 401 without the API key, got $status" }
    $headers["Authorization"] = "Bearer $env:LOCALSUB_MCP_TOKEN"
    $resp = Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType "application/json" -Headers $headers
    if ($resp.result.supportedVersions[0] -ne "2026-07-28") { throw "mcp http: unexpected response $($resp | ConvertTo-Json -Depth 5)" }
    $audit = Join-Path $env:LOCALSUB_OUTPUT_DIR "logs\mcp_http.jsonl"
    if (-not (Test-Path $audit)) { throw "mcp http: audit log $audit missing" }
    $entries = @(Get-Content $audit | ForEach-Object { $_ | ConvertFrom-Json })
    if ($entries.Count -lt 2 -or $entries[-2].status -ne 401 -or $entries[-1].status -ne 200) {
      throw "mcp http: audit log does not show 401 then 200"
    }
    Write-Host "MCP HTTP ok: 401 without key, 200 with key, $($entries.Count) audit entries"
  }
  finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Remove-Item Env:LOCALSUB_MCP_TOKEN -ErrorAction SilentlyContinue
  }

  $size = (Get-ChildItem -Recurse (Split-Path $exe) | Measure-Object Length -Sum).Sum / 1MB
  Write-Host ("Frozen worker ready: {0} ({1:N0} MB)" -f (Split-Path $exe), $size) -ForegroundColor Green
}
finally {
  Pop-Location
}
