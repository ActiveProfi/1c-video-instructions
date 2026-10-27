@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0tools\Start-Studio.ps1"
if errorlevel 1 pause
