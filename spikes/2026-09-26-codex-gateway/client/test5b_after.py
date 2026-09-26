"""Test 5b: what happens to the interrupted command over time; does a new turn / app-server exit kill it?"""
import asyncio, json, subprocess, sys
from appserver import AppServer, text_input, turn_done
def live():
    out = subprocess.run(["docker","exec","spike-agent","ps","-eo","pid,ppid,pgid,args"],capture_output=True,text=True).stdout
    return [l.strip() for l in out.splitlines() if "sleep 30" in l and "defunct" not in l]
async def main():
    app = AppServer(extra_args=["--strict-config", *sys.argv[1:]])
    await app.start(); await app.initialize()
    tid = (await app.request("thread/start", {"cwd": "/work"}))["thread"]["id"]
    started = app.wait_for(lambda m,p: m=="item/started" and p["item"].get("type")=="commandExecution", 180)
    turn = (await app.request("turn/start", {"threadId": tid, "input": text_input("Run exactly: `sleep 30 && echo SLEPT-OK` and report output.")}))["turn"]["id"]
    done = app.wait_for(turn_done(turn), 120)
    await started; await asyncio.sleep(3)
    print("app-server pid tree:", subprocess.run(["docker","exec","spike-agent","sh","-c","pgrep -a codex"],capture_output=True,text=True).stdout.strip())
    print("live before:", live())
    n0 = len(app.notifications)
    await app.request("turn/interrupt", {"threadId": tid, "turnId": turn}); await done
    print(f"[{app.t():.1f}] interrupted; live:", live())
    # start a new turn right away
    t2 = (await app.request("turn/start", {"threadId": tid, "input": text_input("Just say OK.")}))["turn"]["id"]
    _, p2 = await app.wait_for(turn_done(t2), 120)
    print(f"[{app.t():.1f}] second turn {p2['turn']['status']}; live:", live())
    await asyncio.sleep(max(0, 36 - app.t()))
    print(f"[{app.t():.1f}] after natural end; live:", live())
    for t, m, p in app.notifications[n0:]:
        if m in ("item/completed","item/started","process/exited","item/commandExecution/outputDelta","error","warning"):
            it = p.get("item", {})
            print(f"   [{t:.1f}] {m} {it.get('type','')} {it.get('status','')} {json.dumps(p)[:160] if not it else ''}")
    await app.close()
asyncio.run(main())
