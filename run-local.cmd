@echo off
rem Double-click to open the news agent page on this PC: http://localhost:8000
rem Close this window or press Ctrl+C to stop. Example: run-local.cmd --every 180
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto novenv
".venv\Scripts\python.exe" -m agent.serve %*
pause
exit /b

:novenv
echo [!] .venv not found. Do step 1 of README "Run on this PC" first (create the virtual environment).
pause
exit /b 1
