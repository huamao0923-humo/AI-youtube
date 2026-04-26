# AI 頻道本機排程器 — NSSM 安裝腳本
#
# 用途：把 scheduler.py 註冊為 Windows 服務（開機自動啟動），
#       讓需要 GPU/FFmpeg 的影片合成排程能在本機 24h 待命。
#
# 雲端不需 GPU 的爬蟲/評分/Brief 走 Railway worker（Procfile 的 worker:）；
# 本機這個服務只負責 SCHEDULER_MODE=local 的 jobs（目前是 14:00 影片合成上傳）。
#
# 前置需求：
#   1. 安裝 NSSM（https://nssm.cc/download）並解壓到 PATH 內，或調整 $NssmPath
#   2. 確認 Python 路徑（含必要 pip 套件）
#   3. 以「系統管理員」身分執行 PowerShell
#
# 使用：
#   PS> .\scripts\install_scheduler_service.ps1
#   PS> .\scripts\install_scheduler_service.ps1 -Uninstall   # 移除服務
#
# 服務管理：
#   nssm start/stop/restart AIChannelScheduler
#   nssm edit AIChannelScheduler        # GUI 修改
#   sc query AIChannelScheduler          # 查狀態

[CmdletBinding()]
param(
    [string]$ServiceName = "AIChannelScheduler",
    [string]$NssmPath    = "nssm",                                                            # 預設依靠 PATH
    [string]$ProjectRoot = "F:\claude project\every project\AI youtube channel",
    [string]$PythonPath  = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Write-Host "→ 停止並移除服務 $ServiceName" -ForegroundColor Yellow
    & $NssmPath stop $ServiceName 2>$null
    & $NssmPath remove $ServiceName confirm
    Write-Host "✓ 已移除" -ForegroundColor Green
    exit 0
}

# 驗證
if (-not (Test-Path $PythonPath)) {
    throw "找不到 Python：$PythonPath（請用 -PythonPath 指定，或調整預設值）"
}
if (-not (Test-Path $ProjectRoot)) {
    throw "找不到專案路徑：$ProjectRoot"
}
$SchedulerScript = Join-Path $ProjectRoot "scheduler.py"
if (-not (Test-Path $SchedulerScript)) {
    throw "找不到 scheduler.py：$SchedulerScript"
}
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Write-Host "→ 安裝 NSSM 服務 $ServiceName" -ForegroundColor Cyan
Write-Host "  Python:    $PythonPath"
Write-Host "  Scheduler: $SchedulerScript"
Write-Host "  Mode:      local（只跑需 GPU/FFmpeg 的合成上傳任務）"

# 安裝
& $NssmPath install $ServiceName $PythonPath $SchedulerScript

# 工作目錄、環境變數、log
& $NssmPath set $ServiceName AppDirectory $ProjectRoot
& $NssmPath set $ServiceName AppEnvironmentExtra "SCHEDULER_MODE=local" "PYTHONUNBUFFERED=1" "PYTHONIOENCODING=utf-8"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout (Join-Path $LogDir "scheduler.out.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $LogDir "scheduler.err.log")
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 10485760   # 10 MB 滾動
& $NssmPath set $ServiceName Description "AI 頻道本機排程器 — 14:00 影片合成上傳"

# 自動失敗重啟
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 10000     # 10 秒後重啟

Write-Host ""
Write-Host "→ 啟動服務" -ForegroundColor Cyan
& $NssmPath start $ServiceName
Start-Sleep -Seconds 2
$status = (Get-Service -Name $ServiceName).Status
Write-Host "✓ 服務狀態：$status" -ForegroundColor Green
Write-Host ""
Write-Host "Log 路徑：" -ForegroundColor Yellow
Write-Host "  out → $LogDir\scheduler.out.log"
Write-Host "  err → $LogDir\scheduler.err.log"
Write-Host ""
Write-Host "管理指令：" -ForegroundColor Yellow
Write-Host "  nssm restart $ServiceName"
Write-Host "  nssm stop    $ServiceName"
Write-Host "  nssm edit    $ServiceName     # GUI 修改"
Write-Host "  .\scripts\install_scheduler_service.ps1 -Uninstall"
