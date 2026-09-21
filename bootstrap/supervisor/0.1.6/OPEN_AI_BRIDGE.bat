@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "_System\.venv\Scripts\python.exe" (
  echo AI Bridge is not initialized. Run START_AI_BRIDGE.bat first.
  pause
  exit /b 1
)
"_System\.venv\Scripts\python.exe" "_System\supervisor.py" open
if errorlevel 1 pause
