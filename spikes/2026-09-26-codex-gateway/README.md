> **Throwaway spike (2026-09-26).** This is evidence for the design in
> [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md), not product code.
> Logs, Codex home folders, and secrets were not kept. Paths refer to the
> original scratch folder.

# DataLab v1 spike: Codex app-server, nginx gateway, internal network, MCP

Throwaway proof of concept, run 2026-09-26 on macOS with Docker Desktop 29.6.2
(linuxkit 6.12, arm64), Codex CLI 0.157.1, nginx 1.31.6 (`nginx:alpine`
@sha256:1ed1b0e1…), Squid 6.13 (`ubuntu/squid` @sha256:6a097f68…), MCP Python
SDK 2.2.0, Python 3.14 on the host. Model endpoint: U-M GPT
`https://api.toolkit.umgpt.umich.edu/v1`. Models tried: `gpt-5.5` and
`gpt-5.6-terra`. Both work.

## Layout

| Path | What |
|---|---|
| `agent/Dockerfile` | node:22-bookworm-slim, `@openai/codex@0.157.1`, curl, python3, procps, dnsutils; user `agent` (uid 1001) |
| `gateway/` | **v1 gateway**: `default.conf.template` (blind `/v1/` proxy) and `15-write-auth.sh` (writes the key include from the mounted file) |
| `gateway_v2/` | **v2 gateway (recommended)**: stock nginx plus bundled njs. `nginx.conf` loads the js module, `filter.js` is a request-shape allowlist, and `default.conf.template` has exact locations |
| `run_gateway.sh`, `run_gateway_v2.sh`, `run_agent.sh`, `run_squid.sh` | Exact docker run lines. The agent runs with `--init --dns 192.0.2.1 --cap-drop ALL --security-opt no-new-privileges` and resource limits, on `spike-data` only. Test 4 and 6 were re-verified with these flags |
| `config.toml.template` | Codex config (copied to `codex_home/config.toml`) |
| `client/appserver.py` | asyncio JSON-RPC client for `docker exec -i … codex app-server` |
| `client/test*.py` | Tests 4, 5, 6, 7 (a to c), 9, 10 and 11 |
| `mcp_server/server.py` | Host MCP server (streamable HTTP, bearer gate, `echo_query`, `ask_helper` with elicitation, `slow_tool`) |
| `research/squid.conf` | Research forward proxy |
| `bypass/probe.py` | Direct-to-gateway probes of hosted tools (run inside the agent) |
| `appserver-schema-0.157.1/` | Output of `codex app-server generate-json-schema` |
| `logs/` | Raw evidence for every test |

## Topology

```
spike-data (--internal)          spike-egress (bridge)
  spike-agent ──http──▶ spike-gateway (alias "gateway") ──https──▶ U-M GPT
                                     └──http──▶ host.docker.internal:8765 (MCP, bound 127.0.0.1)
spike-research (--internal)
  spike-research-agent ─▶ spike-squid (alias "proxy") ──▶ internet (not host, not RFC1918)
```

## Results

| # | Test | Result |
|---|---|---|
| 1 | `/v1/models` from the agent through the gateway, with a dummy key | PASS: 44 models, including gpt-5.5 and gpt-5.6-terra |
| 2 | Egress blocked, including DNS | PASS: curl example.com fails with exit 6 (no DNS). 1.1.1.1 fails with exit 7 (no route). tcpdump shows the embedded DNS answers SERVFAIL locally and **never forwards** on an internal network (a control container on a normal bridge does forward). `gateway` still resolves |
| 3 | No key in the agent's env, inspect output, filesystem or CODEX_HOME | PASS: no key found. A positive control on the gateway found it |
| 4 | app-server over stdio: initialize, thread/start with developerInstructions, turn/start, stream | PASS |
| 5 | turn/interrupt | PASS for turn semantics, **FAIL for process kill**. The turn ends `interrupted` in 20 ms, but `sleep 30` keeps running and completes later. An adapter-side process-group kill fixes it |
| 6 | Resume after the container is recreated | PASS: recalls "teal" |
| 7a | Gateway reaches a host service bound to 127.0.0.1 via host.docker.internal | PASS: no 0.0.0.0 bind needed on Docker Desktop Mac |
| 7b | Codex lists and calls `echo_query` over streamable HTTP with a bearer token | PASS |
| 7c | Elicitation reaches the client and is accepted after 30 s and 150 s with `tool_timeout_sec=15` | PASS. Control: a plain 25 s tool times out at 15 s |
| 8 | Squid research proxy | PASS: internet works; the host, RFC1918 ranges, metadata, IP-encoding tricks and container names are all denied |
| 9 | Timings | Container start 0.5 to 0.6 s; app-server initialize 0.11 to 0.44 s; thread/start 0.06 to 0.10 s; first token 1.2 to 2.5 s |
| 10 | Hosted-tool bypass with the blind `/v1/` proxy | **FAIL (risk)**: U-M runs web_search, remote MCP, code_interpreter and background/store, and it tries to fetch external image URLs. **The v2 njs allowlist blocks all of them**, and Codex still works |
| 11 | Elicitation decline; interrupt while an elicitation is pending | PASS (details below) |
| 12 | app-server killed mid-turn, then thread/resume | PASS (details below). Orphan processes are possible |

## Working configs

### nginx v2 (`gateway_v2/default.conf.template`, abridged)
```nginx
js_import filter from /etc/nginx/njs/filter.js;      # nginx.conf: load_module modules/ngx_http_js_module.so;
server {
  listen 80 default_server;
  client_max_body_size 32m; client_body_buffer_size 32m;   # body must stay in memory for njs
  proxy_http_version 1.1; proxy_ssl_server_name on; proxy_ssl_name ${UPSTREAM_HOST};
  proxy_ssl_verify on; proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
  proxy_buffering off; proxy_cache off; gzip off;
  proxy_read_timeout 900s; proxy_send_timeout 900s; proxy_connect_timeout 15s;

  location = /v1/responses { limit_except POST { deny all; } js_content filter.check; }  # validate → internalRedirect
  location @umgpt_responses {
    proxy_pass https://${UPSTREAM_HOST};            # named location: no URI part allowed
    proxy_set_header Host ${UPSTREAM_HOST}; proxy_set_header Connection "";
    include /etc/nginx/secret/auth.conf;             # proxy_set_header Authorization "Bearer <key>";
  }
  location = /v1/models { limit_except GET { deny all; } proxy_pass https://${UPSTREAM_HOST}/v1/models; ... }
  location /v1/ { return 403; }
  location /mcp {
    proxy_pass http://${MCP_UPSTREAM};               # host.docker.internal:8765
    proxy_set_header Host ${MCP_HOST_HEADER};        # 127.0.0.1:8765 — MCP SDK DNS-rebinding check
    proxy_set_header Connection ""; proxy_request_buffering off;
    proxy_read_timeout 3600s; proxy_send_timeout 3600s;
  }
  location / { return 404; }
}
```
The key gets in through `/docker-entrypoint.d/16-write-auth.sh`, which reads
`/run/secrets/umgpt_key` (a bind mount of a 0600 file) and writes
`/etc/nginx/secret/auth.conf` (umask 077). The key never goes into an env var,
the command line, or `docker inspect`. Name the script `16-*`: the stock image
already ships `15-local-resolvers.envsh`.

`filter.js` allows only what Codex 0.157.1 actually sends (captured in
`logs/codex_request_shape.log`):
- top-level fields: model, instructions, input, tools, tool_choice, parallel_tool_calls, reasoning, store, stream, include, prompt_cache_key, text, client_metadata (plus a few sampling fields);
- `store` must be `false`;
- `include` may only be `reasoning.encrypted_content`;
- tools may only be `function`, `custom`, or `tool_search` with `execution:"client"`;
- no `input_image` unless it is a `data:` URL; no `input_file` with a URL or file_id; no `item_reference`.

Streaming still works through `internalRedirect`: 46 deltas, TTFB 0.5 s. njs uses
the classic engine, which has no `for…of` and no `Set`.

### Codex `config.toml` (the part that matters)
```toml
model = "gpt-5.5"
model_provider = "umgpt"
sandbox_mode = "danger-full-access"          # container is the sandbox
approval_policy = { granular = { sandbox_approval = false, rules = false, mcp_elicitations = true, request_permissions = false, skill_approval = false } }
check_for_update_on_startup = false
web_search = "disabled"
[analytics] enabled = false
[feedback]  enabled = false
[otel]      exporter = "none"
[history]   persistence = "none"
[features]  # all default-ON in 0.157.1 unless listed false here
memories = false; apps = false; plugins = false; remote_plugin = false; browser_use = false
browser_use_external = false; in_app_browser = false; computer_use = false; image_generation = false
multi_agent = false; realtime_conversation = false; tool_suggest = false; skill_mcp_dependency_install = false
workspace_dependencies = false; daemon_auto_start = false; goals = false; auth_elicitation = false
[model_providers.umgpt]
base_url = "http://gateway/v1"; env_key = "UMGPT_DUMMY_KEY"; wire_api = "responses"
[mcp_servers.ihs-data]
url = "http://gateway/mcp"; bearer_token_env_var = "IHS_MCP_TOKEN"
startup_timeout_sec = 20; tool_timeout_sec = 15; default_tools_approval_mode = "approve"
```
Run it as `codex app-server --strict-config`. The flag works on `app-server` but
not on `features`.

### app-server message sequence (JSONL; no `"jsonrpc"` field needed)
```
→ {"id":1,"method":"initialize","params":{"clientInfo":{"name":"datalab","version":"x"},"capabilities":{"experimentalApi":false}}}
← result {userAgent, codexHome, platformOs...}
→ {"method":"initialized"}
→ {"id":2,"method":"thread/start","params":{"cwd":"/work","model":"gpt-5.5","developerInstructions":"..."}}
← result.thread.id ; notif thread/started, mcpServer/startupStatus/updated(starting→ready)
→ {"id":3,"method":"turn/start","params":{"threadId":T,"input":[{"type":"text","text":"...","text_elements":[]}]}}
← result.turn.id ; notifs turn/started, item/started, item/agentMessage/delta*, item/completed, thread/tokenUsage/updated, turn/completed{turn.status}
← server request {"id":0,"method":"mcpServer/elicitation/request","params":{threadId,turnId,serverName,mode:"form",message,requestedSchema,_meta}}
→ {"id":0,"result":{"action":"accept","content":{...}}}      # or {"action":"decline"} / "cancel"
← notif serverRequest/resolved {requestId}
→ {"id":4,"method":"turn/interrupt","params":{"threadId":T,"turnId":U}}   → {} then turn/completed status "interrupted"
→ {"id":5,"method":"thread/resume","params":{"threadId":T,"cwd":"/work","developerInstructions":"..."}}
```

## Findings and gotchas

**DNS (test 2).** On Docker 29.6.2 / Desktop, a container attached only to an
`--internal` network has `nameserver 127.0.0.11`. The embedded resolver answers
container names and returns SERVFAIL for everything else, without forwarding.
tcpdump in the host netns saw no query for the unique leak-test names from the
agent, but did see the control container's queries go to 192.168.65.7. For
defense in depth, `--dns 192.0.2.1` (TEST-NET-1, unroutable) keeps `gateway`
resolvable and external names unresolvable. Recommend using it, and keeping the
tcpdump leak test in the adversarial suite, because older Docker Engines (before
26) did forward.

**Interrupt does not kill commands (test 5).** In Codex 0.157.1, `turn/interrupt`
completes the turn right away, but the running `exec_command` process keeps
running. This holds with `unified_exec` on or off. Its `item/completed` (status
failed, exit 137, or the real output) arrives later under the old turnId. For
Stop to be real, the adapter must kill the process groups under the
app-server's children after it sends interrupt:
`for a in $(pgrep -x codex); do for c in $(pgrep -P $a); do kill -9 -$c; done; done`.
After that, verified 0 live processes and the next turn works. (dash's `kill`
has no `--`.)

**Zombies and orphans.** With `sleep infinity` as PID 1 nothing reaps zombies,
so run the agent with `docker run --init`. If `codex` is SIGKILLed inside the
container, its grandchildren are orphaned and keep running (`sleep 20`
survived). SIGKILL of the host-side `docker exec` client did take the
in-container app-server and children down with it on Docker Desktop Mac.

**Elicitation schema.** Codex 0.157.1 rejects a top-level `title` in
`requestedSchema`, and the MCP SDK's `ctx.elicit(schema=PydanticModel)` always
emits one. Codex then silently auto-**cancels**. The only trace is a stderr
line: `failed to parse typed MCP elicitation schema… unknown field title`. Send
a schema with only `type/properties/required`, via
`ctx.request_context.session.elicit_form(...)`.

**Approval policy matrix (test 7c).**

| approval_policy | tool approval mode | server elicitation reaches client? | MCP tool-call approval prompt? |
|---|---|---|---|
| granular, mcp_elicitations=true | `approve` | yes | no (tools just run) |
| granular, mcp_elicitations=false | any | no: auto-declined | n/a |
| `"never"` | any, even `prompt` | no: auto-declined | no: tools **run** |
| `"on-request"` | `approve` | yes | no |
| granular or on-request | `prompt` or `auto` | yes | yes, **also** delivered as `mcpServer/elicitation/request` with `_meta.codex_approval_kind="mcp_tool_call"` and an empty schema |
| `"untrusted"` | — | config error ("no longer supported") | — |

The recommended setting is the granular policy above with
`default_tools_approval_mode="approve"`. Commands never prompt, and only
DataLab's real elicitations reach the UI. The adapter should still branch on
`_meta.codex_approval_kind`.

**Tool timeout pauses during elicitation.** With a 15 s `tool_timeout_sec`,
elicitations answered after 30 s and 150 s both completed. A plain 25 s tool
failed at 15 s ("timed out awaiting tools/call after 15s").

**Elicitation edge cases.**
- Decline: `{"action":"decline"}` makes the tool return `declined` cleanly, and
  the model reports it.
- Interrupt while pending: the turn ends `interrupted` within 60 ms. Codex emits
  `serverRequest/resolved` for the pending request and sends `cancel` to the MCP
  server, where `ctx.elicit` returns `action=cancel` and the tool ends. No
  `item/completed` is emitted for that mcpToolCall. A late client reply is
  ignored silently. The next turn works. The UI should withdraw the card when
  `serverRequest/resolved` arrives.

**Kill app-server mid-turn, then resume.** A new app-server with `thread/resume`
works. The killed turn shows as `interrupted` in `thread.turns`, and the model
recalls the earlier turn. Clean up orphaned processes on restart (see above).

**Hosted-tool bypass (important).** With the blind `/v1/` proxy, an agent can
curl the gateway directly, and U-M GPT (Azure) will:
- run `web_search` and `web_search_preview` (it searched Ann Arbor weather and
  returned citations);
- call an **arbitrary remote MCP server** (`type:"mcp"`, server_url
  mcp.deepwiki.com: mcp_list_tools and mcp_call completed);
- run `code_interpreter`;
- accept `store:true` with `background:true`, and `/v1/chat/completions`;
- **try to download** external `input_image` URLs (it got a 403 from the image
  host, so the fetch was attempted).

`/v1/files`, `vector_stores` and similar returned 400 or 500 upstream. Every one
of these is an exfiltration channel that bypasses the network boundary.
Gateway v2 blocks all of them with 403, and Codex's own traffic still works
(tests 4, 5 and 7 were re-run through v2).

**Research proxy.**
- curl ignores uppercase `HTTP_PROXY` for http:// URLs, so set lowercase
  `http_proxy` and `https_proxy` too.
- A Squid `dstdomain` ACL does a reverse-DNS lookup on IP-literal URLs. That
  hung on 169.254.169.254 until `-n` was added and the `dst` deny was moved
  first.
- Squid 6 is fatal on an overlapping `host.docker.internal` plus
  `.docker.internal`.
- It can't log to /dev/stdout as the proxy user.
- `squid -k reconfigure` killed the container. Restart it instead.

**Host reachability.** Docker Desktop Mac routes host.docker.internal
(192.168.65.254) to the host's loopback. **Any container on a normal bridge can
reach DataLab's 127.0.0.1 port.** The agent can't, because it is on the
internal network and cannot resolve the name. That is why the research proxy
and the per-session MCP token matter. host.docker.internal also resolves to an
IPv6 address that nginx tries first (harmless "Network unreachable" in the
error log). Not checked on Windows.

**CODEX_HOME contents.** These files persist conversation content, which is
expected for resume:
- `sessions/…/rollout-*.jsonl`, `state_5.sqlite` and `thread_history_1.sqlite`;
- `logs_2.sqlite` (about 2.7k rows, including tool arguments such as SQL text);
- `memories_1.sqlite`, created even with memories off, but its tables are empty.

Codex also writes `installation_id` and sends it in `client_metadata` to U-M.
The first run warns about creating PATH helper binaries under CODEX_HOME.

**Other notes.**
- Features on by default in 0.157.1 include apps, plugins, browser_use,
  computer_use, image_generation, multi_agent, realtime and
  skill_mcp_dependency_install. Disable them explicitly and use
  `--strict-config`.
- `codex doctor` still probes for the latest version, which times out.
- `remoteControl/status/changed` is emitted on start.

## Reproduce
```
python3 extract_secret.py secrets   # writes secrets/umgpt_key (0600); prints only length
docker build -t spike-agent:0.157.1 agent
docker network create --internal spike-data; docker network create spike-egress
./run_gateway_v2.sh; cp config.toml.template codex_home/config.toml; ./run_agent.sh
mcp_server/.venv/bin/python mcp_server/server.py 127.0.0.1 8765 &
cd client && python3 test4_basic.py && python3 test5_interrupt.py && python3 test6_resume.py && python3 test7_mcp.py 30
```

## Teardown and key-leak scan
All spike containers and the networks spike-data, spike-egress and
spike-research were removed. The images were kept: spike-agent:0.157.1,
spike-tcpdump, nginx:alpine and ubuntu/squid. `secrets/umgpt_key` was
deleted after the final scan. The scan of every file under this directory
except `secrets/` (CODEX_HOME, logs, work, configs) found the key: **no**.
The agent container scans (env, `/proc/1/environ`, `docker inspect`,
filesystem) found the key: **no**. `keyscan.py` needs the key file, so
re-extract it before running the scan again.
