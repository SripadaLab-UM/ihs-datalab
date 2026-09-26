#!/bin/sh
# DataLab installer for macOS.
#
#   sh install.sh --package <datalab .whl file or URL> [--settings <lab settings file>]
#                 [--constraints <constraints.txt>]
#
# constraints.txt holds the exact tested dependency versions; it's found
# automatically if it sits next to a local package file.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#   4. Downloads the pinned container images.
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into your macOS Keychain.
#   6. Adds DataLab to your Applications folder.
set -eu

UV_VERSION=0.12.19
PACKAGE=""
SETTINGS=""
CONSTRAINTS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="$2"; shift 2 ;;
    --settings) SETTINGS="$2"; shift 2 ;;
    --constraints) CONSTRAINTS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
if [ -z "$PACKAGE" ]; then
  echo "Usage: sh install.sh --package <datalab .whl file or URL> [--settings <file>]"
  exit 2
fi

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "1/6 Docker Desktop"
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

step "2/6 uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
uv --version

step "3/6 DataLab"
if [ -z "$CONSTRAINTS" ] && [ -f "$(dirname "$PACKAGE")/constraints.txt" ]; then
  CONSTRAINTS="$(dirname "$PACKAGE")/constraints.txt"
fi
if [ -z "$CONSTRAINTS" ]; then
  echo "constraints.txt (the tested dependency versions) wasn't found. Pass --constraints."
  exit 2
fi
uv tool install --force --python 3.13 --constraints "$CONSTRAINTS" "$PACKAGE"
DATALAB="$(uv tool dir --bin)/datalab"
"$DATALAB" --version

step "4/6 Container images"
"$DATALAB" pull-images

step "5/6 Settings and keys"
if [ -n "$SETTINGS" ]; then
  "$DATALAB" setup --settings "$SETTINGS"
else
  "$DATALAB" setup
fi

step "6/6 Launcher"
APP="$HOME/Applications/DataLab.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>DataLab</string>
  <key>CFBundleIdentifier</key><string>edu.umich.ihs.datalab</string>
  <key>CFBundleExecutable</key><string>DataLab</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
# DataLab runs in a Terminal window, so it's easy to see it's running and to quit
# (close the window or press Ctrl-C).
cat > "$APP/Contents/MacOS/DataLab" <<LAUNCH
#!/bin/sh
osascript -e 'tell application "Terminal" to activate' \\
  -e 'tell application "Terminal" to do script "\"$DATALAB\" serve"'
LAUNCH
chmod +x "$APP/Contents/MacOS/DataLab"
echo "Added DataLab to $HOME/Applications."

step "Done"
echo "Open DataLab from your Applications folder (or run: $DATALAB serve)."
