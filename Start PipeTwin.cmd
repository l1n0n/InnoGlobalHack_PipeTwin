@echo off
setlocal
cd /d "%~dp0"
set "PIPETWIN_PYTHON=%~dp0.venv-demo\Scripts\python.exe"

if not exist "%PIPETWIN_PYTHON%" (
    echo Demo environment was not found. See README.md for setup.
    pause
    exit /b 1
)

echo Open http://127.0.0.1:8501 in your browser.
"%PIPETWIN_PYTHON%" -m uvicorn backend.web_api:app --host 127.0.0.1 --port 8501
if errorlevel 1 (
    echo PipeTwin could not start. See the error above and README.md for setup.
    pause
    exit /b 1
)
