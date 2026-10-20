#!/bin/sh
# Build a DataLab release package: the Python app with the web UI inside.
# Output: backend/dist/datalab-<version>-py3-none-any.whl
#
# AGENT_IMAGE (optional): the pinned agent image this release runs, e.g.
# ghcr.io/sripadalab-um/datalab-agent@sha256:…  Installed copies use it.
# LAB_DROPBOX_APP_KEY (optional): the lab's Dropbox app key, which installed
# copies use when their settings.toml doesn't set one, so an in-app update is
# all it takes (config.py). From a repository variable, never the source.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"

(cd "$root/frontend" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$root/backend/src/datalab/web_dist"
cp -R "$root/frontend/dist" "$root/backend/src/datalab/web_dist"
if [ -n "${LAB_DROPBOX_APP_KEY:-}" ] && ! printf '%s' "$LAB_DROPBOX_APP_KEY" | grep -Eq '^[a-z0-9]{10,32}$'; then
  echo "LAB_DROPBOX_APP_KEY isn't a Dropbox app key." >&2
  exit 1
fi
if [ -n "${AGENT_IMAGE:-}" ] || [ -n "${LAB_DROPBOX_APP_KEY:-}" ]; then
  AGENT_IMAGE="${AGENT_IMAGE:-}" LAB_DROPBOX_APP_KEY="${LAB_DROPBOX_APP_KEY:-}" python3 -c '
import json, os
release = {}
if os.environ["AGENT_IMAGE"]:
    release["agent_image"] = os.environ["AGENT_IMAGE"]
if os.environ["LAB_DROPBOX_APP_KEY"]:
    release["lab"] = {"dropbox": {"app_key": os.environ["LAB_DROPBOX_APP_KEY"]}}
print(json.dumps(release))
' > "$root/backend/src/datalab/release.json"
fi
(cd "$root/backend" && rm -rf dist && uv build --wheel)
# Every dependency from uv.lock, pinned by version and hash, then the package
# itself by its checksum. The installers and the updater install exactly this,
# with `uv pip install --require-hashes --only-binary :all: -r requirements.txt`,
# so nothing is installed that the release didn't name (docs/DISTRIBUTION.md).
(cd "$root/backend" && uv export --no-dev --no-emit-project \
  --format requirements-txt -q -o dist/requirements.txt)
wheel="$(cd "$root/backend/dist" && ls datalab-*-py3-none-any.whl)"
digest="$(shasum -a 256 "$root/backend/dist/$wheel" 2>/dev/null || sha256sum "$root/backend/dist/$wheel")"
printf './%s --hash=sha256:%s\n' "$wheel" "${digest%% *}" >> "$root/backend/dist/requirements.txt"
rm -rf "$root/backend/src/datalab/web_dist" "$root/backend/src/datalab/release.json"
ls "$root"/backend/dist/*.whl
