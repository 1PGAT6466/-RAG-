@echo off
chcp 65001 >nul
title 伏羲 RAG - 启动服务
cd /d "%~dp0.."

echo ============================================
echo    伏羲 RAG - 启动服务
echo ============================================
echo.

python scripts\ragctl.py start
if errorlevel 1 (
  echo.
  echo [!] 启动失败。排查建议：
  echo     1. python scripts\ragctl.py logs 100     ^<- 看启动日志
  echo     2. python scripts\ragctl.py doctor        ^<- 全链路体检
  echo.
  pause
  exit /b 1
)

echo.
echo [OK] 服务已启动。
echo     本机:  http://127.0.0.1:8099
echo     内网:  http://172.25.30.11:8099
echo.
echo     查看状态: python scripts\ragctl.py status
echo     停止服务: 双击 "停止伏羲服务" 或 python scripts\ragctl.py stop
echo.
pause
