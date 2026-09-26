#!/bin/sh
# Regenerate the frontend's API types from the backend's OpenAPI schema, so
# the two can't drift. CI fails if the result differs from what's committed.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
(
  cd "$root/backend"
  DATALAB_DATA_DIR="$scratch" uv run python -c '
import json
from datalab.app import create_app
from datalab.config import load_settings
print(json.dumps(create_app(load_settings("practice"), manage_containers=False).openapi()))
' > "$scratch/openapi.json"
)
cd "$root/frontend"
npx openapi-typescript "$scratch/openapi.json" -o src/api/schema.d.ts
