#!/bin/bash
# A.F.R.A installer for macOS. Paste this one line into Terminal:
#
#   curl -fsSL https://farmansyah.github.io/afra/install.sh | bash
#
# Installs into ~/AFRA, runs quietly in the background (starts at login, restarts itself if it stops),
# adds an A.F.R.A app to Applications and the Dock, keeps itself up to date, and connects to the Claude app.
# Running it again repairs or updates the installation. Your data (~/AFRA/data) is never touched.
# Remove:  curl -fsSL https://farmansyah.github.io/afra/install.sh | bash -s -- --uninstall
# (c) 2026 danafarmansyah. All rights reserved. Crafted with love.

main() {
  set -euo pipefail
  DIR="$HOME/AFRA"
  PORT=8765
  URL="http://localhost:$PORT"
  LABEL="com.danafarmansyah.afra"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  APP="$HOME/Applications/AFRA.app"
  ZIP="https://codeload.github.com/farmansyah/afra/zip/refs/heads/main"
  UID_="$(id -u)"

  step() { printf "\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n" "$*"; }
  fail() { printf "\n\033[1;31m%s\033[0m\n" "$*"; exit 1; }
  [ "$(uname)" = "Darwin" ] || fail "This installer is for macOS. On Windows, unzip A.F.R.A and double-click start.bat."

  if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    rm -rf "$APP"
    echo "A.F.R.A is removed. Your library and documents are still in $DIR/data (delete that folder to remove them too)."
    exit 0
  fi

  printf "\n\033[1mA.F.R.A: Article Finder & Research Assistant\033[0m\nInstalling into %s (about 2 minutes)...\n" "$DIR"

  # 1. stop a copy that is already running (an old version, or one started by hand) so files and port are free
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  if curl -s -m 2 -o /dev/null "$URL/api/version"; then
    for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
    sleep 2
  fi

  # 2. the program itself (data/ and .venv/ are kept)
  step "Downloading A.F.R.A"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "$ZIP" -o "$TMP/afra.zip" || fail "Download failed. Check the internet connection and try again."
  ditto -x -k "$TMP/afra.zip" "$TMP/src"
  SRC="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -1)"
  mkdir -p "$DIR"
  rsync -a --exclude "/data" --exclude "/.venv" --exclude "/.git" "$SRC/" "$DIR/"
  rm -rf "$DIR/AFRA.app"  # the old double-click app this installer replaces

  # 3. bring over the library from a copy that was unzipped somewhere else before
  if [ ! -f "$DIR/data/research.db" ]; then
    OLD="$( (find "$HOME/Downloads" "$HOME/Documents" "$HOME/Desktop" -maxdepth 5 -path "*/data/research.db" 2>/dev/null || true) \
            | grep -v "^$DIR/" | while read -r f; do echo "$(stat -f %m "$f") $f"; done | sort -rn | head -1 | cut -d' ' -f2- || true)"
    if [ -n "$OLD" ]; then
      step "Copying your existing library from $(dirname "$(dirname "$OLD")")"
      mkdir -p "$DIR/data"
      cp -Rp "$(dirname "$OLD")/." "$DIR/data/"
    fi
  fi
  mkdir -p "$DIR/data"

  # 4. Python: use a recent one if present, otherwise fetch a private copy (no installer windows, no admin password)
  step "Preparing Python"
  cd "$DIR"
  if ! { [ -x .venv/bin/python ] && .venv/bin/python -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; }; then
    rm -rf .venv
    PY=""
    CANDIDATES="python3.13 python3.12 python3.11 python3.10 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3"
    xcode-select -p >/dev/null 2>&1 && CANDIDATES="$CANDIDATES /usr/bin/python3"  # without the developer tools this one only opens an install prompt
    for p in $CANDIDATES; do
      if command -v "$p" >/dev/null 2>&1 && "$p" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then PY="$(command -v "$p")"; break; fi
    done
    if [ -n "$PY" ]; then
      "$PY" -m venv .venv
    else
      echo "No recent Python found: downloading a private copy for A.F.R.A..."
      curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$DIR/.uv" sh >/dev/null
      "$DIR/.uv/uv" venv --seed --quiet --python 3.12 .venv
    fi
  fi
  step "Installing components"
  .venv/bin/python -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || fail "Could not install components. Check the internet connection and run the installer again."

  # 5. background service: starts at login, restarts if it stops, and picks up updates by itself
  step "Starting A.F.R.A in the background"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/python</string><string>$DIR/run.py</string><string>--no-browser</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>AFRA_SUPERVISED</key><string>1</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict>
</plist>
PLIST
  launchctl bootstrap "gui/$UID_" "$PLIST" 2>/dev/null || { sleep 2; launchctl bootstrap "gui/$UID_" "$PLIST" 2>/dev/null; }     || launchctl kickstart -k "gui/$UID_/$LABEL"

  # 6. the app to click: a real macOS applet built on this Mac, so Gatekeeper has nothing to block
  step "Adding A.F.R.A to Applications and the Dock"
  mkdir -p "$HOME/Applications"
  rm -rf "$APP"
  cat > "$TMP/afra.applescript" <<OSA
on run
  try
    do shell script "curl -s -m 2 -o /dev/null $URL/api/version"
  on error
    try
      do shell script "launchctl kickstart gui/\$(id -u)/$LABEL >/dev/null 2>&1 || launchctl bootstrap gui/\$(id -u) '$PLIST'; for i in \$(seq 1 60); do curl -s -m 2 -o /dev/null $URL/api/version && exit 0; sleep 1; done; exit 1"
    on error
      display dialog "A.F.R.A could not start. Please run the installer again:" & return & return & "curl -fsSL https://farmansyah.github.io/afra/install.sh | bash" buttons {"OK"} default button 1 with title "A.F.R.A" with icon caution
      return
    end try
  end try
  open location "$URL"
end run
OSA
  osacompile -o "$APP" "$TMP/afra.applescript"
  cp "$DIR/static/AFRA.icns" "$APP/Contents/Resources/applet.icns"
  /usr/libexec/PlistBuddy -c "Set :CFBundleName A.F.R.A" "$APP/Contents/Info.plist" 2>/dev/null || true
  codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
  touch "$APP"
  if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "Applications/AFRA.app"; then
    defaults write com.apple.dock persistent-apps -array-add "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://$APP/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
    killall Dock 2>/dev/null || true
  fi

  # 7. Claude: use A.F.R.A from the Claude app with a Claude Pro/Max plan, no API key needed
  CLAUDE_NOTE="Install the Claude app (claude.ai/download), then click Settings > Connect to Claude in A.F.R.A."
  if [ -d "$HOME/Library/Application Support/Claude" ]; then
    .venv/bin/python -m app.claude_connect && CLAUDE_NOTE="Connected to the Claude app: quit Claude (Cmd+Q) and open it again to use A.F.R.A there."
  fi

  for i in $(seq 1 60); do curl -s -m 2 -o /dev/null "$URL/api/version" && break; sleep 1; done
  open "$URL"
  printf "\n\033[1;32mA.F.R.A is installed.\033[0m\n"
  echo "- Open it any time from the Dock or Applications (AFRA). It runs quietly in the background and starts with your Mac."
  echo "- Updates install themselves. Your library and documents live in $DIR/data."
  echo "- $CLAUDE_NOTE"
  echo "You can close this Terminal window."
}

main "$@"
