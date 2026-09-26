#!/bin/sh
# Agent: ONLY on the internal network; no key; dummy provider key.
S=$(cd "$(dirname "$0")" && pwd)
DNS_OPTS=${DNS_OPTS:-}
docker rm -f spike-agent >/dev/null 2>&1
mkdir -p "$S/work"
T0=$(python3 -c 'import time;print(time.time())')
docker run -d --name spike-agent --network spike-data --init --dns 192.0.2.1 $DNS_OPTS \
  --cap-drop ALL --security-opt no-new-privileges --memory 2g --cpus 2 --pids-limit 512 \
  -e UMGPT_DUMMY_KEY=dummy-not-a-real-key \
  -e IHS_MCP_TOKEN=spike-mcp-token-123 \
  -v "$S/codex_home:/codex_home" \
  -v "$S/work:/work" \
  spike-agent:0.157.1 >/dev/null
python3 -c "import time;print('agent docker run: %.2fs'%(time.time()-$T0))"
