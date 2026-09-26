"""A stand-in for `codex app-server`, for testing SessionRuntime without Docker.

It speaks the same JSON-RPC-over-lines protocol. FAKE_MODE picks a behaviour:
- normal: a turn streams one answer and completes;
- slow: a turn keeps running until interrupted;
- crash: the process exits in the middle of a turn;
- no_resume: thread/resume fails.
Every request received is appended to FAKE_LOG, one JSON object per line.
"""

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_MODE", "normal")
LOG = os.environ.get("FAKE_LOG")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method, **params):
    send({"method": method, "params": params})


for line in sys.stdin:
    message = json.loads(line)
    if LOG and "method" in message:
        with open(LOG, "a") as log:
            log.write(
                json.dumps({"method": message["method"], "params": message.get("params")}) + "\n"
            )
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        send({"id": request_id, "result": {}})
    elif method == "thread/start":
        send({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
    elif method == "thread/resume":
        if MODE == "no_resume":
            send({"id": request_id, "error": {"code": -32600, "message": "thread not found"}})
        else:
            send({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn-1"}}})
        notify("turn/started", turn={"id": "turn-1"})
        if MODE == "crash":
            time.sleep(0.1)
            sys.exit(1)
        if MODE in ("normal", "no_resume"):
            notify(
                "item/started", item={"type": "agentMessage", "id": "m1", "phase": "final_answer"}
            )
            notify("item/agentMessage/delta", itemId="m1", delta="done")
            notify("turn/completed", turn={"id": "turn-1", "status": "completed"})
    elif method == "turn/interrupt":
        send({"id": request_id, "result": {}})
        notify("turn/completed", turn={"id": "turn-1", "status": "interrupted"})
    else:
        send({"id": request_id, "result": {}})
