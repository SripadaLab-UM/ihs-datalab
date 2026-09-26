#!/bin/sh
# Build a DataLab release package: the Python app with the web UI inside.
# Output: backend/dist/datalab-<version>-py3-none-any.whl
#
# AGENT_IMAGE (optional): the pinned agent image this release runs, e.g.
# ghcr.io/sripadalab-um/datalab-agent@sha256:…  Installed copies use it.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"

(cd "$root/frontend" && npm ci --no-audit --no-fund && npm run build)
rm -rf "$root/backend/src/datalab/web_dist"
cp -R "$root/frontend/dist" "$root/backend/src/datalab/web_dist"
if [ -n "${AGENT_IMAGE:-}" ]; then
  printf '{"agent_image": "%s"}\n' "$AGENT_IMAGE" > "$root/backend/src/datalab/release.json"
fi
(cd "$root/backend" && rm -rf dist && uv build --wheel)
# The exact dependency versions from uv.lock. The installers pass this to
# `uv tool install --constraints`, so every install gets the tested versions.
(cd "$root/backend" && uv export --no-dev --no-hashes --no-emit-project \
  --format requirements-txt -q -o dist/constraints.txt)
rm -rf "$root/backend/src/datalab/web_dist" "$root/backend/src/datalab/release.json"
ls "$root"/backend/dist/*.whl
