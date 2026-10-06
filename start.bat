@echo off
title AFRA
cd /d "%~dp0"
where python >nul 2>nul || (
  echo.
  echo A.F.R.A needs Python ^(free^). The download page is opening now.
  echo Install it and TICK "Add python.exe to PATH" on the first screen,
  echo then double-click start.bat again.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  python -m venv .venv || (echo Failed to create venv & pause & exit /b 1)
)
echo [2/2] Checking dependencies...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo Dependency install failed & pause & exit /b 1)
if exist "%USERPROFILE%\Desktop\Research Tools.lnk" del "%USERPROFILE%\Desktop\Research Tools.lnk" >nul 2>nul
if not exist "%USERPROFILE%\Desktop\AFRA.lnk" (
  powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\AFRA.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()" >nul 2>nul
  echo Created an "AFRA" shortcut on your Desktop.
)
echo.
echo A.F.R.A is opening in your browser. Keep this window open while you work.
".venv\Scripts\python.exe" run.py %*
pause
