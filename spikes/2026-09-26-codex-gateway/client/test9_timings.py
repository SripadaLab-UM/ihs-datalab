"""Timings: cold container start -> app-server initialize -> thread/start -> first token -> turn done."""
import asyncio, json, pathlib, subprocess, statistics, sys, time
from appserver import AppServer, text_input, turn_done

SPIKE = pathlib.Path(__file__).resolve().parent.parent
MODELS = sys.argv[1:] or ["gpt-5.5"]


async def one(model, cold):
    r = {"model": model, "cold": cold}
    if cold:
        t = time.monotonic()
        subprocess.run([str(SPIKE / "run_agent.sh")], capture_output=True)
        # wait until exec works
        while subprocess.run(["docker", "exec", "spike-agent", "true"], capture_output=True).returncode:
            time.sleep(0.05)
        r["container_start_s"] = round(time.monotonic() - t, 2)
    app = AppServer(extra_args=["--strict-config"])
    t = time.monotonic()
    await app.start()
    await app.initialize()
    r["appserver_init_s"] = round(time.monotonic() - t, 2)
    t = time.monotonic()
    tid = (await app.request("thread/start", {"cwd": "/work", "model": model}))["thread"]["id"]
    r["thread_start_s"] = round(time.monotonic() - t, 2)
    t = time.monotonic()
    first = app.wait_for(lambda m, p: m == "item/agentMessage/delta", 180)
    turn = (await app.request("turn/start", {"threadId": tid, "input": text_input("Reply with exactly: hello")}))["turn"]["id"]
    done = app.wait_for(turn_done(turn), 180)
    await first
    r["first_token_s"] = round(time.monotonic() - t, 2)
    _, p = await done
    r["turn_total_s"] = round(time.monotonic() - t, 2)
    r["status"] = p["turn"]["status"]
    await app.close()
    return r


async def main():
    rows = []
    for model in MODELS:
        for i in range(3):
            rows.append(await one(model, cold=(i == 0)))
            print(json.dumps(rows[-1]))
    for model in MODELS:
        ft = [r["first_token_s"] for r in rows if r["model"] == model]
        print(f"{model}: first_token median={statistics.median(ft):.2f}s  runs={ft}")


asyncio.run(main())
