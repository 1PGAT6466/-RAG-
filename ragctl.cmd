@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  伏羲 RAG 系统运维入口
::  用法：ragctl.cmd [start|stop|status|backup|logs|health|restart]
:: ============================================================

set ROOT=%~dp0
set PID_FILE=%ROOT%data\rag.pid
set LOG_DIR=%ROOT%data\logs

:: 创建日志目录
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: 解析命令
set CMD=%1
if "%CMD%"=="" goto :help

if "%CMD%"=="start" goto :start
if "%CMD%"=="stop" goto :stop
if "%CMD%"=="restart" goto :restart
if "%CMD%"=="status" goto :status
if "%CMD%"=="backup" goto :backup
if "%CMD%"=="logs" goto :logs
if "%CMD%"=="health" goto :health
goto :help

:start
echo [伏羲 RAG] 正在启动...
:: 检查是否已运行
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    tasklist /FI "PID eq !PID!" 2>nul | findstr /I "python" >nul
    if !errorlevel! equ 0 (
        echo [伏羲 RAG] 已在运行（PID: !PID!）
        goto :eof
    )
    del "%PID_FILE%"
)
:: 启动服务
cd /d "%ROOT%"
start /B pythonw server.py > "%LOG_DIR%\server.log" 2>&1
:: 等待启动
timeout /t 3 /nobreak >nul
:: 检查是否启动成功
powershell -Command "(Invoke-WebRequest -Uri 'http://127.0.0.1:8099/api/health' -TimeoutSec 5 -UseBasicParsing).StatusCode" > "%LOG_DIR%\health.tmp" 2>nul
set /p HEALTH=<"%LOG_DIR%\health.tmp"
if "%HEALTH%"=="200" (
    echo [伏羲 RAG] 启动成功
    echo [伏羲 RAG] 地址: http://127.0.0.1:8099
) else (
    echo [伏羲 RAG] 启动可能失败，请检查日志: %LOG_DIR%\server.log
)
del "%LOG_DIR%\health.tmp" 2>nul
goto :eof

:stop
echo [伏羲 RAG] 正在停止...
if not exist "%PID_FILE%" (
    echo [伏羲 RAG] 未在运行
    goto :eof
)
set /p PID=<"%PID_FILE%"
taskkill /PID !PID! /F >nul 2>&1
if !errorlevel! equ 0 (
    del "%PID_FILE%"
    echo [伏羲 RAG] 已停止
) else (
    echo [伏羲 RAG] 进程不存在或已停止
    del "%PID_FILE%"
)
goto :eof

:restart
call :stop
timeout /t 2 /nobreak >nul
call :start
goto :eof

:status
echo [伏羲 RAG] 状态检查...
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    tasklist /FI "PID eq !PID!" 2>nul | findstr /I "python" >nul
    if !errorlevel! equ 0 (
        echo   状态: 运行中（PID: !PID!）
    ) else (
        echo   状态: 僵尸（PID 文件存在但进程已死）
        del "%PID_FILE%"
    )
) else (
    echo   状态: 未运行
)
:: 检查数据库
if exist "%ROOT%data\rag.db" (
    for %%A in ("%ROOT%data\rag.db") do echo   数据库: %%~zA bytes
) else (
    echo   数据库: 不存在
)
:: 检查向量库
if exist "%ROOT%data\chroma" (
    echo   向量库: 存在
) else (
    echo   向量库: 不存在
)
goto :eof

:backup
echo [伏羲 RAG] 备份中...
set BACKUP_DIR=%ROOT%data\backup
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
set TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set BACKUP_FILE=%BACKUP_DIR%\rag_%TIMESTAMP%.db
if exist "%ROOT%data\rag.db" (
    copy "%ROOT%data\rag.db" "%BACKUP_FILE%" >nul
    echo   备份完成: %BACKUP_FILE%
) else (
    echo   数据库不存在，跳过
)
goto :eof

:logs
if exist "%LOG_DIR%\server.log" (
    echo [伏羲 RAG] 最近日志:
    powershell -Command "Get-Content '%LOG_DIR%\server.log' -Tail 50"
) else (
    echo [伏羲 RAG] 无日志文件
)
goto :eof

:health
echo [伏羲 RAG] 健康检查...
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8099/api/health' -TimeoutSec 5 -UseBasicParsing; Write-Host '  HTTP:' $r.StatusCode; $j = $r.Content | ConvertFrom-Json; Write-Host '  Status:' $j.status; Write-Host '  Files:' $j.files; Write-Host '  Chunks:' $j.chunks } catch { Write-Host '  不可达' }"
goto :eof

:help
echo.
echo   伏羲 RAG 系统运维入口
echo.
echo   用法: ragctl.cmd [命令]
echo.
echo   命令:
echo     start    启动服务
echo     stop     停止服务
echo     restart  重启服务
echo     status   查看状态
echo     backup   备份数据库
echo     logs     查看最近日志
echo     health   健康检查
echo.
goto :eof
