@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "_System\bootstrap.bat"
if errorlevel 1 exit /b 1
"_System\.venv\Scripts\python.exe" "Tools\register_codex.py" --if-codex-installed
if errorlevel 1 exit /b 1
echo Bridge prepared. Read AI_INSTALL_GUIDE.md and run START_AI_BRIDGE.bat when ready.
