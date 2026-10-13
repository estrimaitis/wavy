@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  call setup.bat || (pause & exit /b 1)
)
set WAVY_OPEN_BROWSER=1
.venv\Scripts\python -m server.app
pause
