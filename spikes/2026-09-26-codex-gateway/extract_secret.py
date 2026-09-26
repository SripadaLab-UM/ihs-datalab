import os, sys
env = "/Users/ataxali/work/srijan-knowledge-graph/um-gpt-local-proxy/.env"
out = sys.argv[1]
vals = {}
for line in open(env):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, v = line.split("=", 1)
    k = k.strip().removeprefix("export ").strip()
    v = v.strip().strip('"').strip("'")
    vals[k] = v
key = vals.get("UMICH_API_KEY", "")
base = vals.get("UMICH_BASE_URL", "")
fd = os.open(os.path.join(out, "umgpt_key"), os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
os.write(fd, key.encode()); os.close(fd)
with open(os.path.join(out, "base_url"), "w") as f: f.write(base)
print("key_len", len(key), "base_url", base)
