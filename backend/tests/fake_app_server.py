"""A stand-in for `codex app-server`, for testing SessionRuntime without Docker.

It speaks the same JSON-RPC-over-lines protocol. FAKE_MODE picks a behaviour:
- normal: a turn streams one answer and completes;
- slow: a turn keeps running until interrupted;
- stop_race: like slow, but a model request lands before the interrupt, so
  the relay refuses it and the turn ends failed, with Codex's error;
- crash: the process exits in the middle of a turn;
- no_resume: thread/resume fails;
- elicit: a turn asks for a research-helper approval and answers with the reply;
- elicit_other: a turn sends an approval request that isn't DataLab's.
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
    send({"method": method, "params": {"threadId": "thread-1", **params}})


for line in sys.stdin:
    message = json.loads(line)
    if LOG and "method" in message:
        with open(LOG, "a") as log:
            log.write(
                json.dumps({"method": message["method"], "params": message.get("params")}) + "\n"
            )
    method, request_id = message.get("method"), message.get("id")
    if method is None and request_id == 900:
        # DataLab's reply to our approval request.
        result = message.get("result") or {}
        notify("item/started", item={"type": "agentMessage", "id": "m2", "phase": "final_answer"})
        notify("item/agentMessage/delta", itemId="m2", delta=str(result.get("action")))
        notify("turn/completed", turn={"id": "turn-1", "status": "completed"})
        continue
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
        if MODE == "other_thread":
            # Another thread's news, which must not end or show in this turn.
            notify("turn/started", threadId="thread-2", turn={"id": "turn-x"})
            notify("item/agentMessage/delta", threadId="thread-2", itemId="x", delta="leak")
            notify("turn/completed", threadId="thread-2", turn={"id": "turn-x", "status": "failed"})
        if MODE in ("elicit", "elicit_other"):
            text = {
                "datalab": os.environ.get("FAKE_KIND", "research_helper"),
                "approval": os.environ.get("FAKE_APPROVAL", ""),
            }
            server = "ihs-data" if MODE == "elicit" else "someone-else"
            send(
                {
                    "id": 900,
                    "method": "mcpServer/elicitation/request",
                    "params": {"serverName": server, "message": json.dumps(text), "mode": "form"},
                }
            )
            continue
        if MODE == "crash":
            time.sleep(0.1)
            sys.exit(1)
        if MODE in ("normal", "no_resume", "other_thread"):
            notify(
                "item/started", item={"type": "agentMessage", "id": "m1", "phase": "final_answer"}
            )
            notify("item/agentMessage/delta", itemId="m1", delta="done")
            notify("turn/completed", turn={"id": "turn-1", "status": "completed"})
    elif method == "review/start":
        # Like Codex 0.157: the id returned isn't the id of the turn that runs.
        send({"id": request_id, "result": {"turn": {"id": "turn-r-returned"}}})
        if MODE == "late_review":
            time.sleep(0.5)  # a Stop can land before Codex says which turn runs
        notify("turn/started", turn={"id": "turn-r"})
        if MODE in ("slow_review", "late_review"):
            continue
        notify("item/completed", item={"type": "exitedReviewMode", "id": "r1", "review": "1. ok"})
        notify("turn/completed", turn={"id": "turn-r", "status": "completed"})
    elif method == "turn/interrupt":
        wanted = (message.get("params") or {}).get("turnId")
        if MODE in ("slow_review", "late_review") and wanted != "turn-r":
            error = {"code": -32600, "message": "expected active turn id"}
            send({"id": request_id, "error": error})
            continue
        send({"id": request_id, "result": {}})
        if MODE in ("slow_review", "late_review"):
            notify("turn/completed", turn={"id": "turn-r", "status": "interrupted"})
            continue
        if MODE == "stop_race":
            refused = {"message": "unexpected status 409 Conflict: this request's turn is over"}
            notify("error", error=refused, willRetry=False)
            notify("turn/completed", turn={"id": "turn-1", "status": "failed", "error": refused})
            continue
        if MODE == "elicit":
            notify("serverRequest/resolved", requestId=900)
        notify("turn/completed", turn={"id": "turn-1", "status": "interrupted"})
    else:
        send({"id": request_id, "result": {}})
