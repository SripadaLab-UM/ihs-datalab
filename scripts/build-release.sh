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
