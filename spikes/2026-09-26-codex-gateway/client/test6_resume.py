"""Test 6: after the container is recreated (same CODEX_HOME), thread/resume the test-4 thread and check recall."""
import asyncio, json, pathlib, time
from appserver import AppServer, text_input, turn_done, agent_text

HERE = pathlib.Path(__file__).parent


async def main():
    tid = (HERE / "thread_id.txt").read_text().strip()
    app = AppServer(extra_args=["--strict-config"], log=print)
    t = time.monotonic()
    await app.start()
    _, ti = await app.initialize()
    print(f"initialize {ti:.2f}s")
    t = time.monotonic()
    r = await app.request("thread/resume", {"threadId": tid, "cwd": "/work",
                                            "developerInstructions": "You are the DataLab spike agent. Always end every answer with the token [DL-SPIKE]."})
    th = r["thread"]
    print(f"thread/resume {time.monotonic()-t:.2f}s id={th['id']} turns_in_history={len(th.get('turns') or [])}")
    tr = await app.request("turn/start", {"threadId": tid, "input": text_input(
        "What did I ask you before, and what is my favourite colour?")})
    turn_id = tr["turn"]["id"]
    _, p = await app.wait_for(turn_done(turn_id), 300)
    txt = agent_text(app, turn_id)
    print(f"turn status={p['turn']['status']}\nanswer: {txt}")
    print("RECALL:", "PASS" if "teal" in txt.lower() else "FAIL")
    await app.close()


asyncio.run(main())
