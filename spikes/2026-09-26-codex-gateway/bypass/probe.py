"""Runs INSIDE the agent container: bypass Codex and hit the gateway directly with hosted-tool request shapes."""
import json, sys, urllib.request, urllib.error
BASE = "http://gateway/v1"
def call(method, path, body=None, timeout=90):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return None, repr(e)
def summarize(name, status, text):
    out = {"test": name, "http": status}
    try:
        d = json.loads(text)
    except Exception:
        out["body"] = text[:300]; print(json.dumps(out)); return
    if "error" in d and d["error"]:
        out["error"] = json.dumps(d["error"])[:400]
    items = d.get("output") or []
    out["output_item_types"] = [i.get("type") for i in items]
    for i in items:
        if i.get("type") == "web_search_call":
            out.setdefault("web_search_calls", []).append({"status": i.get("status"), "action": json.dumps(i.get("action"))[:200]})
        if i.get("type") in ("mcp_list_tools", "mcp_call"):
            out.setdefault("mcp_items", []).append(json.dumps(i)[:300])
        if i.get("type") == "message":
            for c in i.get("content", []):
                anns = c.get("annotations") or []
                cites = [a.get("url") for a in anns if a.get("type") == "url_citation"]
                out["text"] = (c.get("text") or "")[:300]
                if cites: out["url_citations"] = cites[:5]
    if "data" in d: out["data_len"] = len(d["data"]); out["data_sample"] = json.dumps(d["data"][:2])[:200]
    print(json.dumps(out))
M = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.5"
tests = sys.argv[2].split(",") if len(sys.argv) > 2 else ["ws", "ws_preview", "img", "mcp", "files", "misc"]
if "ws" in tests:
    summarize("web_search", *call("POST", "/responses", {"model": M, "store": False,
        "tools": [{"type": "web_search"}], "tool_choice": "auto",
        "input": "Use web search: what is today's weather forecast in Ann Arbor, Michigan? Cite a source URL."}))
if "ws_preview" in tests:
    summarize("web_search_preview", *call("POST", "/responses", {"model": M, "store": False,
        "tools": [{"type": "web_search_preview"}],
        "input": "Use web search: what is today's weather forecast in Ann Arbor, Michigan? Cite a source URL."}))
if "img" in tests:
    summarize("input_image_external_url", *call("POST", "/responses", {"model": M, "store": False, "input": [
        {"role": "user", "content": [
            {"type": "input_text", "text": "Describe this image in 5 words."},
            {"type": "input_image", "image_url": "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"}]}]}))
if "mcp" in tests:
    summarize("remote_mcp_tool", *call("POST", "/responses", {"model": M, "store": False,
        "tools": [{"type": "mcp", "server_label": "deepwiki", "server_url": "https://mcp.deepwiki.com/mcp", "require_approval": "never"}],
        "input": "Use the deepwiki MCP tool to tell me, in one sentence, what the repository modelcontextprotocol/python-sdk is."}))
if "files" in tests:
    for path in ["/files", "/vector_stores", "/batches", "/assistants", "/responses/resp_doesnotexist", "/containers", "/fine_tuning/jobs", "/uploads"]:
        summarize("GET " + path, *call("GET", path, timeout=30))
if "misc" in tests:
    summarize("code_interpreter", *call("POST", "/responses", {"model": M, "store": False,
        "tools": [{"type": "code_interpreter", "container": {"type": "auto"}}],
        "input": "Use the python tool to compute 2**20 and report it."}))
    summarize("file_search(no store)", *call("POST", "/responses", {"model": M, "store": False,
        "tools": [{"type": "file_search", "vector_store_ids": ["vs_doesnotexist"]}], "input": "search"}))
    summarize("store:true + background", *call("POST", "/responses", {"model": M, "store": True, "background": True,
        "input": "Say OK."}))
    summarize("chat/completions", *call("POST", "/chat/completions", {"model": M,
        "messages": [{"role": "user", "content": "Say OK."}]}))
