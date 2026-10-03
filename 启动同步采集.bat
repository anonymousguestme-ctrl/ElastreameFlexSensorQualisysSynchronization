@echo off
setlocal
set "APP_DIR=%~dp0"
if /I "%~1"=="--check" goto check
if not exist "%APP_DIR%.venv-local\Scripts\pythonw.exe" goto missing
start "" /D "%APP_DIR%" "%APP_DIR%.venv-local\Scripts\pythonw.exe" "%APP_DIR%sync_gui.py"
exit /b 0

:check
if not exist "%APP_DIR%.venv-local\Scripts\pythonw.exe" exit /b 2
if not exist "%APP_DIR%sync_gui.py" exit /b 3
echo OK
exit /b 0

:missing
echo Python environment is missing. Run install.ps1 first.
pause
exit /b 1
