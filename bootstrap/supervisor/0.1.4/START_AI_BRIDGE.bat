@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AI Bridge Supervisor

echo ==========================================
echo AI Bridge
echo START entry - keep this file at the root
echo ==========================================
echo.
call "_System\bootstrap.bat"
if errorlevel 1 goto :fail
"_System\.venv\Scripts\python.exe" "_System\supervisor.py" run
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo AI Bridge failed to start. This window is intentionally kept open.
pause
exit /b 1
