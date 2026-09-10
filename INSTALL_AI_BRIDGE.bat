@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_AI_BRIDGE.ps1" %*
if errorlevel 1 (
  echo.
  echo AI Bridge installation failed.
  pause
  exit /b 1
)
