"""Kill the app-server mid-turn (simulated DataLab restart), then thread/resume.
  how=client    : SIGKILL the host-side `docker exec` client (what happens if DataLab crashes)
  how=container : SIGKILL `codex` inside the container
"""
import asyncio, json, os, signal, subprocess, sys
from appserver import AppServer, text_input, turn_done, agent_text

HOW = sys.argv[1] if len(sys.argv) > 1 else "client"


def codex_procs():
    out = subprocess.run(["docker", "exec", "spike-agent", "ps", "-eo", "pid,ppid,etime,args"],
                         capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if ("codex" in l or "sleep 20" in l) and "ps -eo" not in l]


async def main():
    app = AppServer(extra_args=["--strict-config"])
    await app.start(); await app.initialize()
    tid = (await app.request("thread/start", {"cwd": "/work"}))["thread"]["id"]
    # turn 1 completes normally (gives us history to recall)
    t1 = (await app.request("turn/start", {"threadId": tid, "input": text_input("Remember the code word PELICAN. Reply 'noted'.")}))["turn"]["id"]
    await app.wait_for(turn_done(t1), 120)
    # turn 2 is in flight when we kill
    started = app.wait_for(lambda m, p: m == "item/started" and p["item"].get("type") == "commandExecution", 120)
    t2 = (await app.request("turn/start", {"threadId": tid, "input": text_input(
        "Run `sleep 20 && echo WOKE` in the shell, then say DONE.")}))["turn"]["id"]
    await started
    await asyncio.sleep(2)
    print("before kill:", codex_procs())
    if HOW == "client":
        os.kill(app.proc.pid, signal.SIGKILL)   # host-side docker exec client dies
    else:
        subprocess.run(["docker", "exec", "spike-agent", "pkill", "-9", "-x", "codex"])
    await asyncio.sleep(3)
    print(f"after kill ({HOW}):", codex_procs())
    orphan = [l for l in codex_procs() if "app-server" in l]
    if orphan:
        print("  NOTE: in-container app-server survived the client kill -> killing it explicitly")
        subprocess.run(["docker", "exec", "spike-agent", "pkill", "-9", "-x", "codex"])
        await asyncio.sleep(1)
        print("  after explicit kill:", codex_procs())

    app2 = AppServer(extra_args=["--strict-config"])
    await app2.start(); await app2.initialize()
    r = await app2.request("thread/resume", {"threadId": tid, "cwd": "/work"})
    turns = r["thread"].get("turns") or []
    print("resumed; turns in history:", [(t["id"][-6:], t.get("status")) for t in turns])
    t3 = (await app2.request("turn/start", {"threadId": tid, "input": text_input(
        "What was the code word, and did the sleep command in the previous turn finish?")}))["turn"]["id"]
    _, p = await app2.wait_for(turn_done(t3), 180)
    txt = agent_text(app2, t3)
    print(f"follow-up turn {p['turn']['status']}: {txt[:300]}")
    print("RECALL:", "PASS" if "pelican" in txt.lower() else "FAIL")
    await app2.close()
    print("final procs:", codex_procs())


asyncio.run(main())
