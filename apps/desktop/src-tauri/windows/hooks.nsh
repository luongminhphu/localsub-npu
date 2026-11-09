; NSIS hooks for the Tauri installer.
;
; The bundled worker (worker\localsub-worker.exe) can outlive the desktop app: Claude Desktop,
; Claude Code, VS Code or Perplexity start it as a stdio MCP server (`localsub-worker.exe mcp`)
; and keep it running. Its loaded DLLs (VCRUNTIME140.dll, python3*.dll, OpenVINO) are then locked
; and the installer fails with "Error opening file for writing". Stop only the worker processes
; that run from this install folder before files are copied or removed; the AI app restarts the
; MCP server the next time it is used (or after the AI app is restarted).

!macro LOCALSUB_STOP_WORKERS
  DetailPrint "Stopping LocalSub worker processes in $INSTDIR"
  ; Win32_Process (CIM) gives ExecutablePath even from this 32-bit installer, where
  ; Get-Process cannot read the path of a 64-bit process.
  nsExec::ExecToLog `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$dir = [IO.Path]::GetFullPath('$INSTDIR\worker\'); Get-CimInstance Win32_Process | Where-Object { $$_.Name -eq 'localsub-worker.exe' -and $$_.ExecutablePath -and $$_.ExecutablePath.StartsWith($$dir, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"`
  Pop $0
  ; Give Windows a moment to release the file handles.
  Sleep 1000
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro LOCALSUB_STOP_WORKERS
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro LOCALSUB_STOP_WORKERS
!macroend
