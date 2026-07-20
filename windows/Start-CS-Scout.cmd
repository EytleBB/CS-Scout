@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-CS-Scout.ps1"
set "CS_SCOUT_EXIT=%ERRORLEVEL%"
echo.
if not "%CS_SCOUT_EXIT%"=="0" echo CS-Scout stopped with error code %CS_SCOUT_EXIT%.
pause
exit /b %CS_SCOUT_EXIT%
