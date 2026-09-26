"""Scan for the real key without ever printing it. Prints only yes/no per target."""
import os, subprocess, sys
S = os.path.dirname(os.path.abspath(__file__))
keyfile = os.path.join(S, "secrets", "umgpt_key")
key = open(keyfile, "rb").read().strip()
assert len(key) > 10
def check(name, data):
    print(f"{name}: key present = {'YES' if key in data else 'no'}")
mode = sys.argv[1] if len(sys.argv) > 1 else "agent"
if mode == "agent":
    c = "spike-agent"
    check("agent `env` (PID1 + exec)", subprocess.run(["docker","exec",c,"sh","-c","env; cat /proc/1/environ"],capture_output=True).stdout)
    check("agent docker inspect", subprocess.run(["docker","inspect",c],capture_output=True).stdout)
    check("agent filesystem grep (/codex_home /work /home /etc /tmp)", subprocess.run(
        ["docker","exec","-i",c,"sh","-c","grep -rlF -f /dev/stdin /codex_home /work /home /etc /tmp 2>/dev/null | head -1"],
        input=key+b"\n", capture_output=True).stdout.strip() and key or b"")
    blob = b""
    for root, _, files in os.walk(os.path.join(S, "codex_home")):
        for f in files:
            try: blob += open(os.path.join(root, f), "rb").read()
            except Exception: pass
    check("host CODEX_HOME dir", blob)
elif mode == "outputs":
    hits = 0
    for root, dirs, files in os.walk(S):
        if os.path.abspath(root).startswith(os.path.join(S, "secrets")):
            continue
        for f in files:
            p = os.path.join(root, f)
            try:
                if key in open(p, "rb").read(): hits += 1
            except Exception: pass
    print(f"scratch outputs (excluding secrets/): key present = {'YES (%d files)' % hits if hits else 'no'}")
