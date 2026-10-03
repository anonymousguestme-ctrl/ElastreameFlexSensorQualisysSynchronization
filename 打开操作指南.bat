@echo off
setlocal
set "GUIDE_FILE=%~dp0guide.html"
if /I "%~1"=="--check" goto check
if not exist "%GUIDE_FILE%" goto missing
start "" "%GUIDE_FILE%"
exit /b 0

:check
if not exist "%GUIDE_FILE%" exit /b 2
echo OK
exit /b 0

:missing
echo The guide file is missing.
pause
exit /b 1
