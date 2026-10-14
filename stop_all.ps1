# stop_all.ps1 - AI DOTS 일괄 종료 (백엔드 8001 + 프론트 3000 + 워커)
# 사용법: .\stop_all.ps1 [-Yes]   (-Yes: 확인 질문 없이 종료)
param(
    [switch]$Yes
)

$ErrorActionPreference = "Continue"
# 콘솔 출력·외부 프로그램 출력 인코딩을 UTF-8 로 고정(한글 깨짐 방지)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# ---- 설정 (start_all.ps1 과 같은 값) ----
$ProjectRoot  = "C:\meeting-intelligence-system-v1.7"
$BackendDir   = Join-Path $ProjectRoot "backend"
$FrontendDir  = Join-Path $ProjectRoot "frontend"
$BackendPort  = 8001
$FrontendPort = 3000

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " AI DOTS 일괄 종료" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ---- 종료 전 확인: 처리 중인 작업이 있으면 중단될 수 있음 ----
if (-not $Yes) {
    Write-Host "처리 중인 회의록 작업(음성 변환·업무 추출)이 있으면 중단됩니다." -ForegroundColor Yellow
    $ans = Read-Host "계속 종료할까요? (Y/N)"
    if ($ans -notmatch '^[Yy]') {
        Write-Host "종료를 취소했습니다."
        exit 0
    }
}

$self = $PID
$stopped = 0

# 프로세스 트리째 종료 (/T: 자식 포함)
function Stop-Tree($procId, $label) {
    & taskkill.exe /PID $procId /T /F 2>&1 | Out-Null
    Write-Host ("  - 종료: {0} (PID {1})" -f $label, $procId) -ForegroundColor Green
    $script:stopped++
}

# ---- 1. start_all.ps1 이 띄운 창(백엔드·워커·프론트)을 찾아 트리째 종료 ----
$launchers = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue
foreach ($p in $launchers) {
    if ($p.ProcessId -eq $self) { continue }
    $cmd = $p.CommandLine
    if (-not $cmd) { continue }

    if ($cmd -like "*$BackendDir*" -and $cmd -like "*uvicorn*") {
        Stop-Tree $p.ProcessId "백엔드 창"
    }
    elseif ($cmd -like "*$BackendDir*" -and $cmd -like "*worker.py*") {
        Stop-Tree $p.ProcessId "작업 워커 창"
    }
    elseif ($cmd -like "*$FrontendDir*" -and $cmd -like "*npm run dev*") {
        Stop-Tree $p.ProcessId "프론트 창"
    }
}

Start-Sleep -Seconds 2

# ---- 2. 남은 포트 점유 프로세스 정리: 이 프로젝트 경로의 프로세스만 종료 ----
foreach ($port in @($BackendPort, $FrontendPort)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $c.OwningProcess) -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $inProject = ($proc.CommandLine -like "*$ProjectRoot*") -or ($proc.ExecutablePath -like "*$ProjectRoot*")
        if ($inProject) {
            Stop-Tree $proc.ProcessId ("포트 {0} 잔여 프로세스" -f $port)
        } else {
            Write-Warning ("포트 {0} 을(를) 다른 프로그램이 사용 중이라 건드리지 않았습니다. (PID {1}, {2})" -f $port, $proc.ProcessId, $proc.Name)
        }
    }
}

# ---- 3. 워커가 창 없이 남았는지 확인 (이 프로젝트의 worker.py 만) ----
$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
foreach ($w in $workers) {
    if ($w.CommandLine -like "*worker.py*" -and $w.CommandLine -like "*$BackendDir*") {
        Stop-Tree $w.ProcessId "작업 워커 잔여 프로세스"
    }
}

Start-Sleep -Seconds 1

# ---- 4. 결과 확인 ----
$still = @()
foreach ($port in @($BackendPort, $FrontendPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $still += $port }
}

Write-Host ""
if ($still.Count -eq 0) {
    Write-Host ("종료 완료: {0}개 프로세스 정리, 포트 {1}/{2} 해제됨" -f $stopped, $BackendPort, $FrontendPort) -ForegroundColor Cyan
} else {
    Write-Warning ("아직 사용 중인 포트: {0}  (다른 프로그램이거나 종료 중일 수 있습니다)" -f ($still -join ", "))
}
Write-Host "==========================================" -ForegroundColor Cyan