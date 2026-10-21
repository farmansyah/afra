# A.F.R.A installer for Windows. Open PowerShell and paste this one line:
#
#   powershell -ExecutionPolicy Bypass -c "irm https://farmansyah.github.io/afra/install.ps1 | iex"
#
# Or just double-click "Install A.F.R.A (Windows).cmd". No administrator rights are needed.
# Installs into %LOCALAPPDATA%\AFRA, runs quietly in the background (starts at sign-in), adds A.F.R.A to the
# Start menu and Desktop, keeps itself up to date and connects to the Claude app.
# Running it again repairs or updates the installation. Your data (%LOCALAPPDATA%\AFRA\data) is never touched.
# Remove:  powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://farmansyah.github.io/afra/install.ps1))) -Uninstall"
# (c) 2026 danafarmansyah. All rights reserved. Crafted with love.
#
# Switches for testing only: -Dir -Port -ZipPath -NoTask -NoShortcuts -NoClaude -NoMigrate -NoOpen -SearchRoot
#                            -TaskName -ForcePrivatePython
param(
  [switch]$Uninstall,
  [string]$Dir = (Join-Path $env:LOCALAPPDATA 'AFRA'),
  [int]$Port = 8778,
  [string]$ZipPath = '',
  [switch]$NoTask,
  [switch]$NoShortcuts,
  [switch]$NoClaude,
  [switch]$NoMigrate,
  [switch]$NoOpen,
  [string[]]$SearchRoot = @(),
  [string]$TaskName = 'A.F.R.A',
  [switch]$ForcePrivatePython
)

$ZIP = 'https://codeload.github.com/farmansyah/afra/zip/refs/heads/main'
$URL = "http://localhost:$Port"
$ONELINE = 'powershell -ExecutionPolicy Bypass -c "irm https://farmansyah.github.io/afra/install.ps1 | iex"'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch { }

function Say([string]$m) { Write-Host $m }
function Step([string]$m) { Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Note([string]$m) { Write-Host "    $m" -ForegroundColor DarkGray }
function Stop-Here([string]$m) { throw [System.Exception]$m }

# Run a program, keep its output, never let its messages turn into PowerShell errors. Returns the exit code.
function Invoke-Quiet([string]$exe, [string[]]$arguments) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $script:LastOutput = (& $exe @arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
    return $LASTEXITCODE
  } catch {
    $script:LastOutput = "$_"
    return 1
  } finally { $ErrorActionPreference = $old }
}

function Test-Afra {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/version" -TimeoutSec 2
    return ($r.StatusCode -eq 200)
  } catch { return $false }
}

function Wait-Afra([int]$seconds) {
  for ($i = 0; $i -lt $seconds; $i++) {
    if (Test-Afra) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Test-Inside([string]$path, [string]$folder) {
  if (-not $path) { return $false }
  return $path.StartsWith($folder.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

# Stop the A.F.R.A that lives in THIS folder (its scheduled task and its python processes). Nothing else is touched.
function Stop-Own {
  try {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
      $act = $t.Actions | Select-Object -First 1
      if ((Test-Inside $act.Execute $Dir) -or ([string]$act.Arguments).IndexOf($Dir.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      }
    }
  } catch { }
  $stopped = $false
  try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='uv.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
      if ($p.ProcessId -eq $PID) { continue }
      $cmd = [string]$p.CommandLine
      if ((Test-Inside $p.ExecutablePath $Dir) -or ($cmd.IndexOf($Dir.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) -ge 0)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped = $true
      }
    }
  } catch { }
  if ($stopped) { Start-Sleep -Seconds 2 }
}

function Get-Shortcuts {
  $list = @()
  try { $list += Join-Path ([Environment]::GetFolderPath('Programs')) "$TaskName.lnk" } catch { }
  try { $list += Join-Path ([Environment]::GetFolderPath('Desktop')) "$TaskName.lnk" } catch { }
  try { $list += Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.lnk" } catch { }
  return $list
}

function Remove-Everything {
  Step 'Removing A.F.R.A'
  $full = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
  if (($full.Length -le 3) -or ($full -ieq $env:USERPROFILE) -or ($full -ieq $env:LOCALAPPDATA)) { Stop-Here "Refusing to remove $full." }
  $known = (Test-Path (Join-Path $Dir 'run.py')) -or (Test-Path (Join-Path $Dir 'data'))
  Stop-Own
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }
  foreach ($s in (Get-Shortcuts)) { Remove-Item -LiteralPath $s -Force -ErrorAction SilentlyContinue }
  if (-not $known) { Say 'A.F.R.A is not installed here, nothing else to remove.'; return }
  $data = Join-Path $Dir 'data'
  foreach ($item in (Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue)) {
    if ($item.Name -ieq 'data') { continue }
    Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
  }
  if (Test-Path $data) {
    Say ''
    Say 'A.F.R.A is removed.'
    Say "Your library and documents are still in $data"
    Say '(delete that folder too if you want them gone as well).'
  } else {
    Remove-Item -LiteralPath $Dir -Recurse -Force -ErrorAction SilentlyContinue
    Say ''
    Say 'A.F.R.A is removed.'
  }
}

# A real Python 3.10+ (the empty "python.exe" in WindowsApps only opens the Microsoft Store). Returns its path or $null.
function Test-Python([string]$exe, [string[]]$pre = @()) {
  try {
    $code = 'import sys; print(sys.executable) if sys.version_info >= (3, 10) else None'
    $rc = Invoke-Quiet $exe ($pre + @('-c', $code))
    if ($rc -eq 0) {
      $line = ($script:LastOutput -split "`n" | Select-Object -Last 1).Trim()
      if ($line -and (Test-Path -LiteralPath $line)) { return $line }
    }
  } catch { }
  return $null
}

function Find-Python {
  $py = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($py -and -not ($py.Source -match '\\WindowsApps\\' -and (Get-Item -LiteralPath $py.Source -Force).Length -eq 0)) {
    $r = Test-Python $py.Source @('-3'); if ($r) { return $r }
  }
  foreach ($name in @('python.exe', 'python3.exe')) {
    foreach ($c in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
      $src = [string]$c.Source
      if (-not $src) { continue }
      if ($src -match '\\WindowsApps\\') {
        $len = 0
        try { $len = (Get-Item -LiteralPath $src -Force).Length } catch { }
        if ($len -eq 0) { continue }  # the Microsoft Store shortcut, not a real Python
      }
      $r = Test-Python $src
      if ($r) { return $r }
    }
  }
  $folders = @()
  foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)) {
    if ($root -and (Test-Path $root)) { $folders += @(Get-ChildItem -Path $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending) }
  }
  foreach ($f in $folders) {
    $exe = Join-Path $f.FullName 'python.exe'
    if (Test-Path $exe) { $r = Test-Python $exe; if ($r) { return $r } }
  }
  return $null
}

function New-Venv {
  # 1. an existing Python, 2. otherwise a private copy through uv (no installer windows, no admin rights)
  $created = $false
  if (-not $ForcePrivatePython) {
    $py = Find-Python
    if ($py) {
      Note "Using the Python already on this computer ($py)."
      if ((Invoke-Quiet $py @('-m', 'venv', '.venv')) -eq 0 -and (Test-Path '.venv\Scripts\python.exe')) { $created = $true }
      else { Note 'That Python could not make a private environment, so A.F.R.A will use its own.'; Remove-Item -LiteralPath '.venv' -Recurse -Force -ErrorAction SilentlyContinue }
    } else {
      Note 'No recent Python found on this computer.'
    }
  }
  if (-not $created) {
    Say 'Downloading a private copy of Python for A.F.R.A (about 40 MB, one time)...'
    $uvDir = Join-Path $Dir '.uv'
    $uv = Join-Path $uvDir 'uv.exe'
    if (-not (Test-Path $uv)) {
      $env:UV_UNMANAGED_INSTALL = $uvDir
      $rc = Invoke-Quiet 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://astral.sh/uv/install.ps1 | iex")
      Remove-Item Env:\UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
      if ($rc -ne 0 -or -not (Test-Path $uv)) { Stop-Here 'Could not download Python. Check the internet connection and run the installer again.' }
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $uvDir 'python'
    $env:UV_CACHE_DIR = Join-Path $uvDir 'cache'
    $rc = Invoke-Quiet $uv @('venv', '--seed', '--quiet', '--python', '3.12', '.venv')
    Remove-Item Env:\UV_PYTHON_INSTALL_DIR, Env:\UV_CACHE_DIR -ErrorAction SilentlyContinue
    if ($rc -ne 0 -or -not (Test-Path '.venv\Scripts\python.exe')) { Stop-Here 'Could not set up Python. Check the internet connection and run the installer again.' }
  }
}

function Copy-Library {
  if ($NoMigrate) { return }
  if (Test-Path (Join-Path $Dir 'data\research.db')) { return }
  $roots = @()
  if ($SearchRoot.Count -gt 0) { $roots = $SearchRoot }
  else {
    $roots += Join-Path $env:USERPROFILE 'Downloads'
    try { $roots += [Environment]::GetFolderPath('MyDocuments') } catch { }
    try { $roots += [Environment]::GetFolderPath('Desktop') } catch { }
    $roots += Join-Path $env:USERPROFILE 'Documents'
    $roots += Join-Path $env:USERPROFILE 'Desktop'
  }
  $best = $null
  foreach ($r in ($roots | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    try {
      $hits = Get-ChildItem -LiteralPath $r -Recurse -Depth 4 -Filter 'research.db' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -ieq 'data' -and -not (Test-Inside $_.FullName $Dir) }
      foreach ($h in $hits) { if ((-not $best) -or ($h.LastWriteTime -gt $best.LastWriteTime)) { $best = $h } }
    } catch { }
  }
  if ($best) {
    $old = $best.Directory.FullName
    Step "Copying your existing library from $($best.Directory.Parent.FullName)"
    New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'data') | Out-Null
    Copy-Item -Path (Join-Path $old '*') -Destination (Join-Path $Dir 'data') -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# Square app icon from static\icon-512.png, written as a PNG-in-ICO file with built-in Windows parts only.
function New-Icon([string]$png, [string]$ico) {
  try {
    Add-Type -AssemblyName System.Drawing
    $src = [System.Drawing.Image]::FromFile($png)
    $bmp = New-Object System.Drawing.Bitmap 256, 256
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($src, 0, 0, 256, 256)
    $g.Dispose(); $src.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $data = $ms.ToArray(); $ms.Dispose()
    $fs = [System.IO.File]::Create($ico)
    $w = New-Object System.IO.BinaryWriter $fs
    $w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]1)
    $w.Write([byte]0); $w.Write([byte]0); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([uint16]1); $w.Write([uint16]32)
    $w.Write([uint32]$data.Length); $w.Write([uint32]22)
    $w.Write($data)
    $w.Close(); $fs.Close()
    return $true
  } catch { return $false }
}

# The small program the shortcuts run: open A.F.R.A, starting it first when nothing answers. wscript shows no console window.
function New-Opener([string]$pythonw) {
  $vbs = @'
Option Explicit
Dim sh, url, dir, task, pyw, rc, i
Set sh = CreateObject("WScript.Shell")
url = "__URL__"
dir = "__DIR__"
task = "__TASK__"
pyw = "__PYW__"
Function Up()
  Dim h
  On Error Resume Next
  Set h = CreateObject("MSXML2.XMLHTTP")
  h.open "GET", "http://127.0.0.1:__PORT__/api/version", False
  h.send
  Up = (Err.Number = 0 And h.Status = 200)
  On Error GoTo 0
End Function
If Not Up() Then
  rc = 1
  If task <> "" Then rc = sh.Run("schtasks /run /tn """ & task & """", 0, True)
  If rc <> 0 Then
    sh.CurrentDirectory = dir
    sh.Run """" & pyw & """ """ & dir & "\run.py"" --no-browser --port __PORT__", 0, False
  End If
  For i = 1 To 90
    If Up() Then Exit For
    WScript.Sleep 1000
  Next
End If
If Up() Then
  sh.Run url
Else
  MsgBox "A.F.R.A could not start. Please run the installer again.", 48, "A.F.R.A"
End If
'@
  $t = if ($script:UseTask) { $TaskName } else { '' }
  $vbs = $vbs.Replace('__URL__', $URL).Replace('__DIR__', $Dir).Replace('__TASK__', $t).Replace('__PYW__', $pythonw).Replace('__PORT__', "$Port")
  $path = Join-Path $Dir 'open-afra.vbs'
  [System.IO.File]::WriteAllText($path, ($vbs -replace "`r?`n", "`r`n"), [System.Text.Encoding]::ASCII)
  return $path
}

function New-Link([string]$lnk, [string]$target, [string]$arguments, [string]$icon) {
  $sh = New-Object -ComObject WScript.Shell
  $s = $sh.CreateShortcut($lnk)
  $s.TargetPath = $target
  $s.Arguments = $arguments
  $s.WorkingDirectory = $Dir
  $s.Description = 'A.F.R.A: Article Finder & Research Assistant'
  if ($icon -and (Test-Path $icon)) { $s.IconLocation = "$icon,0" }
  $s.Save()
}

function Register-Autostart([string]$pythonw) {
  $runPy = Join-Path $Dir 'run.py'
  $taskArgs = "`"$runPy`" --no-browser --port $Port"
  try {
    $me = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $taskArgs -WorkingDirectory $Dir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
      -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
      -Description 'Starts A.F.R.A in the background when you sign in.' -Force -ErrorAction Stop | Out-Null
    return $true
  } catch {
    Note "Could not create the background task ($($_.Exception.Message.Trim())). Using the Startup folder instead."
    return $false
  }
}

function Install-Afra {
  Say ''
  Say 'A.F.R.A: Article Finder & Research Assistant'
  Say "Installing into $Dir (about 2 minutes)..."

  # 1. stop a copy of A.F.R.A from this folder that is already running, so files and port are free
  Stop-Own

  # 2. the program itself (data and .venv are kept)
  Step 'Downloading A.F.R.A'
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('afra-setup-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  try {
    $zip = Join-Path $tmp 'afra.zip'
    if ($ZipPath) {
      if (-not (Test-Path -LiteralPath $ZipPath)) { Stop-Here "The file $ZipPath was not found." }
      Copy-Item -LiteralPath $ZipPath -Destination $zip
    } else {
      try { Invoke-WebRequest -UseBasicParsing -Uri $ZIP -OutFile $zip } catch { Stop-Here 'Download failed. Check the internet connection and run the installer again.' }
    }
    try { Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $tmp 'src') -Force } catch { Stop-Here 'The download is damaged. Run the installer again.' }
    $src = Get-ChildItem -LiteralPath (Join-Path $tmp 'src') -Directory | Select-Object -First 1
    if (-not $src -or -not (Test-Path (Join-Path $src.FullName 'run.py'))) { Stop-Here 'The download does not look like A.F.R.A. Run the installer again.' }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $null = & robocopy $src.FullName $Dir /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD (Join-Path $src.FullName 'data') (Join-Path $src.FullName '.venv') (Join-Path $src.FullName '.git')
    if ($LASTEXITCODE -ge 8) { Stop-Here "Could not copy the files into $Dir. Close A.F.R.A and run the installer again." }
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }

  # 3. bring over the library from a copy that was unzipped somewhere else before
  Copy-Library
  New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'data') | Out-Null

  # 4. Python and the components A.F.R.A needs
  Step 'Preparing Python'
  Set-Location -LiteralPath $Dir
  $venvOk = $false
  if ((Test-Path '.venv\Scripts\python.exe') -and -not $ForcePrivatePython) {
    $venvOk = ($null -ne (Test-Python (Join-Path $Dir '.venv\Scripts\python.exe')))
  }
  if (-not $venvOk) {
    Remove-Item -LiteralPath '.venv' -Recurse -Force -ErrorAction SilentlyContinue
    New-Venv
  } else {
    Note 'Python is ready.'
  }
  $venvPy = Join-Path $Dir '.venv\Scripts\python.exe'
  $pythonw = Join-Path $Dir '.venv\Scripts\pythonw.exe'
  Step 'Installing components (about a minute)'
  if ((Invoke-Quiet $venvPy @('-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-r', 'requirements.txt')) -ne 0) {
    Note ($script:LastOutput -split "`n" | Select-Object -Last 3)
    Stop-Here 'Could not install the components. Check the internet connection and run the installer again.'
  }
  if (-not (Test-Path $pythonw)) { $pythonw = $venvPy }

  # 5. background start at sign-in
  $script:UseTask = $false
  if (-not $NoTask) {
    Step 'Starting A.F.R.A in the background'
    if (Register-Autostart $pythonw) { $script:UseTask = $true }
    else {
      try {
        New-Link (Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.lnk") $pythonw "`"$(Join-Path $Dir 'run.py')`" --no-browser --port $Port" ''
      } catch { Note 'Could not add A.F.R.A to Startup. It will start when you open it from the Start menu.' }
    }
    if ($script:UseTask) {
      Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } else {
      Start-Process -FilePath $pythonw -ArgumentList @("`"$(Join-Path $Dir 'run.py')`"", '--no-browser', '--port', "$Port") -WorkingDirectory $Dir -WindowStyle Hidden
    }
  }

  # 6. Start menu and Desktop shortcuts
  if (-not $NoShortcuts) {
    Step 'Adding A.F.R.A to the Start menu and Desktop'
    $ico = Join-Path $Dir 'afra.ico'
    $png = Join-Path $Dir 'static\icon-512.png'
    if (-not ((Test-Path $png) -and (New-Icon $png $ico))) { $ico = '' }
    $vbs = New-Opener $pythonw
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Programs')) "$TaskName.lnk"), (Join-Path ([Environment]::GetFolderPath('Desktop')) "$TaskName.lnk"))) {
      try { New-Link $lnk $wscript "`"$vbs`"" $ico } catch { Note "Could not create $lnk" }
    }
  }

  # 7. Claude: use A.F.R.A from the Claude app with a Claude Pro/Max plan, no API key needed
  $claudeNote = 'Install the Claude app (claude.ai/download), then click Settings > Connect to Claude in A.F.R.A.'
  if (-not $NoClaude) {
    $hasClaude = (Test-Path (Join-Path $env:APPDATA 'Claude')) -or (@(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages') -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue).Count -gt 0)
    if ($hasClaude) {
      Step 'Connecting to the Claude app'
      $null = Invoke-Quiet $venvPy @('-m', 'app.claude_connect')
      Say $script:LastOutput.Trim()
      $claudeNote = 'Connected to the Claude app: quit Claude and open it again to use A.F.R.A there.'
    }
  }

  if (-not $NoTask) { $null = Wait-Afra 90 }
  Say ''
  Write-Host 'Done. A.F.R.A is installed.' -ForegroundColor Green
  if ($NoTask) {
    Say '- A.F.R.A is installed but not set to start by itself.'
  } else {
    if (-not $NoOpen) { try { Start-Process $URL } catch { } }
    Say '- A.F.R.A opens in your browser now. Open it any time from the Start menu or the Desktop icon "A.F.R.A".'
  }
  if (-not $NoTask) { Say '- It runs quietly in the background and starts when you sign in to Windows. There is no window to keep open.' }
  Say "- Updates install themselves. Your library and documents live in $(Join-Path $Dir 'data')."
  Say "- $claudeNote"
  Say 'You can close this window.'
}

$here = Get-Location
try {
  if ($env:OS -ne 'Windows_NT') { Stop-Here 'This installer is for Windows. On a Mac, use the Terminal line on farmansyah.github.io/afra.' }
  $Dir = $Dir.TrimEnd('\')
  if ($Uninstall) { Remove-Everything } else { Install-Afra }
} catch {
  Write-Host ''
  Write-Host ("Something went wrong: " + $_.Exception.Message) -ForegroundColor Red
  Write-Host "Nothing is broken. Fix the problem above and run the installer again:  $ONELINE"
} finally {
  Set-Location -LiteralPath $here
}
