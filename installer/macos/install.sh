#!/bin/sh
# DataLab installer for macOS.
#
#   sh install.sh --package <datalab .whl file or URL> [--settings <lab settings file>]
#                 [--constraints <constraints.txt>] [--profile real|practice] [--no-github]
#
# constraints.txt holds the exact tested dependency versions; it's found
# automatically if it sits next to a local package file.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#      Each version gets its own folder, so an update installs beside the one in use
#      and the previous version is kept (docs/DISTRIBUTION.md).
#   4. Downloads the pinned container images.
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into your macOS Keychain.
#   6. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. Skipped for the practice profile.
#   7. Adds DataLab to your Applications folder.
set -eu

UV_VERSION=0.12.19
PACKAGE=""
SETTINGS=""
CONSTRAINTS=""
PROFILE="real"
GITHUB="ask"
while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="$2"; shift 2 ;;
    --settings) SETTINGS="$2"; shift 2 ;;
    --constraints) CONSTRAINTS="$2"; shift 2 ;;
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
if [ -z "$CONSTRAINTS" ] && [ -f "$(dirname "$PACKAGE")/constraints.txt" ]; then
  CONSTRAINTS="$(dirname "$PACKAGE")/constraints.txt"
fi
if [ -z "$CONSTRAINTS" ]; then
  echo "constraints.txt (the tested dependency versions) wasn't found. Pass --constraints."
  exit 2
fi
# The package's file name carries its version: datalab-<version>-py3-none-any.whl
VERSION="$(basename "${PACKAGE%%\?*}" | sed -n 's/^datalab-\([A-Za-z0-9.+!]*\)-py3-none-any\.whl$/\1/p')"
if [ -z "$VERSION" ]; then
  echo "The package must be a datalab-<version>-py3-none-any.whl file."
  exit 2
fi
# Beside the data folders: versions/<version>/, current, previous, bin/datalab.
ROOT="${DATALAB_INSTALL_DIR:-$HOME/Library/Application Support/DataLab/app}"
TARGET="$ROOT/versions/$VERSION"
if [ -f "$TARGET/.complete" ]; then
  echo "DataLab $VERSION is installed already."
else
  # A folder without .complete is an install that was cut off: start it again.
  rm -rf "$TARGET"
  mkdir -p "$ROOT/versions"
  uv venv -q --python 3.13 "$TARGET"
  # uv cuts a --constraints path at its first space ("Application Support",
  # "OneDrive - …"), so it gets a copy under a plain name, from its own folder.
  case "$PACKAGE" in *://*|/*) ;; *) PACKAGE="$(pwd)/$PACKAGE" ;; esac
  PLAIN="$(mktemp -d)"
  cp "$CONSTRAINTS" "$PLAIN/constraints.txt"
  (cd "$PLAIN" && uv pip install -q --python "$TARGET/bin/python" --constraints constraints.txt "$PACKAGE")
  rm -rf "$PLAIN"
  SAID="$("$TARGET/bin/datalab" --version)"
  if [ "$SAID" != "datalab $VERSION" ]; then
    echo "The installed DataLab says '$SAID', not $VERSION."
    rm -rf "$TARGET"
    exit 1
  fi
  printf '{"version": "%s", "installed_at": "%s"}\n' "$VERSION" "$(date +%Y-%m-%dT%H:%M:%S%z)" > "$TARGET/.complete"
fi
# The launcher's command runs whichever version `current` names.
mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/datalab" <<'SHIM'
#!/bin/sh
# Runs the DataLab version the launcher opens (named in ../current).
root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(head -n 1 "$root/current")"
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
if [ -n "$SETTINGS" ]; then
  "$DATALAB" --profile "$PROFILE" setup --settings "$SETTINGS"
else
  "$DATALAB" --profile "$PROFILE" setup
fi

step "6/7 The lab's knowledge base and pipelines"
if [ "$PROFILE" = "practice" ]; then
  echo "Skipped: practice DataLab doesn't use the lab's repositories."
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
# (close the window or press Ctrl-C). It opens the version `current` names, which
# is what an update switches.
cat > "$APP/Contents/MacOS/DataLab" <<LAUNCH
#!/bin/sh
osascript -e 'tell application "Terminal" to activate' \\
  -e 'tell application "Terminal" to do script "\"$DATALAB\" --profile $PROFILE serve"'
LAUNCH
chmod +x "$APP/Contents/MacOS/DataLab"
echo "Added DataLab to $HOME/Applications."

step "Done"
echo "Open DataLab from your Applications folder (or run: \"$DATALAB\" serve)."
