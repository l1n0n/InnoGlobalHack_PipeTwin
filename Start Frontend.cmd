@echo off
setlocal
cd /d "%~dp0"
set "PIPETWIN_PYTHON=%~dp0.venv-demo\Scripts\python.exe"
if not exist "%PIPETWIN_PYTHON%" (
    echo Demo environment was not found. See README.md for setup.
    pause
    exit /b 1
)
echo Frontend: http://127.0.0.1:5173
"%PIPETWIN_PYTHON%" -m http.server 5173 --bind 127.0.0.1 --directory frontend
if errorlevel 1 pause
