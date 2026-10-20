#!/bin/sh
# Run this checkout as a dev copy of the real DataLab, to try a change before
# it's released (a release is offered to everyone on the beta channel within
# the hour). See docs/DISTRIBUTION.md, "Before tagging: try it in a dev copy".
#
# The dev copy is a second, separate DataLab on its own port (8775), with its
# own data folder (DataLab/dev-real) and its own containers, so it never
# touches the installed DataLab's conversations, data or containers. It
# shares the computer's keychain: the U-M GPT key, the database password, and
# the GitHub and Dropbox sign-ins. Signing out or disconnecting in it signs
# out the installed DataLab too.
#
# Usage: scripts/dev-copy.sh [--build-image] [--no-browser]
#   --build-image  build the agent image from images/agent (when this change
#                  touches it); otherwise the latest release's pinned image.
#
# DATALAB_DEV_DIR and DATALAB_DEV_PORT change the folder and port.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
repo=SripadaLab-UM/ihs-datalab
build_image=0
browser=""
for arg in "$@"; do
  case "$arg" in
    --build-image) build_image=1 ;;
    --no-browser) browser="--no-browser" ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

case "$(uname -s)" in
  Darwin) base="$HOME/Library/Application Support/DataLab" ;;
  *) base="${XDG_DATA_HOME:-$HOME/.local/share}/datalab" ;;
esac
dev_dir="${DATALAB_DEV_DIR:-$base/dev-real}"
port="${DATALAB_DEV_PORT:-8775}"

# The lab's settings, copied once from the real DataLab's (never its data).
python3 "$root/scripts/dev_copy_settings.py" "$base/real/settings.toml" "$dev_dir/settings.toml" "$port"

# The agent image.
latest="$(gh release list -R "$repo" --limit 1 --json tagName --jq '.[0].tagName')"
if [ "$build_image" = 1 ]; then
  echo "Building the agent image from images/agent…"
  docker build -q -t datalab-agent:dev "$root/images/agent" >/dev/null
  image="datalab-agent:dev"
else
  if ! git -C "$root" diff --quiet "$latest" -- images/agent 2>/dev/null; then
    echo "Note: images/agent has changed since $latest. To try those changes, run with --build-image." >&2
  fi
  image="$(gh release download "$latest" -R "$repo" -p images.json -O - | python3 -c 'import json, sys; print(json.load(sys.stdin)["agent"])')"
  echo "Agent image: $latest's ($image)"
  docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image" >/dev/null
fi

# The web UI from this checkout.
if [ ! -d "$root/frontend/node_modules" ]; then
  (cd "$root/frontend" && npm ci --no-audit --no-fund)
fi
(cd "$root/frontend" && npm run build >/dev/null)

echo "Starting the dev copy on http://127.0.0.1:$port (data: $dev_dir). Ctrl-C stops it."
cd "$root/backend"
DATALAB_DATA_DIR="$dev_dir" DATALAB_AGENT_IMAGE="$image" \
  exec uv run datalab --profile real serve $browser
