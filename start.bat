@echo off
title AFRA
cd /d "%~dp0"
if exist "%~dp0Install A.F.R.A (Windows).cmd" (
  echo Tip: for the easiest setup ^(runs in the background, Start menu icon^), close this window and double-click "Install A.F.R.A (Windows).cmd".
  echo Or just wait: this window starts A.F.R.A directly.
  echo.
)
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
