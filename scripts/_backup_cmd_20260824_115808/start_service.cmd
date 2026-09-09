@echo off
title FuXi RAG Service
cd /d E:\??RAG??

echo ============================================
echo    FuXi RAG - Start Service
echo ============================================
echo.

netstat -ano | findstr ":8099" | findstr "LISTENING" >nul
if errorlevel 1 goto run

echo [!] Port 8099 is already in use. Service may be running.
echo     To restart, run "Stop FuXi Service" on your desktop first.
echo.
pause
exit

:run
echo [*] Starting service...
echo     Intranet: http://172.25.30.11:8099
echo     Local:    http://127.0.0.1:8099
echo.
echo     Close this window (or press Ctrl+C) to stop the service.
echo ============================================
echo.

"C:\Users\feng-shaoxuan\AppData\Local\easyclaw\ai\tool_cache\resources\tools\win\python-3.11.9\python.exe" server.py

echo.
echo [*] Service stopped.
pause
