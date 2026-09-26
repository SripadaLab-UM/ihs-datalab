#!/bin/sh
S=$(cd "$(dirname "$0")" && pwd)
docker rm -f spike-squid >/dev/null 2>&1
docker run -d --name spike-squid --network spike-egress -v "$S/research/squid.conf:/etc/squid/squid.conf:ro" ubuntu/squid:latest >/dev/null
docker network connect --alias proxy spike-research spike-squid
