param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$agentUrl = "http://127.0.0.1:$Port"
$agentState = $null
try { $agentState = Invoke-RestMethod -Uri "$agentUrl/api/status" -TimeoutSec 3 } catch { }
if ($null -ne $agentState) {
    if ($agentState.active_job) { throw 'A task is running. Stop it and wait before restarting.' }
    if ($agentState.cli -notlike '*solidcraft-cli.exe') { throw 'Port belongs to a different application.' }
    if ($agentState.key_configured -and -not $agentState.connection_saved) {
        throw 'Save the API connection in the current page before restarting.'
    }
    $agentListener = Get-NetTCPConnection -State Listen -LocalPort $Port | Select-Object -First 1
    if ($null -eq $agentListener) { throw 'Cannot identify the local server process.' }
    $agentServer = Get-CimInstance Win32_Process -Filter "ProcessId=$($agentListener.OwningProcess)"
    if ($null -eq $agentServer -or $agentServer.Name -notmatch '^python(w)?\.exe$' -or $agentServer.CommandLine -notmatch '\bapp\.py\b') {
        throw 'Process identity does not match this Python application.'
    }
    # Stop only the identified web server. The SolidCraft desktop stays open.
    Stop-Process -Id $agentServer.ProcessId
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Process -Id $agentServer.ProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
}
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) { $agentPython = $venvPython } else { $agentPython = (Get-Command python -ErrorAction Stop).Source }
$agentPrivate = Join-Path $PSScriptRoot '.private'
New-Item -ItemType Directory -Path $agentPrivate -Force | Out-Null
$agentProcess = Start-Process -FilePath $agentPython -ArgumentList '-X','utf8','app.py','--no-browser','--port',$Port -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $agentPrivate 'server.stdout.log') -RedirectStandardError (Join-Path $agentPrivate 'server.stderr.log') -PassThru
Set-Content -LiteralPath (Join-Path $agentPrivate 'server.pid') -Value $agentProcess.Id -Encoding ascii
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    Start-Sleep -Milliseconds 400
    $agentProcess.Refresh()
    if ($agentProcess.HasExited) { throw 'Server exited. See .private\server.stderr.log.' }
    try {
        $agentState = Invoke-RestMethod -Uri "$agentUrl/api/status" -TimeoutSec 2
        if ($agentState.ready) { Start-Process -FilePath $agentUrl; exit 0 }
        if ($agentState.error) { throw $agentState.error }
    } catch { }
}
throw 'Server did not become ready. See .private\server.stderr.log.'
