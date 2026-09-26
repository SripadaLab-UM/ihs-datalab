"""Minimal asyncio client for `codex app-server` over stdio via `docker exec -i`.

Spike code: JSONL framing, one JSON-RPC object per line (Codex omits "jsonrpc").
"""
import asyncio
import json
import time
from collections import Counter


class AppServer:
    def __init__(self, container="spike-agent", extra_args=(), log=None, on_server_request=None):
        self.container = container
        self.extra_args = list(extra_args)
        self.proc = None
        self._id = 0
        self._pending = {}
        self.events = Counter()
        self.notifications = []          # (t, method, params)
        self.server_requests = []        # (t, method, params)
        self.log = log or (lambda *a: None)
        self.on_server_request = on_server_request  # async fn(method, params) -> result
        self._waiters = []               # (predicate, future)
        self.t0 = time.monotonic()
        self.stderr_lines = []

    def t(self):
        return time.monotonic() - self.t0

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", self.container, "codex", "app-server", *self.extra_args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        self._err = asyncio.create_task(self._err_loop())

    async def _err_loop(self):
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            s = line.decode(errors="replace").rstrip()
            self.stderr_lines.append(s)

    async def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method, params=None, timeout=120):
        self._id += 1
        rid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"id": rid, "method": method, "params": params or {}})
        return await asyncio.wait_for(fut, timeout)

    async def notify(self, method, params=None):
        msg = {"method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    def wait_for(self, predicate, timeout=300):
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append((predicate, fut))
        return asyncio.wait_for(fut, timeout)

    async def _handle_server_request(self, msg):
        method, params = msg["method"], msg.get("params", {})
        self.server_requests.append((self.t(), method, params))
        self.events["REQ " + method] += 1
        self.log(f"[{self.t():7.2f}] <= server request {method} id={msg['id']}")
        try:
            if self.on_server_request:
                result = await self.on_server_request(method, params)
            else:
                result = {"decision": "decline"}
            await self._send({"id": msg["id"], "result": result})
            self.log(f"[{self.t():7.2f}] => replied to {method}: {json.dumps(result)[:200]}")
        except Exception as e:  # noqa
            await self._send({"id": msg["id"], "error": {"code": -32000, "message": str(e)}})

    async def _read_loop(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                for _, f in self._pending.items():
                    if not f.done():
                        f.set_exception(EOFError("app-server closed"))
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.log("non-json:", line[:200])
                continue
            if "id" in msg and "method" in msg:
                asyncio.create_task(self._handle_server_request(msg))
            elif "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(json.dumps(msg["error"])))
                    else:
                        fut.set_result(msg.get("result"))
            else:
                method, params = msg.get("method"), msg.get("params", {})
                self.events[method] += 1
                self.notifications.append((self.t(), method, params))
                for pred, fut in list(self._waiters):
                    if not fut.done() and pred(method, params):
                        fut.set_result((method, params))
                        self._waiters.remove((pred, fut))

    async def initialize(self):
        t = time.monotonic()
        res = await self.request("initialize", {
            "clientInfo": {"name": "datalab-spike", "title": "DataLab spike", "version": "0.0.1"},
            "capabilities": {"experimentalApi": False},
        })
        await self.notify("initialized")
        return res, time.monotonic() - t

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self.proc.kill()


def text_input(s):
    return [{"type": "text", "text": s, "text_elements": []}]


def turn_done(turn_id):
    return lambda m, p: m == "turn/completed" and p.get("turn", {}).get("id") == turn_id


def agent_text(app, turn_id=None):
    """Concatenate completed agentMessage items (optionally for one turn)."""
    out = []
    for _, m, p in app.notifications:
        if m == "item/completed" and p.get("item", {}).get("type") == "agentMessage":
            if turn_id is None or p.get("turnId") == turn_id:
                out.append(p["item"].get("text", ""))
    return "\n".join(out)
