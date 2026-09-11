@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if exist "%PYTHONW%" goto validate
echo JustSpeak is preparing its Python environment...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto failed

:validate
"%PYTHON%" -c "import asr_client, PySide6, dashscope" >nul 2>&1
if errorlevel 1 (
    echo The environment is incomplete. Running setup again...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 goto failed
)

:launch
start "JustSpeak" "%PYTHONW%" "%~dp0scripts\launch.py"
endlocal
exit /b 0

:failed
echo.
echo JustSpeak setup failed. Review the message above, then press any key to close.
pause >nul
endlocal
exit /b 1
