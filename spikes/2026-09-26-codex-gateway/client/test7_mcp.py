"""Test 7: MCP over streamable HTTP via gateway; elicitation answered by our client after a delay."""
import asyncio, json, sys, time
from appserver import AppServer, text_input, turn_done, agent_text

DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
EXTRA = sys.argv[2:]


async def on_server_request(method, params):
    if method == "mcpServer/elicitation/request":
        print(f"   elicitation from {params.get('serverName')} turn={params.get('turnId')} mode={params.get('mode')}")
        print(f"   message: {params.get('message')!r}")
        print(f"   requestedSchema: {json.dumps(params.get('requestedSchema'))[:300]}")
        print(f"   ... sleeping {DELAY}s before accepting")
        await asyncio.sleep(DELAY)
        return {"action": "accept", "content": {"approve": True, "edited_question": ""}}
    print(f"   UNEXPECTED server request {method}: {json.dumps(params)[:400]}")
    # generic approvals: approve (schema: decision)
    return {"decision": "accept"}


async def main():
    app = AppServer(extra_args=["--strict-config", *EXTRA], log=print, on_server_request=on_server_request)
    await app.start()
    await app.initialize()
    ts = await app.request("thread/start", {"cwd": "/work", "developerInstructions":
                           "You have an ihs-data MCP server. Use its tools when asked."})
    tid = ts["thread"]["id"]
    st = await app.wait_for(lambda m, p: m == "mcpServer/startupStatus/updated" and p.get("status") not in ("starting",), 60)
    print("mcp startup:", json.dumps(st[1])[:300])
    status = await app.request("mcpServerStatus/list", {})
    for s in status.get("data", []):
        print("mcp server:", s.get("name"), "tools:", sorted((s.get("tools") or {}).keys()), "auth:", s.get("authStatus"))

    tr = await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Step 1: call the ihs-data tool echo_query with sql 'SELECT cohort, COUNT(*) n FROM ihs.participants GROUP BY cohort'. "
        "Step 2: call the ihs-data tool ask_helper with question 'What is the recommended R package for survival analysis?'. "
        "Then report both tool results briefly, including approval_wait_seconds.")})
    turn_id = tr["turn"]["id"]
    _, p = await app.wait_for(turn_done(turn_id), 600)
    print(f"[{app.t():6.2f}] turn status={p['turn']['status']} error={p['turn'].get('error')}")
    for t, m, pp in app.notifications:
        if m in ("item/started", "item/completed") and pp["item"].get("type") == "mcpToolCall":
            it = pp["item"]
            print(f"   [{t:6.2f}] {m} mcpToolCall {it.get('server')}.{it.get('tool')} status={it.get('status')} "
                  f"dur={it.get('durationMs')} err={json.dumps(it.get('error'))[:200]} "
                  f"result={json.dumps(it.get('result'))[:250]}")
        if m == "serverRequest/resolved":
            print(f"   [{t:6.2f}] serverRequest/resolved {json.dumps(pp)[:200]}")
    for t, m, pp in app.server_requests:
        print(f"   [{t:6.2f}] server->client request: {m}")
    print("answer:", agent_text(app, turn_id))
    print("events:", dict(app.events))
    await app.close()
    errs = [l for l in app.stderr_lines if "ERROR" in l or "WARN" in l]
    if errs:
        print("stderr ERROR/WARN (last 5):", [e[-300:] for e in errs[-5:]])


asyncio.run(main())
