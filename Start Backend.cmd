@echo off
setlocal
cd /d "%~dp0"
set "PIPETWIN_PYTHON=%~dp0.venv-demo\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import fastapi, uvicorn; import importlib.util; raise SystemExit(importlib.util.find_spec('neqsim') is None)" >nul 2>&1
    if not errorlevel 1 set "PIPETWIN_PYTHON=%~dp0.venv\Scripts\python.exe"
)
if not defined JAVA_HOME (
    for /d %%J in ("%USERPROFILE%\.jdks\*21*") do (
        if exist "%%~fJ\bin\server\jvm.dll" set "JAVA_HOME=%%~fJ"
    )
)
if not exist "%PIPETWIN_PYTHON%" (
    echo Demo environment was not found. See README.md for setup.
    pause
    exit /b 1
)
echo Backend: http://127.0.0.1:8000/docs
"%PIPETWIN_PYTHON%" -m uvicorn backend.web_api:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
