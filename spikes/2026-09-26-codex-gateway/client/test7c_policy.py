"""Approval-policy matrix: does the elicitation reach the client? Are MCP tool calls prompted/rejected?"""
import asyncio, json, sys
from appserver import AppServer, text_input, turn_done
async def on_req(method, params):
    print(f"   -> server request {method}: {json.dumps(params)[:int(__import__('os').environ.get('REQLEN','300'))]}")
    await asyncio.sleep(2)
    if method == "mcpServer/elicitation/request":
        return {"action": "accept", "content": {"approve": True, "edited_question": ""}}
    return {"decision": "accept"}
async def main():
    app = AppServer(extra_args=["--strict-config", *sys.argv[1:]], on_server_request=on_req)
    await app.start(); await app.initialize()
    tid = (await app.request("thread/start", {"cwd": "/work"}))["thread"]["id"]
    turn = (await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Call the ihs-data tool echo_query with sql 'SELECT 1 FROM dual', then call ask_helper with question 'ping'. Report both results verbatim. Do not retry.")}))["turn"]["id"]
    _, p = await app.wait_for(turn_done(turn), 300)
    for t, m, pp in app.notifications:
        if m == "item/completed" and pp["item"].get("type") == "mcpToolCall":
            it = pp["item"]; r = json.dumps(it.get("result"))
            print(f"   {it['tool']}: status={it['status']} err={json.dumps(it.get('error'))[:160]} result={'approved' if 'approved' in r else ('declined' if 'declined' in r else r[:120])}")
    print("   server requests:", [m for _, m, _ in app.server_requests] or "none")
    await app.close()
asyncio.run(main())
