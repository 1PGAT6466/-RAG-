# fuxi_launch.ps1 — 伏羲知识库一键启动/联动关闭
# 双击桌面快捷方式执行本脚本：
#   1. 若后端未运行，后台启动 server.py
#   2. 等待健康检查通过
#   3. 以独立应用窗口（--app + 独立 user-data-dir）打开浏览器访问伏羲
#   4. 监控独立浏览器实例：关闭窗口后自动停止后端进程（联动关闭）
param(
    [int]$Port = 8099,
    [string]$Url = "http://127.0.0.1:8099",
    [string]$ServerDir = "E:\更新RAG框架",
    [string]$Python = "C:\Users\feng-shaoxuan\AppData\Local\easyclaw\ai\tool_cache\resources\tools\win\python-3.11.9\python.exe",
    [string]$Browser = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    [string]$ProfileDir = "E:\更新RAG框架\data\edge_profile_fuxi"
)

$ErrorActionPreference = 'Stop'
Set-Location $ServerDir

# ---------- 1. 启动后端（若未运行） ----------
$listening = netstat -ano | Select-String ":$Port" | Select-String "LISTENING"
if (-not $listening) {
    Write-Host "[*] 后端未运行，启动 server.py ..."
    $proc = Start-Process -FilePath $Python -ArgumentList "server.py" -WorkingDirectory $ServerDir -WindowStyle Hidden -PassThru
    $startedNew = $true
    $backendPid = $proc.Id
} else {
    Write-Host "[*] 后端已在运行（端口 $Port），复用现有服务（关闭浏览器不停止它）。"
    $startedNew = $false
    $backendPid = $null
}

# ---------- 2. 等待健康检查通过 ----------
Write-Host "[*] 等待服务就绪..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 1000 }
}
if (-not $ready) {
    Write-Host "[!] 服务未能在 60 秒内就绪，仍尝试打开浏览器。"
}

# ---------- 3. 打开浏览器（独立 user-data-dir 实例，关闭窗口即进程树退出） ----------
Write-Host "[*] 打开浏览器访问 $Url ..."
Start-Process -FilePath $Browser -ArgumentList "--app=`"$Url`"","--user-data-dir=`"$ProfileDir`"","--no-first-run","--no-default-browser-check"

# ---------- 4. 监控独立浏览器实例，关闭后联动停止后端 ----------
# 通过命令行含 ProfileDir 精确识别伏羲专属的 Edge 进程树。
# 关闭 app 窗口 → 该独立实例主进程退出 → 整个进程树退出。
Write-Host "[*] 浏览器已打开。关闭浏览器窗口后将自动停止后端..."
# 等待浏览器进程树出现（启动有延迟）
Start-Sleep -Seconds 2
$found = $false
for ($i = 0; $i -lt 30; $i++) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$ProfileDir*" })
    if ($procs.Count -gt 0) { $found = $true; break }
    Start-Sleep -Seconds 1
}

if ($found) {
    # 监控：独立实例进程是否全部退出
    while ($true) {
        Start-Sleep -Seconds 2
        $alive = @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$ProfileDir*" })
        if ($alive.Count -eq 0) {
            Write-Host "[*] 浏览器窗口已关闭。"
            break
        }
    }
} else {
    Write-Host "[!] 未检测到独立浏览器进程，等待 60 秒后按超时处理。"
    Start-Sleep -Seconds 60
}

# ---------- 5. 停止后端（仅当本次由本脚本启动时） ----------
if ($startedNew -and $backendPid) {
    Write-Host "[*] 停止后端进程 (PID $backendPid) ..."
    Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Get-Process -Id $backendPid -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] 后端已停止。"
} else {
    Write-Host "[*] 后端由外部启动，保持运行（不自动停止）。"
}
