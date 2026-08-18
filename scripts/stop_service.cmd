@echo off
title Stop FuXi RAG Service
cd /d E:\??RAG??

echo ============================================
echo    FuXi RAG - Stop Service
echo ============================================
echo.

set killed=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8099" ^| findstr "LISTENING"') do call :kill %%a
goto :done

:kill
taskkill /f /pid %1 >nul 2>&1
set killed=1
goto :eof

:done
if "%killed%"=="1" (
  echo [OK] Service stopped.
) else (
  echo [!] Port 8099 not listening. Service may not be running.
)
echo.
pause
