@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "SYS=%CD%\_System"
set "VENV=%SYS%\.venv"
set "RUNTIME=%CD%\Runtime\Current"

if exist "%VENV%\Scripts\python.exe" goto :deps

echo [AI Bridge] Preparing stable Python environment...
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv "%VENV%"
    goto :deps
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    python -m venv "%VENV%"
    goto :deps
  )
)

echo ERROR: Python 3.11 or newer was not found.
start "" "https://www.python.org/downloads/windows/"
exit /b 1

:deps
"%VENV%\Scripts\python.exe" -c "import fastapi,uvicorn,pydantic,httpx,websockets,pytest" >nul 2>nul
if not errorlevel 1 goto :ready

echo [AI Bridge] Installing runtime dependencies...
"%VENV%\Scripts\python.exe" -m pip install -e "%RUNTIME%[test]"
if not errorlevel 1 goto :ready

echo [AI Bridge] Package download failed. Retrying once without inherited proxy variables...
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="
"%VENV%\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -e "%RUNTIME%[test]"
if errorlevel 1 exit /b 1

:ready
exit /b 0
