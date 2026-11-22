@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-Diagnostics.ps1"
echo.
echo Send the generated cs-scout-diagnostics-*.txt file back for analysis.
echo Review it first: tokens are redacted, but you own the final check.
pause
