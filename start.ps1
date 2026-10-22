param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
try {
    $existing = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 2
    if ($existing.ready -and $existing.cli -like '*solidcraft-cli.exe') {
        Start-Process -FilePath "http://127.0.0.1:$Port"
        exit 0
    }
} catch { }
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) { $agentPython = $localPython } else { $agentPython = 'python' }
& $agentPython -c "import mcp, openai, fastapi, uvicorn, jsonschema, PIL"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Missing dependencies. Run: python -m pip install -r requirements.txt'
    exit 1
}
& $agentPython -X utf8 app.py --port $Port
