@echo off
setlocal EnableDelayedExpansion
:: ============================================================
::  伏羲 RAG — Windows 守护进程安装脚本（#17，2026-09-21）
::  作用：用 NSSM 把 server.py 注册为 Windows 服务，实现崩溃自动拉起。
::
::  前置条件：
::    1. 已安装 NSSM（https://nssm.cc/download），并把 nssm.exe 放到 PATH
::       或修改下方 NSSM 变量为绝对路径
::    2. 已确认 python 路径（本脚本用 python 命令探测）
::
::  用法（需管理员权限的 cmd）：
::    install_service.cmd          安装并启动服务
::    install_service.cmd remove   卸载服务
:: ============================================================

set ROOT=%~dp0..
set SERVICE_NAME=FuxiRAG
set NSSM=nssm.exe

where %NSSM% >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 nssm.exe，请先安装 NSSM 并加入 PATH
    echo        下载：https://nssm.cc/download
    exit /b 1
)

if /i "%1"=="remove" (
    echo [伏羲 RAG] 正在卸载服务 %SERVICE_NAME% ...
    %NSSM% stop %SERVICE_NAME%
    %NSSM% remove %SERVICE_NAME% confirm
    echo [伏羲 RAG] 已卸载
    exit /b 0
)

:: 解析 python 绝对路径
for /f "delims=" %%i in ('where python') do (
    set PYEXE=%%i
    goto :found
)
:found
if "%PYEXE%"=="" (
    echo [错误] 未找到 python
    exit /b 1
)

echo [伏羲 RAG] 注册服务 %SERVICE_NAME%
echo   python : %PYEXE%
echo   工作目录: %ROOT%

%NSSM% install %SERVICE_NAME% "%PYEXE%" "server.py"
%NSSM% set %SERVICE_NAME% AppDirectory "%ROOT%"
%NSSM% set %SERVICE_NAME% DisplayName "Fuxi RAG Server"
%NSSM% set %SERVICE_NAME% Description "伏羲 RAG 知识库系统（FastAPI 单进程 + SQLite 单写者）"
:: 崩溃自动重启（延迟 5s，防止快速循环重启）
%NSSM% set %SERVICE_NAME% AppExit Default Restart
%NSSM% set %SERVICE_NAME% AppRestartDelay 5000
:: 日志
%NSSM% set %SERVICE_NAME% AppStdout "%ROOT%\data\logs\service_out.log"
%NSSM% set %SERVICE_NAME% AppStderr "%ROOT%\data\logs\service_err.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760
:: 启动类型：自动，但不随系统启动立即拉起（建议手动/延迟）
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START

echo [伏羲 RAG] 启动服务...
%NSSM% start %SERVICE_NAME%

echo.
echo [伏羲 RAG] 完成。服务名：%SERVICE_NAME%
echo   查看状态: nssm status %SERVICE_NAME%
echo   停止服务: nssm stop %SERVICE_NAME%
echo   卸载服务: install_service.cmd remove
endlocal
