@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-CS-Scout.ps1"
set "CS_SCOUT_EXIT=%ERRORLEVEL%"
echo.
if not "%CS_SCOUT_EXIT%"=="0" (
  echo Installation failed. See the message above.
) else (
  echo Installation completed successfully.
)
pause
exit /b %CS_SCOUT_EXIT%
