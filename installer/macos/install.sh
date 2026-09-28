#!/bin/sh
# DataLab installer for macOS.
#
#   sh install.sh --package <datalab .whl file or URL> [--settings <lab settings file>]
#                 [--requirements <requirements.txt or URL>] [--profile real|practice]
#                 [--no-github]
#
# requirements.txt comes with each release: every dependency pinned by version
# and hash, and the package by its checksum. It's found automatically if it
# sits next to a local package file. Nothing is installed that it doesn't name.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#      Each version gets its own folder, so an update installs beside the one in use
#      and the previous version is kept (docs/DISTRIBUTION.md).
#   4. Downloads the pinned container images (for practice, Oracle Database Free too).
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into your macOS Keychain. Practice asks for no password, and the key
#      is optional there.
#   6. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. For practice, instead: sets up the practice database (a
#      container on this computer only, with made-up data), keeping any data it
#      already has.
#   7. Adds the DataLab app ("DataLab (practice)" for practice, with its own icon) to
#      /Applications, or to ~/Applications if you can't add to /Applications without
#      sudo, and a shortcut to it on your Desktop. At the end it says where everything
#      went and offers to show the app in Finder and open it.
#
# DATALAB_SYSTEM_APPLICATIONS (for tests) stands in for /Applications.
set -eu

# Everything goes in your own user account: never run this as root.
[ "$(id -u)" -ne 0 ] || { echo "Run this without sudo."; exit 1; }
# Nothing from the environment may steer uv or pip (another index, checks
# turned off) or Python.
for name in $(env | sed -n 's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p; s/^\(PIP_[A-Za-z0-9_]*\)=.*/\1/p'); do
  unset "$name"
done
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP VIRTUAL_ENV

UV_VERSION=0.12.19
PACKAGE=""
SETTINGS=""
REQUIREMENTS=""
PROFILE="real"
GITHUB="ask"
while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="$2"; shift 2 ;;
    --settings) SETTINGS="$2"; shift 2 ;;
    --requirements) REQUIREMENTS="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --no-github) GITHUB="no"; shift ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
if [ -z "$PACKAGE" ]; then
  echo "Usage: sh install.sh --package <datalab .whl file or URL> [--settings <file>]"
  exit 2
fi
case "$PROFILE" in
  real|practice) ;;
  *) echo "--profile must be real or practice, not $PROFILE"; exit 2 ;;
esac

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
# Questions are read from the terminal, even when this script arrives on a pipe.
ask() {
  printf '%s ' "$1"
  answer=""
  if [ -r /dev/tty ]; then read -r answer < /dev/tty || answer=""; fi
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

step "1/7 Docker Desktop"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop isn't installed. Install it from the page that's opening,"
  echo "start it once, then run this installer again."
  open "https://www.docker.com/products/docker-desktop/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop…"
  open -a Docker
  i=0
  until docker info >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then echo "Docker Desktop didn't start. Start it, then run this again."; exit 1; fi
    sleep 2
  done
fi
echo "Docker Desktop is running."

step "2/7 uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
uv --version

step "3/7 DataLab"
# The package's file name carries its version: datalab-<version>-py3-none-any.whl
WHEEL="$(basename "${PACKAGE%%\?*}")"
VERSION="$(printf '%s' "$WHEEL" | sed -n -e 's/^datalab-\([0-9][A-Za-z0-9.+!]*[A-Za-z0-9]\)-py3-none-any\.whl$/\1/p' \
  -e 's/^datalab-\([0-9]\)-py3-none-any\.whl$/\1/p')"
if [ -z "$VERSION" ]; then
  echo "The package must be a datalab-<version>-py3-none-any.whl file."
  exit 2
fi
case "$PACKAGE" in
  *://*) ;;
  *) if [ -z "$REQUIREMENTS" ] && [ -f "$(dirname "$PACKAGE")/requirements.txt" ]; then
       REQUIREMENTS="$(dirname "$PACKAGE")/requirements.txt"
     fi ;;
esac
if [ -z "$REQUIREMENTS" ]; then
  echo "requirements.txt (every dependency, pinned by hash) wasn't found. Pass --requirements."
  exit 2
fi
# uv gets both files under plain names, from their own folder: it cuts a path
# at its first space ("Application Support", "OneDrive - …").
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
fetch() {
  case "$1" in
    https://*) curl -fsSL --proto '=https' --proto-redir '=https' -o "$2" "$1" ;;
    *://*) echo "Only https downloads: $1"; exit 2 ;;
    *) cp "$1" "$2" ;;
  esac
}
fetch "$PACKAGE" "$STAGE/$WHEEL"
fetch "$REQUIREMENTS" "$STAGE/requirements.txt"
# The package must be the one requirements.txt names, by its checksum.
SHA256="$(shasum -a 256 "$STAGE/$WHEEL" | cut -d ' ' -f 1)"
if ! grep -qxF "./$WHEEL --hash=sha256:$SHA256" "$STAGE/requirements.txt"; then
  echo "requirements.txt doesn't name this package with this checksum. Use the two files"
  echo "from the same DataLab release."
  exit 1
fi
# Beside the data folders: versions/<version>/, current, previous, bin/datalab.
# The real and practice DataLabs share them.
ROOT="${DATALAB_INSTALL_DIR:-$HOME/Library/Application Support/DataLab/app}"
TARGET="$ROOT/versions/$VERSION"
if [ -f "$TARGET/.complete" ] && grep -qF "\"wheel_sha256\": \"$SHA256\"" "$TARGET/.complete"; then
  echo "DataLab $VERSION is installed already."
else
  # A folder without .complete (or with another package) is replaced.
  rm -rf "$TARGET"
  mkdir -p "$ROOT/versions"
  uv venv -q --no-config --python 3.13 "$TARGET"
  # Every file checked against requirements.txt's hashes, only wheels, and
  # only from PyPI.
  (cd "$STAGE" && uv pip install -q --no-config --require-hashes --only-binary :all: \
    --default-index https://pypi.org/simple --link-mode copy --python "$TARGET/bin/python" -r requirements.txt)
  SAID="$("$TARGET/bin/datalab" --version)"
  if [ "$SAID" != "datalab $VERSION" ]; then
    echo "The installed DataLab says '$SAID', not $VERSION."
    rm -rf "$TARGET"
    exit 1
  fi
  sync
  printf '{"version": "%s", "wheel_sha256": "%s", "installed_at": "%s"}\n' \
    "$VERSION" "$SHA256" "$(date +%Y-%m-%dT%H:%M:%S%z)" > "$TARGET/.complete"
fi
# The launcher's command runs whichever version `current` names.
mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/datalab" <<'SHIM'
#!/bin/sh
# Runs the DataLab version the launcher opens (named in ../current), or the
# one before (../previous) if that one can't run.
# UTF-8 for Python's own text files and console, whatever the locale.
export PYTHONUTF8=1
root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(head -n 1 "$root/current" 2>/dev/null || true)"
if [ -z "$version" ] || [ ! -x "$root/versions/$version/bin/datalab" ]; then
  echo "DataLab ${version:-(none)} can't be opened; opening the version before it." >&2
  version="$(head -n 1 "$root/previous" 2>/dev/null || true)"
fi
exec "$root/versions/$version/bin/datalab" "$@"
SHIM
chmod +x "$ROOT/bin/datalab"
OLD="$(head -n 1 "$ROOT/current" 2>/dev/null || true)"
if [ -n "$OLD" ] && [ "$OLD" != "$VERSION" ]; then
  printf '%s\n' "$OLD" > "$ROOT/.previous.new" && mv "$ROOT/.previous.new" "$ROOT/previous"
fi
printf '%s\n' "$VERSION" > "$ROOT/.current.new" && mv "$ROOT/.current.new" "$ROOT/current"
DATALAB="$ROOT/bin/datalab"
"$DATALAB" --version

step "4/7 Container images"
"$DATALAB" --profile "$PROFILE" pull-images

step "5/7 Settings and keys"
if [ "$PROFILE" = "practice" ]; then
  echo "Next, DataLab asks for your U-M GPT API key. It's optional for practice: press"
  echo "Enter to skip it. No database password, VPN or GitHub account is needed."
else
  echo "Next, DataLab asks for your U-M GPT API key (and the database password, if"
  echo "your lab uses one)."
fi
echo "Each character shows as *. Press Enter when done. Keys are kept in your macOS Keychain."
if [ -n "$SETTINGS" ]; then
  "$DATALAB" --profile "$PROFILE" setup --settings "$SETTINGS"
else
  "$DATALAB" --profile "$PROFILE" setup
fi

if [ "$PROFILE" = "practice" ]; then
  step "6/7 Setting up the practice database"
else
  step "6/7 The lab's knowledge base and pipelines"
fi
if [ "$PROFILE" = "practice" ]; then
  echo "Skipped: practice DataLab doesn't use the lab's repositories. Instead it runs its"
  echo "own database of made-up data, in Docker, reachable from this computer only."
  echo "The first time, this takes a few minutes; data it already has is kept."
  # Not a reason to stop: DataLab sets it up (or starts it) each time it opens.
  "$DATALAB" --profile "$PROFILE" practice-db setup \
    || echo "It isn't ready yet (the messages above say why). DataLab tries again each time it opens."
elif [ "$GITHUB" = "no" ]; then
  echo "Skipped. Sign in later in DataLab, under Settings → GitHub."
elif ask "Sign in to GitHub now, to download them? [Y/n]"; then
  # 2: not set up here (the lab's settings don't name the repos); it says so.
  # 1: not signed in, or no access to a repo; it says whom to ask.
  signed=0
  "$DATALAB" --profile "$PROFILE" github sign-in || signed=$?
  if [ "$signed" -ne 2 ]; then
    "$DATALAB" --profile "$PROFILE" repos sync \
      || echo "You can sync again later in DataLab (Knowledge and Pipelines)."
  fi
else
  echo "Skipped. Sign in later in DataLab, under Settings → GitHub."
fi

step "7/7 Launcher"
# Real and practice each have their own app, name and icon, so neither
# replaces the other and they're easy to tell apart.
if [ "$PROFILE" = "practice" ]; then
  NAME="DataLab (practice)"
  BUNDLE="edu.umich.ihs.datalab.practice"
  ICON="DataLab-practice.icns"
else
  NAME="DataLab"
  BUNDLE="edu.umich.ihs.datalab"
  ICON="DataLab.icns"
fi
# Whether a bundle is this profile's DataLab app (one this installer made).
ours() { [ -f "$1/Contents/Info.plist" ] && grep -qF "<string>$BUNDLE</string>" "$1/Contents/Info.plist"; }
# In /Applications, where people look, if you can add to it without sudo (an
# administrator account can); otherwise in your own Applications folder
# (~/Applications, which Finder, Spotlight and Launchpad also show). Only an
# app of ours is ever replaced: something else called "$NAME.app" is left alone.
SYSTEM_APPS="${DATALAB_SYSTEM_APPLICATIONS:-/Applications}"
USER_APPS="$HOME/Applications"
# Makes $1/$NAME.app ready for a new copy: 0 when it is, 1 when what's there
# isn't ours, 2 when ours couldn't be replaced (open, or not ours to change).
prepare() {
  target="$1/$NAME.app"
  if [ -L "$target" ]; then return 1; fi
  if [ -e "$target" ]; then
    ours "$target" || return 1
    rm -rf "$target" 2>/dev/null || true
    [ ! -e "$target" ] || return 2
  fi
  mkdir -p "$target/Contents/MacOS" "$target/Contents/Resources" 2>/dev/null || return 2
}
APPS=""
if [ -d "$SYSTEM_APPS" ] && [ -w "$SYSTEM_APPS" ] && prepare "$SYSTEM_APPS"; then
  APPS="$SYSTEM_APPS"
else
  placed=0
  prepare "$USER_APPS" || placed=$?
  case "$placed" in
    0) APPS="$USER_APPS" ;;
    1) echo "$USER_APPS/$NAME.app is another app, not DataLab's, so it was left alone."
       echo "Move or rename it, then run this installer again (DataLab itself is installed)."
       exit 1 ;;
    *) echo "$USER_APPS/$NAME.app couldn't be replaced. Quit DataLab if it's open (or drag"
       echo "the app to the Trash), then run this installer again (DataLab itself is installed)."
       exit 1 ;;
  esac
fi
APP="$APPS/$NAME.app"
# The icon comes with the package. The app keeps its own copy, so an update
# that later removes this version's folder doesn't take the icon with it.
ICONFILE=""
for found in "$TARGET"/lib/python*/site-packages/datalab/branding/"$ICON"; do
  if [ -f "$found" ] && cp "$found" "$APP/Contents/Resources/DataLab.icns"; then ICONFILE="DataLab"; fi
done
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE</string>
  <key>CFBundleExecutable</key><string>DataLab</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>$ICONFILE</string>
</dict></plist>
PLIST
# DataLab runs in a Terminal window, so it's easy to see it's running and to quit
# (close the window or press Ctrl-C). It runs bin/datalab, never a version's own
# folder: that opens the version `current` names, which is what an update
# switches, so the app (and the Desktop shortcut to it) keeps working.
# The path goes to AppleScript as an argument, single-quoted for this script
# ('\'' for a quote, as in /Users/o'brien), and AppleScript quotes it for
# Terminal's shell with `quoted form of`.
QUOTED="$(printf '%s' "$DATALAB" | sed "s/'/'\\\\''/g")"
cat > "$APP/Contents/MacOS/DataLab" <<LAUNCH
#!/bin/sh
exec osascript - '$QUOTED' <<'OSA'
on run argv
  tell application "Terminal"
    activate
    do script (quoted form of item 1 of argv) & " --profile $PROFILE serve"
  end tell
end run
OSA
LAUNCH
chmod +x "$APP/Contents/MacOS/DataLab"
touch "$APP" # so Finder picks up the icon
echo "Added $NAME to $APPS."
# An earlier installer's copy in the other Applications folder would be a
# second, stale "$NAME".
for other in "$USER_APPS/$NAME.app" "$SYSTEM_APPS/$NAME.app"; do
  if [ "$other" != "$APP" ] && [ ! -L "$other" ] && ours "$other"; then
    if rm -rf "$other" 2>/dev/null; then
      echo "(Removed the copy an earlier installer put in $(dirname "$other").)"
    else
      echo "(An earlier copy is still in $(dirname "$other"); you can drag it to the Trash.)"
    fi
  fi
done
# A shortcut on the Desktop: a link to the app. One already there is replaced
# only if it's a link to this app, in either Applications folder.
DESKTOP_LINK=""
LINK="$HOME/Desktop/$NAME"
if [ -d "$HOME/Desktop" ]; then
  if [ -L "$LINK" ] || [ ! -e "$LINK" ]; then
    to="$(readlink "$LINK" 2>/dev/null || echo "$APP")"
    if [ "$to" = "$USER_APPS/$NAME.app" ] || [ "$to" = "$SYSTEM_APPS/$NAME.app" ]; then
      if rm -f "$LINK" 2>/dev/null && ln -s "$APP" "$LINK" 2>/dev/null; then
        DESKTOP_LINK="$LINK"
        echo "Added a shortcut to $NAME on your Desktop."
      else
        echo "A Desktop shortcut couldn't be added (macOS may not let Terminal use the Desktop)."
      fi
    else
      echo "Your Desktop already has a shortcut called $NAME to something else; it was left alone."
    fi
  else
    echo "Your Desktop already has something called $NAME; it was left alone."
  fi
fi

step "Done"
echo "$NAME is installed."
echo "  The app:           $APP"
if [ -n "$DESKTOP_LINK" ]; then echo "  Desktop shortcut:  $DESKTOP_LINK"; fi
echo "  Program files:     $ROOT"
echo "Open it with the Desktop shortcut, from Applications in Finder, or with Spotlight"
echo "(Cmd-Space, then type $NAME). It opens a Terminal window, then your browser."
echo "(Or run: \"$DATALAB\" --profile $PROFILE serve)"
# Only with someone at the keyboard: not when this runs from a pipe or a script.
if [ -t 0 ] && [ -t 1 ]; then
  if ask "Show $NAME in Finder? [Y/n]"; then open -R "$APP" || true; fi
  if ask "Open $NAME now? [Y/n]"; then open "$APP" || true; fi
fi
