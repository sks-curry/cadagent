param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$agentState = $null
try { $agentState = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 3 } catch { }
if ($agentState -and $agentState.ready) {
    # Open an active session; never restart while a CAD task is running.
    if ($agentState.active_job -or ($agentState.max_rounds -eq 0 -and $agentState.max_tokens)) {
        Start-Process -FilePath "http://127.0.0.1:$Port"
        exit 0
    }
}
& (Join-Path $PSScriptRoot 'restart.ps1') -Port $Port
exit $LASTEXITCODE
