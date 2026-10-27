<#
.SYNOPSIS
  Starts EVOLVE (FastAPI backend + frontend) with one command.
.DESCRIPTION
  Default: dev mode (Vite on http://127.0.0.1:5173).
  -Built : build the production bundle and serve it with `vite preview` (http://127.0.0.1:4173).
  -NoBrowser : do not open the browser.
  Stop everything with .\stop.ps1. Logs are written to .\logs\.
#>
[CmdletBinding()]
param(
    [switch]$Built,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'
$Logs = Join-Path $Root 'logs'
$BackendPort = 8000
$FrontendPort = if ($Built) { 4173 } else { 5173 }

function Fail($message) { Write-Host "ERROR: $message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

function Stop-Port($port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

function Wait-Url($url, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { return Invoke-RestMethod -Uri $url -TimeoutSec 5 } catch { Start-Sleep -Milliseconds 700 }
    }
    return $null
}

# ---- prerequisites -----------------------------------------------------------------
Step 'Checking prerequisites'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail 'Node.js/npm was not found. Install Node 20.19+ or 22.12+.' }
if (-not (Test-Path $Python)) {
    Step 'Creating the backend virtual environment (first run)'
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) { Fail 'Python launcher `py` was not found. Install Python 3.13.' }
    Push-Location $Backend
    try {
        & py -3.13 -m venv .venv
        & $Python -m pip install -r requirements-lock.txt
    } finally { Pop-Location }
}
$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root '.env.example') $envFile
    Write-Host 'Created .env from .env.example.' -ForegroundColor Yellow
}
$keyLine = Select-String -Path $envFile -Pattern '^\s*LLM_API_KEY\s*=\s*(\S+)' -ErrorAction SilentlyContinue
if (-not $keyLine -and -not $env:LLM_API_KEY) {
    Write-Host 'No LLM_API_KEY is set. Get a free key (Groq: https://console.groq.com/keys),' -ForegroundColor Yellow
    Write-Host "put it on the LLM_API_KEY= line in $envFile, then run this script again." -ForegroundColor Yellow
    Write-Host 'Continuing: the app will start and show what is missing.' -ForegroundColor Yellow
}
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Step 'Installing frontend packages (first run)'
    Push-Location $Frontend
    try { npm ci } finally { Pop-Location }
}
if ($Built) {
    Step 'Building the production frontend'
    Push-Location $Frontend
    try { npm run build; if ($LASTEXITCODE -ne 0) { Fail 'Frontend build failed.' } } finally { Pop-Location }
}

# ---- start -------------------------------------------------------------------------
Step 'Freeing ports from any previous run'
Stop-Port $BackendPort
Stop-Port $FrontendPort
New-Item -ItemType Directory -Force $Logs | Out-Null

Step 'Starting the backend'
$backendProc = Start-Process -FilePath $Python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $BackendPort `
    -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Logs 'backend.out.log') `
    -RedirectStandardError (Join-Path $Logs 'backend.err.log')

Step 'Starting the frontend'
$npm = (Get-Command npm.cmd).Source
$frontArgs = if ($Built) { '/c', 'npm', 'run', 'preview' } else { '/c', 'npm', 'run', 'dev' }
$frontProc = Start-Process -FilePath 'cmd.exe' -ArgumentList $frontArgs `
    -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Logs 'frontend.out.log') `
    -RedirectStandardError (Join-Path $Logs 'frontend.err.log')

@{ backend = $backendProc.Id; frontend = $frontProc.Id; frontendPort = $FrontendPort } |
    ConvertTo-Json | Set-Content (Join-Path $Logs 'pids.json')

# ---- verify ------------------------------------------------------------------------
Step 'Waiting for the backend'
$health = Wait-Url "http://127.0.0.1:$BackendPort/health" 40
if (-not $health) { Fail "Backend did not start. See $Logs\backend.err.log" }
$app = Wait-Url "http://127.0.0.1:$FrontendPort/api/health" 40
if (-not $app) { Fail "Frontend did not start. See $Logs\frontend.err.log" }

$model = Wait-Url "http://127.0.0.1:$BackendPort/model-status" 30
Write-Host ''
if ($model -and $model.status -eq 'ready') {
    Write-Host "Model API ready: $($model.provider) / $($model.model)" -ForegroundColor Green
} elseif ($model) {
    Write-Host "Model API status: $($model.status)" -ForegroundColor Yellow
    Write-Host $model.message -ForegroundColor Yellow
}
$url = "http://127.0.0.1:$FrontendPort"
Write-Host "EVOLVE is running at $url   (stop with .\stop.ps1, logs in .\logs)" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $url }
