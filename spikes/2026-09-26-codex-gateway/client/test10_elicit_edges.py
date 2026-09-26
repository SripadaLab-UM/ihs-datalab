"""Elicitation edge cases.
  mode=decline   : reply {"action":"decline"} -> tool returns cleanly to the model
  mode=interrupt : send turn/interrupt while the elicitation is still pending (never answer it)
"""
import asyncio, json, subprocess, sys
from appserver import AppServer, text_input, turn_done, agent_text

MODE = sys.argv[1] if len(sys.argv) > 1 else "decline"
pending = {}


async def on_req(method, params):
    if method != "mcpServer/elicitation/request":
        return {"decision": "decline"}
    if MODE == "decline":
        await asyncio.sleep(3)
        return {"action": "decline"}
    # interrupt mode: park forever (host would show a card); the test interrupts the turn
    fut = asyncio.get_running_loop().create_future()
    pending["fut"] = fut
    return await fut


def mcp_log_tail(n=4):
    return subprocess.run(["tail", f"-{n}", "../logs/mcp_server_127.log"], capture_output=True, text=True).stdout


async def main():
    app = AppServer(extra_args=["--strict-config"], log=print, on_server_request=on_req)
    await app.start(); await app.initialize()
    tid = (await app.request("thread/start", {"cwd": "/work"}))["thread"]["id"]
    turn = (await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Call the ihs-data tool ask_helper with question 'ping' once. Report the result verbatim. Do not retry.")}))["turn"]["id"]
    done = app.wait_for(turn_done(turn), 300)
    if MODE == "interrupt":
        while not app.server_requests:
            await asyncio.sleep(0.2)
        await asyncio.sleep(5)
        print(f"[{app.t():6.2f}] elicitation pending; sending turn/interrupt")
        print("   mcp server log before interrupt:\n" + mcp_log_tail(2))
        r = await app.request("turn/interrupt", {"threadId": tid, "turnId": turn})
        print(f"[{app.t():6.2f}] interrupt response: {r}")
    _, p = await done
    print(f"[{app.t():6.2f}] turn status={p['turn']['status']}")
    await asyncio.sleep(3)
    for t, m, pp in app.notifications:
        if m == "item/completed" and pp["item"].get("type") == "mcpToolCall":
            it = pp["item"]
            print(f"   [{t:6.2f}] mcpToolCall {it['tool']} status={it['status']} err={json.dumps(it.get('error'))[:200]} result={json.dumps(it.get('result'))[:200]}")
        if m == "serverRequest/resolved":
            print(f"   [{t:6.2f}] serverRequest/resolved {json.dumps(pp)}")
    print("   pending elicitation future done?", pending.get("fut").done() if pending.get("fut") else "n/a")
    print("   answer:", agent_text(app, turn)[:300])
    print("   mcp server log after:\n" + mcp_log_tail(4))
    if MODE == "interrupt":
        # does the thread still work afterwards?
        t2 = (await app.request("turn/start", {"threadId": tid, "input": text_input("Say OK.")}))["turn"]["id"]
        _, p2 = await app.wait_for(turn_done(t2), 120)
        print(f"   follow-up turn: {p2['turn']['status']} -> {agent_text(app, t2)[:80]!r}")
        if pending.get("fut") and not pending["fut"].done():
            pending["fut"].set_result({"action": "cancel"})
            await asyncio.sleep(1)
            print("   late reply to stale elicitation sent (action=cancel); stderr tail:",
                  [l[-200:] for l in app.stderr_lines[-2:]])
    print("   events:", dict(app.events))
    await app.close()


asyncio.run(main())
