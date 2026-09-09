# start_seeddms.ps1 — 一键启动 SeedDMS（Docker 容器）
# 用途：开机后若伏羲「DMS 文档源」显示未连接，运行本脚本即可恢复。
# 流程：启动 Docker Desktop → 等待守护进程就绪 → 确保 seeddms 容器运行 → 验证可达 + 登录。
param(
    [string]$DockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe",
    [string]$ContainerName = "seeddms",
    [int]$Port = 8080,
    [int]$WaitDaemonSec = 180,
    [int]$WaitContainerSec = 60
)

$ErrorActionPreference = 'Continue'

# ---------- 辅助函数（须先于调用定义） ----------
function Test-SeedDms {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$Port/out/out.Login.php" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Pause-Read {
    if ($host.Name -like "*ConsoleHost*") {
        Write-Host ""
        Write-Host "按任意键退出..." -ForegroundColor DarkGray
        $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
}

# ---------- 开始 ----------
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "   SeedDMS 一键启动" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 0. 检查容器是否已在运行 ----------
Write-Host "[*] 检查容器状态..."
$already = docker ps --filter "name=$ContainerName" --format "{{.Names}}" 2>$null
if ($already -eq $ContainerName) {
    Write-Host "[OK] 容器 $ContainerName 已在运行。" -ForegroundColor Green
    & docker ps --filter "name=$ContainerName" --format "  -> {{.Names}} | {{.Status}} | {{.Ports}}"
    Write-Host ""
    Write-Host "[*] 验证 SeedDMS 是否可达..."
    if (Test-SeedDms) {
        Write-Host "[OK] SeedDMS 已就绪：http://localhost:$Port" -ForegroundColor Green
    } else {
        Write-Host "[!] SeedDMS 尚不可达，请稍后刷新重试。" -ForegroundColor Yellow
    }
    Pause-Read
    exit 0
}

# ---------- 1. 检查/启动 Docker Desktop ----------
Write-Host "[*] 检查 Docker 守护进程..."
$daemonOk = $false
docker info *> $null 2>&1
if ($LASTEXITCODE -eq 0) {
    $daemonOk = $true
    Write-Host "[OK] Docker 守护进程已在运行。" -ForegroundColor Green
} else {
    if (Test-Path $DockerExe) {
        Write-Host "[*] 未检测到 Docker 守护进程，启动 Docker Desktop ..." -ForegroundColor Yellow
        Start-Process -FilePath $DockerExe
        Write-Host "[*] 等待 Docker 守护进程就绪（最长 $WaitDaemonSec 秒）..."
        for ($i = 0; $i -lt $WaitDaemonSec; $i++) {
            docker info *> $null 2>&1
            if ($LASTEXITCODE -eq 0) { $daemonOk = $true; break }
            Start-Sleep -Seconds 2
        }
    } else {
        Write-Host "[X] 未找到 Docker Desktop 可执行文件：$DockerExe" -ForegroundColor Red
        Write-Host "    请确认 Docker Desktop 已安装，或修改本脚本的 -DockerExe 参数。" -ForegroundColor Red
        Pause-Read
        exit 1
    }
}

if (-not $daemonOk) {
    Write-Host "[X] Docker 守护进程在 $WaitDaemonSec 秒内未能就绪。" -ForegroundColor Red
    Write-Host "    请手动打开 Docker Desktop 等待其完成启动后再运行本脚本。" -ForegroundColor Red
    Pause-Read
    exit 1
}
Write-Host "[OK] Docker 守护进程就绪。" -ForegroundColor Green

# ---------- 2. 确保 seeddms 容器运行 ----------
Write-Host "[*] 检查容器 $ContainerName ..."
$exists = docker ps -a --filter "name=^/${ContainerName}$" --format "{{.Names}}" 2>$null
if ($exists -ne $ContainerName) {
    Write-Host "[X] 未找到容器 $ContainerName（可能名称不同或未创建过）。" -ForegroundColor Red
    Write-Host "    请运行：docker ps -a  查看实际容器名。" -ForegroundColor Red
    Pause-Read
    exit 1
}

$state = docker inspect --format "{{.State.Running}}" $ContainerName 2>$null
if ($state -eq "true") {
    Write-Host "[OK] 容器已在运行（restart 策略自动恢复）。" -ForegroundColor Green
} else {
    Write-Host "[*] 容器处于停止状态，正在启动 ..."
    docker start $ContainerName 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] 启动容器失败。" -ForegroundColor Red
        Pause-Read
        exit 1
    }
}

# ---------- 3. 等待容器健康/端口就绪 ----------
Write-Host "[*] 等待 SeedDMS 服务就绪（端口 $Port）..."
$ready = $false
for ($i = 0; $i -lt $WaitContainerSec; $i++) {
    $listening = netstat -ano | Select-String ":$Port" | Select-String "LISTENING"
    if ($listening) { $ready = $true; break }
    Start-Sleep -Seconds 2
}

if ($ready) {
    Write-Host "[OK] SeedDMS 已就绪：http://localhost:$Port" -ForegroundColor Green
    & docker ps --filter "name=$ContainerName" --format "  -> {{.Names}} | {{.Status}} | {{.Ports}}"
} else {
    Write-Host "[!] 端口 $Port 尚未监听，容器可能仍在初始化，请稍后刷新伏羲页面。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "完成。现在回到伏羲「DMS 文档源」页面点「刷新」即可。" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Pause-Read
