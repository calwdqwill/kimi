# Brent Spread Dashboard — quick launch script for PowerShell
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvActivate = Join-Path $projectRoot "venv\Scripts\Activate.ps1"

if (-not (Test-Path $venvActivate)) {
    Write-Host "Virtual environment not found." -ForegroundColor Red
    Write-Host "Please run first:" -ForegroundColor Yellow
    Write-Host "  python -m venv venv" -ForegroundColor Yellow
    Write-Host "  .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

& $venvActivate

Set-Location (Join-Path $projectRoot "backend")

Write-Host "Starting Brent Spread Dashboard at http://localhost:8000 ..." -ForegroundColor Cyan
uvicorn main:app --reload --port 8000
