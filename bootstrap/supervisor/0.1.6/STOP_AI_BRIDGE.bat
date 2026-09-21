@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "_System\.venv\Scripts\python.exe" (
  echo AI Bridge environment has not been initialized yet.
  pause
  exit /b 0
)
"_System\.venv\Scripts\python.exe" "_System\supervisor.py" stop
pause
