"""Control: a plain slow MCP tool (25s) with tool_timeout_sec=15 must time out."""
import asyncio, json, sys
from appserver import AppServer, text_input, turn_done
async def main():
    app = AppServer(extra_args=["--strict-config", *sys.argv[1:]])
    await app.start(); await app.initialize()
    tid = (await app.request("thread/start", {"cwd": "/work"}))["thread"]["id"]
    turn = (await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Call the ihs-data tool slow_tool with seconds=25 exactly once, then report the result or error verbatim. Do not retry.")}))["turn"]["id"]
    _, p = await app.wait_for(turn_done(turn), 300)
    for t, m, pp in app.notifications:
        if m == "item/completed" and pp["item"].get("type") == "mcpToolCall":
            it = pp["item"]; print(f"[{t:.1f}] {it['tool']} status={it['status']} dur={it.get('durationMs')} err={json.dumps(it.get('error'))[:200]}")
    await app.close()
asyncio.run(main())
