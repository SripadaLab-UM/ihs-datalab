"""Test 5: turn/interrupt while a shell command (sleep 30) is running."""
import asyncio, json, subprocess, sys, time
from appserver import AppServer, text_input, turn_done


def ps():
    return subprocess.run(["docker", "exec", "spike-agent", "ps", "-eo", "pid,ppid,etime,args"],
                          capture_output=True, text=True).stdout


async def main():
    app = AppServer(extra_args=["--strict-config", *sys.argv[1:]], log=print)
    await app.start()
    await app.initialize()
    ts = await app.request("thread/start", {"cwd": "/work", "developerInstructions": "You are a test agent."})
    tid = ts["thread"]["id"]
    cmd_started = app.wait_for(lambda m, p: m == "item/started" and p.get("item", {}).get("type") == "commandExecution", 180)
    tr = await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Run exactly this shell command and wait for it: `sleep 30 && echo SLEPT-OK`. Then tell me the output.")})
    turn_id = tr["turn"]["id"]
    done = app.wait_for(turn_done(turn_id), 120)
    _, p = await cmd_started
    print(f"[{app.t():6.2f}] command started: {json.dumps(p['item'].get('command'))[:120]}")
    await asyncio.sleep(5)
    print("ps before interrupt:\n" + "\n".join(l for l in ps().splitlines() if "sleep" in l or "PID" in l))
    t = time.monotonic()
    r = await app.request("turn/interrupt", {"threadId": tid, "turnId": turn_id})
    print(f"[{app.t():6.2f}] turn/interrupt response: {r}")
    _, p = await done
    print(f"[{app.t():6.2f}] turn/completed status={p['turn']['status']} ({time.monotonic()-t:.2f}s after interrupt)")
    for _, m, pp in app.notifications:
        if m == "item/completed" and pp["item"].get("type") == "commandExecution":
            it = pp["item"]
            print("  commandExecution completed: status=", it.get("status"), "exitCode=", it.get("exitCode"),
                  "output=", repr((it.get("aggregatedOutput") or "")[:80]))
    # Mitigation: Codex 0.157.1 does not kill the running command on interrupt.
    # Kill every process group whose leader is a child of the app-server.
    kill = ("for a in $(pgrep -x codex); do for c in $(pgrep -P $a); do "
            "kill -9 -$c && echo killed-pgid-$c; done; done")
    print(f"[{app.t():6.2f}] adapter kill:", subprocess.run(["docker", "exec", "spike-agent", "sh", "-c", kill],
          capture_output=True, text=True).stdout.strip())
    for i in range(12):
        after = [l for l in ps().splitlines() if "sleep 30" in l and "defunct" not in l]
        print(f"[{app.t():6.2f}] live 'sleep 30' procs:", len(after))
        if not after: break
        await asyncio.sleep(1)
    t2 = (await app.request("turn/start", {"threadId": tid, "input": text_input("Say OK.")}))["turn"]["id"]
    _, p2 = await app.wait_for(turn_done(t2), 120)
    print(f"[{app.t():6.2f}] follow-up turn after kill: {p2['turn']['status']}")
    for _, m, pp in app.notifications:
        if m == "item/completed" and pp["item"].get("type") == "commandExecution":
            it = pp["item"]
            print("  late commandExecution item/completed: status=", it.get("status"), "exitCode=", it.get("exitCode"))
    print("events:", dict(app.events))
    await app.close()


asyncio.run(main())
