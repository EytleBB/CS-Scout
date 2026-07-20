@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Copy-Access-Key.ps1"
set "CS_SCOUT_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %CS_SCOUT_EXIT%
