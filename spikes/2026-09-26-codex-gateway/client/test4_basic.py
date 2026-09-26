"""Test 4: initialize -> thread/start (developerInstructions) -> turn/start -> stream to turn/completed."""
import asyncio, json, sys, time, pathlib
from appserver import AppServer, text_input, turn_done, agent_text

HERE = pathlib.Path(__file__).parent
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.5"


async def main():
    app = AppServer(extra_args=["--strict-config"], log=print)
    t_spawn = time.monotonic()
    await app.start()
    init, t_init = await app.initialize()
    print(f"initialize ok in {t_init:.2f}s (incl. docker exec spawn {time.monotonic()-t_spawn:.2f}s):",
          json.dumps(init)[:300])

    t = time.monotonic()
    ts = await app.request("thread/start", {
        "cwd": "/work",
        "model": MODEL,
        "developerInstructions": "You are the DataLab spike agent. Always end every answer with the token [DL-SPIKE].",
    })
    thread_id = ts["thread"]["id"]
    print(f"thread/start ok in {time.monotonic()-t:.2f}s thread={thread_id} model={ts.get('model')} "
          f"approval={json.dumps(ts.get('approvalPolicy'))} sandbox={json.dumps(ts.get('sandbox'))[:80]}")

    prompt = "My favourite colour is teal. Reply with one short sentence acknowledging it."
    t_turn = time.monotonic()
    first_delta = app.wait_for(lambda m, p: m == "item/agentMessage/delta", timeout=180)
    tr = await app.request("turn/start", {"threadId": thread_id, "input": text_input(prompt)})
    turn_id = tr["turn"]["id"]
    done = app.wait_for(turn_done(turn_id), timeout=300)
    await first_delta
    t_first = time.monotonic() - t_turn
    _, p = await done
    t_total = time.monotonic() - t_turn
    print(f"turn {turn_id} status={p['turn']['status']} first_token={t_first:.2f}s total={t_total:.2f}s")
    print("agent text:", agent_text(app, turn_id))
    print("event types seen:", dict(app.events))
    (HERE / "thread_id.txt").write_text(thread_id)
    await app.close()
    if app.stderr_lines:
        print("stderr (last 5):", app.stderr_lines[-5:])


asyncio.run(main())
