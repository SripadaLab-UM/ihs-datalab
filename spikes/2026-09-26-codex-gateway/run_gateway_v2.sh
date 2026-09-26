#!/bin/sh
# Gateway v2: stock nginx:alpine + njs request-shape allowlist on /v1/responses.
S=$(cd "$(dirname "$0")" && pwd)
docker rm -f spike-gateway >/dev/null 2>&1
docker run -d --name spike-gateway --network spike-egress \
  -e UPSTREAM_HOST=api.toolkit.umgpt.umich.edu \
  -e MCP_UPSTREAM=host.docker.internal:8765 \
  -e MCP_HOST_HEADER=127.0.0.1:8765 \
  -v "$S/gateway_v2/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$S/gateway_v2/filter.js:/etc/nginx/njs/filter.js:ro" \
  -v "$S/gateway_v2/default.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  -v "$S/gateway/15-write-auth.sh:/docker-entrypoint.d/16-write-auth.sh:ro" \
  -v "$S/secrets/umgpt_key:/run/secrets/umgpt_key:ro" \
  nginx:alpine >/dev/null
docker network connect --alias gateway spike-data spike-gateway
