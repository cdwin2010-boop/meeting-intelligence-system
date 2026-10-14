# start_all.ps1 - AI DOTS 일괄 실행 (백엔드 8001 + 프론트 3000)
# 사용법: powershell -ExecutionPolicy Bypass -File .\start_all.ps1 [-Worker] [-Reload] [-SkipMigrationCheck]
# 주의: 이 파일은 "UTF-8 (BOM 포함)"으로 저장해야 한글이 깨지지 않습니다.
param(
    [switch]$Worker,              # 작업 워커(worker.py)를 함께 실행
    [switch]$Reload,              # 개발용 자동 재시작 (기본 꺼짐, 처리 중 작업이 끊길 수 있음)
    [switch]$SkipMigrationCheck   # 마이그레이션 최신 여부 점검 건너뛰기
)

$ErrorActionPreference = "Stop"
# 콘솔 출력·외부 프로그램 출력 인코딩을 UTF-8 로 고정(한글 깨짐 방지)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# ---- 설정 (경로·포트는 여기서만 바꿉니다) ----
$ProjectRoot  = "C:\meeting-intelligence-system-v1.7"
$BackendDir   = Join-Path $ProjectRoot "backend"
$FrontendDir  = Join-Path $ProjectRoot "frontend"
$BackendPort  = 8001   # 운영 백엔드 (8000은 스텁 서버라 사용하지 않음)
$FrontendPort = 3000

function Fail($msg) {
    Write-Host "[중단] $msg" -ForegroundColor Red
    exit 1
}

function Test-PortInUse($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " AI DOTS 일괄 실행 시작" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ---- 0. 폴더·파일 점검 ----
if (-not (Test-Path $BackendDir))  { Fail "백엔드 폴더가 없습니다: $BackendDir" }
if (-not (Test-Path $FrontendDir)) { Fail "프론트 폴더가 없습니다: $FrontendDir" }
if (-not (Test-Path (Join-Path $BackendDir "app\main.py"))) {
    Fail "backend\app\main.py 가 없습니다. 백엔드 실행 경로를 확인하세요."
}
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Fail "frontend\node_modules 가 없습니다. 먼저 frontend 폴더에서 npm install 을 실행하세요."
}

# ---- 1. 파이썬 실행 파일 찾기 (가상환경 우선, 활성화 스크립트 없이 직접 사용) ----
$py = $null
foreach ($cand in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $BackendDir  ".venv\Scripts\python.exe"))) {
    if (Test-Path $cand) { $py = $cand; break }
}
if (-not $py) {
    Write-Warning "가상환경(.venv)을 찾지 못해 시스템 python 을 사용합니다."
    $py = "python"
}

# ---- 2. 포트 점검: 이미 사용 중이면 중단 (기존 프로세스는 건드리지 않음) ----
if (Test-PortInUse $BackendPort)  { Fail "포트 $BackendPort 이 이미 사용 중입니다. 기존 백엔드를 확인하세요." }
if (Test-PortInUse $FrontendPort) { Fail "포트 $FrontendPort 이 이미 사용 중입니다. 기존 프론트를 확인하세요." }

# ---- 3. 마이그레이션 최신 여부 점검 (적용은 하지 않음) ----
if (-not $SkipMigrationCheck) {
    if (Test-Path (Join-Path $BackendDir "alembic.ini")) {
        Push-Location $BackendDir
        # alembic 은 INFO 로그를 stderr 로 내보내 PowerShell 5.1 에서 빨간 오류(NativeCommandError)처럼 보인다.
        # cmd 로 stderr 를 stdout 에 합쳐 일반 문자열로 받는다(조회만 하며 적용하지 않는다).
        try   { $cur = (& cmd.exe /c "`"$py`" -m alembic current 2>&1" | Out-String) }
        finally { Pop-Location }

        if ($cur -notmatch "\(head\)") {
            Write-Host (($cur -split "`r?`n" | Where-Object { $_ -and $_ -notmatch "^INFO\s" }) -join "`n")
            Fail ("DB 가 최신 마이그레이션이 아닙니다. 서버를 모두 끈 상태에서 DB 를 백업한 뒤 " +
                  "backend 폴더에서 'alembic upgrade head' 를 실행하세요. (이 스크립트는 자동 적용하지 않습니다)")
        }
        Write-Host "[확인] DB 마이그레이션이 최신입니다." -ForegroundColor Green
    } else {
        Write-Warning "backend\alembic.ini 가 없어 마이그레이션 점검을 건너뜁니다."
    }
}

# ---- 4. 작업 워커 필요 여부 판단 (.env 값은 출력하지 않고 queue 여부만 확인) ----
$queueMode = $false
foreach ($envFile in @((Join-Path $BackendDir ".env"), (Join-Path $ProjectRoot ".env"))) {
    if (Test-Path $envFile) {
        if (Select-String -Path $envFile -Pattern '^\s*PROCESSING_MODE\s*=\s*queue\s*$' -Quiet) {
            $queueMode = $true
        }
    }
}
$workerPath = Join-Path $BackendDir "worker.py"
$startWorker = $Worker -or $queueMode
if ($startWorker -and -not (Test-Path $workerPath)) {
    Write-Warning "워커가 필요하지만 backend\worker.py 를 찾지 못했습니다. 워커 없이 진행합니다."
    $startWorker = $false
}

# ---- 5. 백엔드 기동 ----
Write-Host "[1/3] 백엔드 서버 기동 중... (포트 $BackendPort)" -ForegroundColor Green
$reloadArg = ""
if ($Reload) { $reloadArg = "--reload" }
$backendCmd = "Set-Location '$BackendDir'; & '$py' -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort $reloadArg"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd -WindowStyle Normal

# ---- 6. 백엔드가 응답할 때까지 최대 30초 대기 ----
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 2
        if ($r.status -eq "ok") { $healthy = $true; break }
    } catch { }
}
if (-not $healthy) {
    Fail "백엔드가 30초 안에 응답하지 않았습니다. 열린 백엔드 창의 오류 메시지를 확인하세요. 프론트는 시작하지 않았습니다."
}
Write-Host "      백엔드 응답 확인 (/api/health)" -ForegroundColor Green

# ---- 7. 작업 워커 기동 (queue 모드이거나 -Worker 지정 시) ----
if ($startWorker) {
    Write-Host "[2/3] 작업 워커 기동 중..." -ForegroundColor Green
    $workerCmd = "Set-Location '$BackendDir'; & '$py' worker.py"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $workerCmd -WindowStyle Normal
} else {
    Write-Host "[2/3] 작업 워커: 사용 안 함 (inline 모드)" -ForegroundColor DarkGray
}

# ---- 8. 프론트엔드 기동 ----
Write-Host "[3/3] 프론트엔드 개발 서버 기동 중... (포트 $FrontendPort)" -ForegroundColor Green
$frontendCmd = "Set-Location '$FrontendDir'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd -WindowStyle Normal

Write-Host ""
Write-Host "모든 서비스를 각각의 창에서 실행했습니다. (끄려면 각 창에서 Ctrl+C 후 닫기)" -ForegroundColor Cyan
Write-Host " - 백엔드 API 문서: http://127.0.0.1:$BackendPort/docs"
Write-Host " - 웹 화면:         http://localhost:$FrontendPort/v2"
Write-Host "==========================================" -ForegroundColor Cyan