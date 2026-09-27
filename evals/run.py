"""Run the scientific evaluation set against DataLab on the practice profile.

    uv run --project backend python evals/run.py [--only ID ...] [--repeat N] [--effort medium]

It starts its own practice DataLab (its own data folder and port, so eval
conversations stay out of yours), signs in with the link it prints, and asks
each task's question through the same API the browser uses. Analysis plans
the agent proposes are approved as written (and recorded); research-helper
questions are declined, since evals never reach the internet. The rigor
review is switched off so each answer is graded as given.

Results, with the expected answers, every answer, and the commit, go to
evals/results/<date>-<commit>/, written as each task finishes. Synthetic
data only: the practice profile only ever connects to the synthetic
database, and the answer key checks its marker too.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import expected as answer_key
from tasks import TASKS, Check

ROOT = Path(__file__).resolve().parent.parent
PORT = 8767
TURN_TIMEOUT = 20 * 60


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="task ids to run (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="runs of each task (default 1)")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--model", help="the model to evaluate (default: DataLab's default)")
    args = parser.parse_args()
    tasks = [t for t in TASKS if not args.only or t.id in args.only]

    expected = answer_key.compute()
    commit = _git("rev-parse", "HEAD")
    # Results already written aren't "changes" to the code under test.
    dirty = bool(_git("status", "--porcelain", "--", ".", ":(exclude)evals/results"))
    started = datetime.now(UTC)
    name = f"{started:%Y-%m-%d-%H%M%S}-{commit[:7]}{'-dirty' if dirty else ''}"
    out = ROOT / "evals" / "results" / name
    out.mkdir(parents=True)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))

    home = Path.home() / (
        "Library/Application Support/DataLab"
        if sys.platform == "darwin"
        else ".local/share/datalab"
    )
    # A fresh data folder each run: nothing carries over between runs.
    data_dir = Path(tempfile.mkdtemp(prefix="practice-evals-", dir=home))
    # The run's containers carry this instance's label, so a practice DataLab
    # running alongside is never touched, and the run cleans up only its own.
    instance = _instance(data_dir)
    env = {**os.environ, "DATALAB_PROFILE": "practice", "DATALAB_DATA_DIR": str(data_dir)}
    # The catalog, rebuilt from the synthetic database (metadata only), so the
    # column comments the agent reads always match the data.
    catalog = data_dir / "catalog"
    built = subprocess.run(
        ["uv", "run", "datalab", "--profile", "practice", "catalog", "--from-database", "--out", str(catalog)],
        cwd=ROOT / "backend", env=env, capture_output=True, text=True,
    )  # fmt: skip
    if built.returncode != 0 or not any(catalog.rglob("*.yml")):
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        sys.exit(f"Couldn't build the catalog from the synthetic database:\n{built.stderr[-2000:]}")
    (data_dir / "settings.toml").write_text(
        f"port = {PORT}\ncatalog_dir = {json.dumps(str(catalog))}\n"
        + (f"default_model = {json.dumps(args.model)}\n" if args.model else "")
    )
    log = (out / "server.log").open("w")
    server = subprocess.Popen(
        ["uv", "run", "datalab", "--profile", "practice", "serve", "--no-browser"],
        cwd=ROOT / "backend", env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )  # fmt: skip
    info: dict = {
        "started_at": started.isoformat(timespec="seconds"),
        "commit": commit,
        "uncommitted_changes": dirty,
        "effort": args.effort,
        "model": args.model or "default",
        "repeat": args.repeat,
        "catalog_sha256": _tree_hash(catalog),
        "catalog_tables": sum(1 for _ in catalog.rglob("*.yml")),
    }
    results: list[dict] = []
    try:
        client = _sign_in(out / "server.log")
        health = _get(client, "/api/health")
        if health.get("profile") != "practice":
            sys.exit("The eval server isn't the practice profile.")
        info |= {"server": health, "models": _get(client, "/api/models")}
        for task in tasks:
            for attempt in range(1, args.repeat + 1):
                print(f"· {task.id} ({attempt}/{args.repeat}) …", flush=True)
                try:
                    result = _run_task(client, task, args.effort, expected) | {"attempt": attempt}
                except httpx.HTTPError as error:
                    # One task's trouble (a 409, a timeout) doesn't end the run.
                    result = _failed(task, attempt, f"{type(error).__name__}: {error}")
                results.append(result)
                verdict = "PASS" if result["passed"] else "FAIL"
                print(f"  {verdict} in {result['seconds']:.0f}s", flush=True)
                (out / f"{task.id}-{attempt}.json").write_text(json.dumps(result, indent=2))
                _write_summary(out, info, results)
    finally:
        info["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        _write_summary(out, info, results)
        _stop(server, instance)
        log.close()
        # The eval conversations' workspaces: the results keep what matters.
        shutil.rmtree(data_dir, ignore_errors=True)
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed. Results: {out.relative_to(ROOT)}")
    return 0


def _sign_in(log_path: Path) -> httpx.Client:
    deadline = time.time() + 120
    link = None
    while time.time() < deadline and link is None:
        text = log_path.read_text()
        match = re.search(rf"http://127\.0\.0\.1:{PORT}/sign-in\?token=[\w-]+", text)
        link = match.group(0) if match else None
        time.sleep(0.5)
    if link is None:
        sys.exit(f"The eval server didn't start; see {log_path}")
    client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
    for _ in range(60):
        try:
            client.get(link.removeprefix(f"http://127.0.0.1:{PORT}"))
            break
        except httpx.TransportError:
            time.sleep(0.5)
    _get(client, "/api/conversations")  # signed in, or this raises
    return client


def _get(client: httpx.Client, path: str, **params):
    response = client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def _run_task(client: httpx.Client, task, effort: str, expected: dict) -> dict:
    begun = time.time()
    created = client.post(
        "/api/conversations", json={"mode": task.mode, "title": f"eval: {task.id}"}
    )
    created.raise_for_status()
    cid = created.json()["id"]
    client.patch(f"/api/conversations/{cid}", json={"rigor_review": False}).raise_for_status()
    client.post(
        f"/api/conversations/{cid}/messages", json={"text": task.prompt, "effort": effort}
    ).raise_for_status()
    events: list[dict] = []
    approvals: list[dict] = []
    answered: set[str] = set()
    stopped_at: float | None = None
    proposed: dict | None = None  # a plan task's plan
    follow_up = ""
    follow_up_after = 0  # the seq after which the follow-up turn's events start
    turn_began = begun
    while True:
        # Only what's new: the API returns events a page (1,000) at a time.
        while page := _get(
            client, f"/api/conversations/{cid}/events", after=events[-1]["seq"] if events else 0
        ):
            events += page
        for event in events:
            data = event["data"]
            if event["type"] != "approval_requested" or data["id"] in answered:
                continue
            answered.add(data["id"])
            # Plans are approved as the agent wrote them; the helper is never used.
            # A plan task is over once its plan is proposed: it's sent back and
            # the turn stopped, since the plan is what's graded.
            if data["kind"] == "analysis_plan" and not task.plan_only:
                body = {"approve": True, "plan": data["plan"]}
            else:
                body = {"approve": False}
            reply = client.post(f"/api/conversations/{cid}/approvals/{data['id']}", json=body)
            if data["kind"] == "analysis_plan" and task.plan_only and proposed is None:
                proposed = data["plan"]
                client.post(f"/api/conversations/{cid}/stop")
                stopped_at = time.time()
            approvals.append(
                {
                    "kind": data["kind"],
                    "approved": body["approve"],
                    "status": reply.status_code,
                    **data,
                }
            )
        done = [e for e in events if e["type"] == "turn_done"]
        if task.plan_only and done:
            break  # its plan was proposed (and the turn stopped), or it never proposed one
        if done and not follow_up and _asks_to_continue(events):
            # Analysis mode pilots first and asks before the full run; a
            # researcher would say go ahead. Once, and recorded.
            follow_up = FOLLOW_UP
            follow_up_after = events[-1]["seq"]
            turn_began = time.time()  # the full run gets its own time budget
            client.post(
                f"/api/conversations/{cid}/messages", json={"text": follow_up, "effort": effort}
            ).raise_for_status()
            continue
        if done and (not follow_up or len(done) >= 2):
            break
        if stopped_at is None and time.time() - turn_began > TURN_TIMEOUT:
            client.post(f"/api/conversations/{cid}/stop")
            stopped_at = time.time()
        if stopped_at is not None and time.time() - stopped_at > 60:
            break
        time.sleep(3)

    finished = stopped_at is None and any(e["type"] == "turn_done" for e in events)
    # After a follow-up, only the full run's answer counts, never the pilot's.
    answers = [
        e["data"]
        for e in events
        if e["type"] == "answer" and e["data"].get("text") and e["seq"] > follow_up_after
    ]
    # The final answer; a turn that finished without phases ends on its last message.
    final = [a for a in answers if a.get("phase") == "final_answer"]
    if not final and any(e["type"] == "turn_done" for e in events):
        final = [a for a in answers if a.get("phase") is None][-1:]
    answer = final[-1]["text"] if final else ""
    if task.plan_only:
        # The plan is what's graded; the turn was stopped once it was proposed.
        checks = task.grade({"plan": proposed, "answer": answer}, expected)
        finished = proposed is not None or any(e["type"] == "turn_done" for e in events)
        answer = json.dumps(proposed, indent=2) if proposed else answer
    else:
        checks = task.grade(answer, expected) if answer else []
    # Graded on the first turn, and the answer says it's only a pilot.
    # Still a pilot: on the first turn by any pilot label; after the
    # follow-up only by an explicit one (full answers mention the pilot).
    still_pilot = _PILOT_AGAIN if follow_up else _PILOT_ONLY
    if not task.plan_only and answer and re.search(still_pilot, answer[:600], re.IGNORECASE):
        checks.append(Check("ran in full", False, "the answer is a pilot or preliminary result"))
    queries = _get(client, f"/api/conversations/{cid}/data-accessed")
    usage = [e for e in events if e["type"] == "usage"]
    # The last usage report of each turn (the pilot's, then the full run's).
    per_turn = [u["data"] for u in usage if u["seq"] <= follow_up_after][-1:] if follow_up else []
    per_turn += [u["data"] for u in usage if u["seq"] > follow_up_after][-1:]
    return {
        "task": task.id,
        "mode": task.mode,
        "prompt": task.prompt,
        "tests": task.tests,
        # A timed-out turn never passes, whatever its commentary said.
        "passed": finished and bool(checks) and all(c.passed for c in checks),
        "checks": [asdict(c) for c in checks],
        "answer": answer,
        "finished": finished,
        "seconds": time.time() - begun,
        "approvals": approvals,
        "follow_up": follow_up,
        "queries": [q.get("sql_text") for q in queries],
        "usage": per_turn,
        "conversation": cid,
    }


# What a researcher says when the agent pauses after a pilot: nothing about
# the answer, and self-contained, so the second answer stands on its own.
FOLLOW_UP = (
    "Yes, go ahead as you recommend and finish the full analysis, then give me the final "
    "answer to my original question."
)
_ASKS = (
    r"\b(pilot|scale|full (analysis|cohort|run|sample)|proceed|go ahead|continue|all (\d+ )?participants"
    r"|everyone|whole cohort|rest of|shall I|should I|want me to|would you like)\b"
)


# An answer that labels itself a pilot (not one that mentions an earlier pilot).
_PILOT_ONLY = (
    r"\b(pilot|preliminary) (results?|findings?|estimates?|analysis)\b|\bthis (is|was) (a|the) pilot\b"
    r"|\bpilot (of|on|with) \d+|\bpilot subset\b|\bpilot only\b|\bnot yet the (cohort|full) answer\b"
)


_PILOT_AGAIN = (
    r"\bpilot only\b|\bnot yet the (cohort|full) answer\b|\bthis (is|was) (a|the) pilot\b"
)


def _asks_to_continue(events: list[dict]) -> bool:
    """Whether the turn ended by asking the person whether to go on (after a pilot)."""
    answers = [e["data"]["text"] for e in events if e["type"] == "answer" and e["data"].get("text")]
    ending = answers[-1][-400:] if answers else ""
    said_pilot = any(re.search(r"\b(pilot|preliminary)\b", a, re.IGNORECASE) for a in answers)
    # A pilot answer, whether or not it ends with a question ("I stopped
    # before the full run"), or a closing question after a pilot.
    pilot_only = bool(answers) and bool(re.search(_PILOT_ONLY, answers[-1][:600], re.IGNORECASE))
    return pilot_only or (
        said_pilot and "?" in ending and bool(re.search(_ASKS, ending, re.IGNORECASE))
    )


def _failed(task, attempt: int, error: str) -> dict:
    return {
        "task": task.id, "mode": task.mode, "prompt": task.prompt, "tests": task.tests,
        "passed": False, "checks": [], "answer": f"(the run failed: {error})", "finished": False,
        "seconds": 0.0, "approvals": [], "follow_up": "", "queries": [], "usage": None, "conversation": None,
        "attempt": attempt,
    }  # fmt: skip


def _write_summary(out: Path, info: dict, results: list[dict]) -> None:
    (out / "run.json").write_text(json.dumps(info, indent=2))
    (out / "SUMMARY.md").write_text(_summary(info, results))


def _summary(info: dict, results: list[dict]) -> str:
    passed = sum(r["passed"] for r in results)
    by_task: dict[str, list[dict]] = {}
    for r in results:
        by_task.setdefault(r["task"], []).append(r)
    changes = " (with uncommitted changes)" if info["uncommitted_changes"] else ""
    lines = [
        f"# Evaluation run {info['started_at']}",
        "",
        f"Commit `{info['commit'][:12]}`{changes}, effort {info['effort']}, "
        f"model {info.get('models', {}).get('default', '?')}, "
        f"DataLab {info.get('server', {}).get('version', '?')}, practice profile (synthetic data "
        f"only), catalog sha256 `{info['catalog_sha256'][:12]}`."
        + ("" if info.get("finished_at") else " **Still running, or stopped early.**"),
        "",
        f"**{passed} of {len(results)} passed.** A pass means every mechanical check held; "
        "each answer below still needs a person's read.",
        "",
        "| Task | Tests | Passed | Checks (last run) |",
        "|---|---|---|---|",
    ]
    for task, runs in by_task.items():
        last = runs[-1]
        checks = "; ".join(f"{'✓' if c['passed'] else '✗'} {c['name']}" for c in last["checks"])
        passes = f"{sum(r['passed'] for r in runs)}/{len(runs)}"
        lines.append(f"| {task} | {last['tests']} | {passes} | {checks or 'no answer'} |")
    for r in results:
        lines += ["", f"## {r['task']} (run {r['attempt']})", "", f"> {r['prompt']}", ""]
        for c in r["checks"]:
            lines.append(f"- {'✓' if c['passed'] else '✗'} **{c['name']}**: {c['detail']}")
        if r.get("follow_up"):
            lines.append(f"- The agent paused to ask; the runner replied: “{r['follow_up']}”")
        for a in r["approvals"]:
            verdict = "approved" if a["approved"] else "declined"
            lines.append(f"- {a['kind'].replace('_', ' ')} {verdict} (HTTP {a['status']})")
        ended = "" if r["finished"] else ", **did not finish**"
        lines.append(f"- {len(r['queries'])} queries, {r['seconds']:.0f}s{ended}")
        body = (r["answer"] or "(none)").splitlines()
        lines += ["", "Answer:", "", *(f"    {line}" for line in body)]
    return "\n".join(lines) + "\n"


def _instance(data_dir: Path) -> str:
    """The instance label DataLab gives this data folder's containers."""
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from datalab.sessions.containers import instance_of

    return instance_of(data_dir)


def _containers(instance: str) -> list[str]:
    return subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=datalab.instance={instance}"],
        capture_output=True, text=True,
    ).stdout.split()  # fmt: skip


def _networks(instance: str) -> list[str]:
    return subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", f"label=datalab.instance={instance}"],
        capture_output=True, text=True,
    ).stdout.split()  # fmt: skip


def _stop(server: subprocess.Popen, instance: str) -> None:
    """Stop the server and everything it started (uv, then datalab).

    SIGTERM, not SIGINT: uv passes a signal on and the group gets it too, and
    a second SIGINT makes uvicorn skip its shutdown, which is what removes
    the session containers. Repeated SIGTERMs don't.
    """
    if server.poll() is not None:
        pass  # it had already exited (it didn't start, say): nothing to signal
    else:
        try:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=90)
        except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(server.pid, signal.SIGKILL)
    # Containers this run made that DataLab's shutdown didn't remove.
    if left := _containers(instance):
        subprocess.run(["docker", "rm", "-f", *left], capture_output=True)
    if networks := _networks(instance):
        subprocess.run(["docker", "network", "rm", *networks], capture_output=True)


def _tree_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(folder)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
