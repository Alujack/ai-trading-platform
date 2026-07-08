# =============================================================================
# start-trading.ps1 - bring the whole trading platform up in one command.
#
#   .\start-trading.ps1              start everything (paper or live per .env)
#   .\start-trading.ps1 -SetupLive   write the live-broker keys into .env
#                                    (BROKER=exness, EXNESS_ENV=demo, bridge URL
#                                    + token copied from services\mt5bridge\.env)
#   .\start-trading.ps1 -AllowReal   required when EXNESS_ENV=real - the script
#                                    refuses to start a real-money session
#                                    without this explicit flag
#   .\start-trading.ps1 -Stop        stop containers + the native MT5 bridge
#
# Mode is derived from .env:
#   BROKER unset/paper -> paper stack only (no MT5 bridge)
#   BROKER=exness      -> also starts the native MT5 bridge (services\mt5bridge)
#                         and health-checks it before declaring ready.
#
# PowerShell 5.1 compatible.
# =============================================================================
[CmdletBinding()]
param(
    [switch]$SetupLive,
    [switch]$AllowReal,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$RepoRoot  = $PSScriptRoot
$EnvFile   = Join-Path $RepoRoot ".env"
$BridgeDir = Join-Path $RepoRoot "services\mt5bridge"
$BridgeEnv = Join-Path $BridgeDir ".env"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "  [XX] $msg" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------
function Read-DotEnv($path) {
    $map = @{}
    if (-not (Test-Path $path)) { return $map }
    foreach ($line in Get-Content $path) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    return $map
}

function Set-DotEnvKey($path, $key, $value) {
    $lines = @()
    if (Test-Path $path) { $lines = @(Get-Content $path) }
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($key))\s*=") { $found = $true; "$key=$value" }
        else { $line }
    }
    if (-not $found) { $out = @($out) + "$key=$value" }
    # ASCII: BOM-free so docker compose / python-dotenv parse the first key fine.
    Set-Content -Path $path -Value $out -Encoding ASCII
}

# ---------------------------------------------------------------------------
# HTTP wait helper
# ---------------------------------------------------------------------------
function Wait-Http($name, $url, $timeoutSec, $headers) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5 -Headers $headers
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                Write-Ok "$name responding at $url"
                return $true
            }
        } catch { Start-Sleep -Seconds 3 }
    }
    Write-Fail "$name did not respond at $url within ${timeoutSec}s"
    return $false
}

function Get-BridgePid {
    try {
        $conn = Get-NetTCPConnection -LocalPort 8800 -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($null -ne $conn) { return $conn.OwningProcess }
    } catch {}
    return $null
}

Set-Location $RepoRoot

# ---------------------------------------------------------------------------
# -Stop: tear everything down
# ---------------------------------------------------------------------------
if ($Stop) {
    Write-Step "Stopping docker services"
    docker compose down
    $bridgePid = Get-BridgePid
    if ($null -ne $bridgePid) {
        Write-Step "Stopping native MT5 bridge (pid $bridgePid)"
        try { Stop-Process -Id $bridgePid -Force -Confirm:$false } catch {}
        Write-Ok "bridge stopped"
    } else {
        Write-Ok "no native bridge running on :8800"
    }
    Write-Host "`nAll stopped." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# -SetupLive: write the live keys into root .env (demo account)
# ---------------------------------------------------------------------------
if ($SetupLive) {
    Write-Step "Writing live-broker keys into .env (EXNESS_ENV=demo)"
    $bridgeCfg = Read-DotEnv $BridgeEnv
    $token = $bridgeCfg["MT5_BRIDGE_TOKEN"]
    if (-not $token) {
        Write-Fail "MT5_BRIDGE_TOKEN not found in services\mt5bridge\.env - set up the bridge first."
        exit 1
    }
    Set-DotEnvKey $EnvFile "BROKER" "exness"
    Set-DotEnvKey $EnvFile "EXNESS_ENV" "demo"
    # api runs in docker; the native bridge is on the host.
    Set-DotEnvKey $EnvFile "MT5_BRIDGE_URL" "http://host.docker.internal:8800"
    Set-DotEnvKey $EnvFile "MT5_BRIDGE_TOKEN" $token
    Write-Ok "BROKER=exness, EXNESS_ENV=demo, MT5_BRIDGE_URL, MT5_BRIDGE_TOKEN written"
    Write-Host "  Re-run .\start-trading.ps1 to start the live (demo) stack." -ForegroundColor Cyan
    exit 0
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
Write-Step "Preflight checks"

if (-not (Test-Path $EnvFile)) { Write-Fail ".env not found at repo root"; exit 1 }
$cfg = Read-DotEnv $EnvFile

docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Fail "Docker engine is not running - start Docker Desktop first."; exit 1 }
Write-Ok "docker engine running"

$broker    = "paper"
if ($cfg["BROKER"]) { $broker = $cfg["BROKER"].ToLower() }
$exnessEnv = "demo"
if ($cfg["EXNESS_ENV"]) { $exnessEnv = $cfg["EXNESS_ENV"].ToLower() }
$isLive    = ($broker -eq "exness")

if ($isLive) {
    Write-Ok "mode: LIVE broker (exness, $exnessEnv account)"
    if ($exnessEnv -eq "real" -and -not $AllowReal) {
        Write-Fail "EXNESS_ENV=real but -AllowReal was not passed."
        Write-Host "       Real-money sessions must be started explicitly:" -ForegroundColor Red
        Write-Host "           .\start-trading.ps1 -AllowReal" -ForegroundColor Red
        exit 1
    }
    if (-not $cfg["MT5_BRIDGE_URL"])   { Write-Fail "BROKER=exness but MT5_BRIDGE_URL missing in .env (run -SetupLive)"; exit 1 }
    if (-not $cfg["MT5_BRIDGE_TOKEN"]) { Write-Fail "BROKER=exness but MT5_BRIDGE_TOKEN missing in .env (run -SetupLive)"; exit 1 }
    $bridgeCfg = Read-DotEnv $BridgeEnv
    if ($bridgeCfg["MT5_BRIDGE_TOKEN"] -and ($bridgeCfg["MT5_BRIDGE_TOKEN"] -ne $cfg["MT5_BRIDGE_TOKEN"])) {
        Write-Fail "MT5_BRIDGE_TOKEN in .env does not match services\mt5bridge\.env - api calls will 401 (run -SetupLive)"
        exit 1
    }
} else {
    Write-Ok "mode: PAPER trading (BROKER=$broker) - no real orders, no bridge needed"
}

# ---------------------------------------------------------------------------
# Native MT5 bridge (live mode only)
# ---------------------------------------------------------------------------
if ($isLive) {
    Write-Step "MT5 bridge (native, :8800)"
    $bridgeHeaders = @{ "X-Bridge-Token" = $cfg["MT5_BRIDGE_TOKEN"] }
    $bridgePid = Get-BridgePid
    if ($null -eq $bridgePid) {
        if (-not (Test-Path (Join-Path $BridgeDir "venv\Scripts\python.exe"))) {
            Write-Fail "bridge venv missing - in services\mt5bridge run:  python -m venv venv ; venv\Scripts\pip install -r requirements.txt"
            exit 1
        }
        Write-Host "  starting bridge (logs: services\mt5bridge\bridge.log)..."
        $bridgePy = Join-Path $BridgeDir "venv\Scripts\python.exe"
        Start-Process -FilePath $bridgePy `
            -ArgumentList "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8800" `
            -WorkingDirectory $BridgeDir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $BridgeDir "bridge.log") `
            -RedirectStandardError (Join-Path $BridgeDir "bridge.err.log")
    } else {
        Write-Ok "bridge already listening (pid $bridgePid)"
    }
    # /health also verifies the MT5 terminal connection + login.
    if (-not (Wait-Http "MT5 bridge" "http://localhost:8800/health" 90 $bridgeHeaders)) {
        Write-Fail "bridge unhealthy - is the MT5 terminal installed/logged in? Check the bridge window."
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Docker stack
# ---------------------------------------------------------------------------
Write-Step "Starting docker services (postgres, redis, ai, api, web, worker, n8n)"
docker compose up -d
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose up failed"; exit 1 }

Write-Step "Waiting for infrastructure"
$pgUser = "postgres"; if ($cfg["POSTGRES_USER"]) { $pgUser = $cfg["POSTGRES_USER"] }
$deadline = (Get-Date).AddSeconds(120)
$pgOk = $false
while ((Get-Date) -lt $deadline) {
    docker exec trading-postgres pg_isready -U $pgUser *> $null
    if ($LASTEXITCODE -eq 0) { $pgOk = $true; break }
    Start-Sleep -Seconds 3
}
if ($pgOk) { Write-Ok "postgres ready" } else { Write-Fail "postgres not ready after 120s"; exit 1 }

$redisPing = docker exec trading-redis redis-cli ping 2>$null
if ("$redisPing".Trim() -eq "PONG") { Write-Ok "redis ready" } else { Write-Warn2 "redis did not PONG yet" }

Write-Step "Waiting for application services"
$aiPort = "8000"; if ($cfg["AI_SERVICE_PORT"]) { $aiPort = $cfg["AI_SERVICE_PORT"] }
$apiPort = "4000"; if ($cfg["API_PORT"]) { $apiPort = $cfg["API_PORT"] }
$webPort = "3100"; if ($cfg["WEB_PORT"]) { $webPort = $cfg["WEB_PORT"] }

[void](Wait-Http "AI service" "http://localhost:$aiPort/health" 120 @{})
# api runs prisma migrate deploy before listening - give it time.
$apiOk = Wait-Http "API" "http://localhost:$apiPort/api/health" 180 @{}
if (-not $apiOk) { Write-Warn2 "check logs:  docker logs trading-api --tail 50" }
if (-not (Wait-Http "Web dashboard" "http://localhost:$webPort" 180 @{})) {
    Write-Warn2 "next dev can be slow on first compile - check:  docker logs trading-web --tail 50"
}

# ---------------------------------------------------------------------------
# Trading-readiness report
# ---------------------------------------------------------------------------
Write-Step "Trading readiness"

# Candle freshness - the strategy runner is blind on stale data.
# SQL is piped via stdin: docker exec args with embedded quotes get mangled by
# PowerShell 5.1 native-arg quoting, stdin bypasses that entirely.
function Invoke-TradingSql($sql) {
    return ($sql | docker exec -i trading-postgres psql -U $pgUser -d trading -tA 2>$null)
}

$staleHours = $null
try {
    $raw = Invoke-TradingSql 'SELECT COALESCE(EXTRACT(EPOCH FROM (now() - max("timestamp")))/3600.0, 999999) FROM "Candle";'
    $staleHours = [double]("$raw".Trim())
} catch {}
if ($null -ne $staleHours) {
    if ($staleHours -gt 24) {
        Write-Warn2 ("candle data is stale ({0:N0}h old) - worker backfills on its cycle, or run:" -f $staleHours)
        Write-Host  "       cd services\data ; python prepare_backtest.py --symbols XAUUSD --timeframes 15min --no-backtest" -ForegroundColor Yellow
    } else {
        Write-Ok ("candles fresh ({0:N1}h old)" -f $staleHours)
    }
}

# Enabled strategies + execution mode.
try {
    $strats = Invoke-TradingSql 'SELECT name FROM "Strategy" WHERE enabled = true ORDER BY name;'
    $mode   = Invoke-TradingSql "SELECT mode FROM `"ExecutionSetting`" WHERE scope = 'GLOBAL' LIMIT 1;"
    $stratList = ("$strats".Trim() -split "\r?\n" | Where-Object { $_ }) -join ", "
    Write-Ok "enabled strategies: $stratList"
    Write-Ok ("execution mode: {0}" -f "$mode".Trim())
} catch { Write-Warn2 "could not read strategy/execution config" }

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=============================================================" -ForegroundColor Green
if ($isLive) {
    Write-Host (" PLATFORM UP - LIVE trading ({0} account)" -f $exnessEnv.ToUpper()) -ForegroundColor Green
} else {
    Write-Host " PLATFORM UP - PAPER trading (no real orders)" -ForegroundColor Green
}
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "  Dashboard   http://localhost:$webPort"
Write-Host "  API         http://localhost:$apiPort/api/health"
Write-Host "  AI service  http://localhost:$aiPort/health"
if ($isLive) {
    Write-Host "  MT5 bridge  http://localhost:8800/health  (X-Bridge-Token)"
}
Write-Host "  n8n         http://localhost:5678"
Write-Host ""
Write-Host "  Logs:  docker compose logs -f api worker" -ForegroundColor DarkGray
Write-Host "  Stop:  .\start-trading.ps1 -Stop" -ForegroundColor DarkGray
Write-Host ""
exit 0
