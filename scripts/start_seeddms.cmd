@echo off
title Start SeedDMS (Docker)
cd /d E:\更新RAG框架

echo ==============================================
echo    SeedDMS 一键启动
echo ==============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "E:\更新RAG框架\scripts\start_seeddms.ps1"

echo.
echo [*] 脚本执行完毕。
pause
