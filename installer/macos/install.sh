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
#   4. Downloads the pinned container images.
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into your macOS Keychain.
#   6. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. Skipped for the practice profile.
#   7. Adds DataLab to your Applications folder ("DataLab (practice)" for practice).
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
# Real and practice each have their own launcher, so neither replaces the other.
if [ "$PROFILE" = "practice" ]; then
  APP="$HOME/Applications/DataLab (practice).app"
  NAME="DataLab (practice)"
  BUNDLE="edu.umich.ihs.datalab.practice"
else
  APP="$HOME/Applications/DataLab.app"
  NAME="DataLab"
  BUNDLE="edu.umich.ihs.datalab"
fi
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE</string>
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
echo "Added $NAME to $HOME/Applications."

step "Done"
echo "Open $NAME from your Applications folder (or run: \"$DATALAB\" --profile $PROFILE serve)."
