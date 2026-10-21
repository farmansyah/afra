@echo off
rem A.F.R.A installer for Windows: double-click this file. No administrator rights needed.
title Install A.F.R.A
echo.
echo   A.F.R.A installer
echo   Please wait, this takes about 2 minutes. You can leave this window open.
echo.
set "PS1=%~dp0docs\install.ps1"
if not exist "%PS1%" set "PS1=%~dp0install.ps1"
if exist "%PS1%" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://farmansyah.github.io/afra/install.ps1 | iex"
)
echo.
pause
