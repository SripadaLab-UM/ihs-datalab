#!/bin/sh
S=$(cd "$(dirname "$0")" && pwd)
docker rm -f spike-gateway >/dev/null 2>&1
T0=$(python3 -c 'import time;print(time.time())')
docker run -d --name spike-gateway --network spike-egress \
  -e UPSTREAM_HOST=api.toolkit.umgpt.umich.edu \
  -e MCP_UPSTREAM=host.docker.internal:8765 \
  -e MCP_HOST_HEADER=127.0.0.1:8765 \
  -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx/conf.d \
  -v "$S/gateway/default.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  -v "$S/gateway/15-write-auth.sh:/docker-entrypoint.d/16-write-auth.sh:ro" \
  -v "$S/secrets/umgpt_key:/run/secrets/umgpt_key:ro" \
  nginx:alpine >/dev/null
docker network connect --alias gateway spike-data spike-gateway
python3 -c "import time;print('gateway docker run+connect: %.2fs'%(time.time()-$T0))"
