@echo off
chcp 65001 >nul
title 伏羲 RAG - 停止服务
cd /d "%~dp0.."

echo ============================================
echo    伏羲 RAG - 停止服务
echo ============================================
echo.

python scripts\ragctl.py stop

echo.
pause
