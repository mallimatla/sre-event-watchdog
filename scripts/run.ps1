# SRE Event Watchdog — one-command demo launcher (Windows / PowerShell)
#
#   Starts: (1) mock alert receiver on :8001, (2) watchdog API + dashboard on :8000.
#   First run creates a venv and installs requirements.
#
# Usage:  ./scripts/run.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- venv ---
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

# --- mock receiver (background) ---
Write-Host "Starting mock alert receiver on http://localhost:8001 ..." -ForegroundColor Cyan
$receiver = Start-Process -FilePath $py `
    -ArgumentList "-m", "uvicorn", "mock_receiver.main:app", "--port", "8001" `
    -PassThru -NoNewWindow

Start-Sleep -Seconds 2

# --- watchdog API + dashboard (foreground) ---
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "  Dashboard : http://localhost:8000/"        -ForegroundColor Green
Write-Host "  API docs  : http://localhost:8000/docs"    -ForegroundColor Green
Write-Host "  Health    : http://localhost:8000/api/health" -ForegroundColor Green
Write-Host "  Mock recv : http://localhost:8001/received" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""

try {
    & $py -m app
}
finally {
    Write-Host "Shutting down mock receiver..." -ForegroundColor Cyan
    if ($receiver -and -not $receiver.HasExited) { Stop-Process -Id $receiver.Id -Force }
}
