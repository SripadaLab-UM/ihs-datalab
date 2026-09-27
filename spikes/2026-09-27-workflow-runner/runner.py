"""A throwaway workflow runner: SQL result CSV -> R summary -> QC -> deliver.

Questions 2, 4 and 6 of the spike. It is not app code: it borrows DataLab's
real `exports.export()` and `DestinationStore` for delivery, and fakes the
data service with SQLite over a made-up CSV (make_fake_data.py). No database
is queried.

    python runner.py run workflows/wearable_weekly_summary.yaml --source fake.csv [--param k=v]
    python runner.py again runs/<run_id> --source fake_later.csv
    python runner.py replay runs/<run_id>
    python runner.py compare runs/<a> runs/<b>

Containers are labelled datalab.spike=runner and removed when they exit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from check_reads import check_reads, tables_in

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
WRAPPER = HERE / "datalab"
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "backend" / "src"))
from datalab import exports  # noqa: E402

LABEL = "datalab.spike=runner"
DEFAULT_IMAGE = "datalab-agent:dev"
STEP_TIMEOUT = 30 * 60
MAX_RESULT_BYTES = 64 * 1024
MAX_LOG_BYTES = 1024 * 1024

# The proposed step sandbox. See probe_sandbox.sh for what each one is for.
SANDBOX_FLAGS = [
    "--rm", "--pull", "never", "--label", LABEL,
    "--network", "none",
    "--read-only",
    "--tmpfs", "/tmp:rw,size=1g,mode=1777,nosuid,nodev,noexec",
    "--user", "10004:10004",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--init",
    "--pids-limit", "256", "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
    # Deterministic numerics: one thread for BLAS/OpenMP/data.table.
    "--env", "OMP_NUM_THREADS=1", "--env", "OPENBLAS_NUM_THREADS=1",
    "--env", "R_DATATABLE_NUM_THREADS=1", "--env", "TZ=Etc/UTC",
    "--workdir", "/run/out",
]  # fmt: skip


# ---------------------------------------------------------------- helpers


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def csv_facts(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    return {"rows": rows, "columns": header}


def file_facts(path: Path) -> dict[str, Any]:
    facts: dict[str, Any] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if path.suffix.lower() == ".csv":
        facts.update(csv_facts(path))
    return facts


def step_seed(run_seed: int, step_id: str) -> int:
    """Each step's seed, from the run's: adding a step never changes another's."""
    return int(hashlib.sha256(f"{run_seed}:{step_id}".encode()).hexdigest()[:8], 16) % 2**31


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def image_facts(image: str) -> dict[str, Any]:
    """The image's ID, and a fingerprint of the R packages in it (cached per ID)."""
    image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
    cache = RUNS / f"image-facts-{image_id.split(':')[-1][:16]}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    listing = docker(
        "run", *SANDBOX_FLAGS, image_id, "Rscript", "--vanilla", "-e",
        'ip <- installed.packages()[, c("Package", "Version")];'
        'ip <- ip[order(ip[, "Package"]), ];'
        'cat(R.version.string, "\\n"); cat(paste(ip[, 1], ip[, 2], sep = "=="), sep = "\\n")',
    ).stdout  # fmt: skip
    lines = listing.splitlines()
    facts = {
        "ref": image,
        "id": image_id,
        "r_version": lines[0].strip(),
        "r_packages": len(lines) - 1,
        "r_packages_sha256": hashlib.sha256("\n".join(lines[1:]).encode()).hexdigest(),
    }
    cache.write_text(json.dumps(facts, indent=2))
    return facts


def repo_commit() -> str:
    """Stand-in for the ihs-pipelines commit: this worktree's HEAD."""
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip()


def new_run_dir() -> tuple[str, Path]:
    # Always a new folder: Docker Desktop often can't mount a folder that was
    # deleted and recreated at the same path (race_probe.sh).
    run_id = f"run_{datetime.now(UTC):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
    path = RUNS / run_id
    path.mkdir(parents=True)
    return run_id, path


# ------------------------------------------------------- fake data service


class FakeDataService:
    """Stands in for DataService.run_query: same inputs, a CSV like oracle.py writes."""

    def __init__(self, source_csv: Path) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH ':memory:' AS IHS_SYN")
        self.db.create_function("TO_DATE", 2, lambda s, _fmt: f"{s} 00:00:00")
        with source_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            self.db.execute(f"CREATE TABLE IHS_SYN.DAILY_WEARABLE ({', '.join(header)})")
            self.db.executemany(
                f"INSERT INTO IHS_SYN.DAILY_WEARABLE VALUES ({', '.join('?' * len(header))})",
                ([v if v != "" else None for v in row] for row in reader),
            )

    def run_query(
        self, *, session_id: str, sql: str, binds: dict, results_dir: Path, allowed: set[str]
    ) -> dict[str, Any]:
        tables, problems = tables_in(sql)
        # The runner's extra rule: a workflow's SQL reads only what it declared.
        if problems or not tables <= allowed:
            raise RuntimeError(f"Refused: {problems or sorted(tables - allowed)}")
        query_id = f"q_{datetime.now(UTC):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
        results_dir.mkdir(parents=True, exist_ok=True)
        out = results_dir / f"{query_id}.csv"
        cursor = self.db.execute(sql, binds)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)  # "\r\n" line ends, as oracle.py writes
            writer.writerow([d[0] for d in cursor.description])
            rows = 0
            for row in cursor:
                writer.writerow(["" if v is None else str(v) for v in row])
                rows += 1
        return {"query_id": query_id, "result_path": out, "row_count": rows,
                "tables": sorted(tables), "session_id": session_id}  # fmt: skip


# ------------------------------------------------------------ built-in QC


def resolve(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return params[value[1:]]
    return value


def builtin_qc(config: dict[str, Any], path: Path, params: dict[str, Any]) -> list[dict]:
    """DataLab's own checks. Messages hold counts and column names, never values."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
    checks: list[dict] = []

    def check(check_id: str, passed: bool, observed: Any, expected: Any, message: str) -> None:
        checks.append({"id": check_id, "status": "pass" if passed else "fail",
                       "observed": observed, "expected": expected, "message": message})  # fmt: skip

    if "min_rows" in config:
        want = resolve(config["min_rows"], params)
        check("min_rows", len(rows) >= want, len(rows), f">= {want}", f"{len(rows)} rows")
    if "required_columns" in config:
        missing = [c for c in config["required_columns"] if c not in header]
        check("required_columns", not missing, missing, [], f"missing columns: {missing}")
    for column, limit in (config.get("max_missing") or {}).items():
        share = sum(1 for r in rows if r.get(column, "") == "") / max(len(rows), 1)
        limit = resolve(limit, params)
        check(f"max_missing:{column}", share <= limit, round(share, 4), f"<= {limit}",
              f"{column} is {share:.1%} missing")  # fmt: skip
    for column in config.get("no_missing") or []:
        empty = sum(1 for r in rows if r.get(column, "") == "")
        check(f"no_missing:{column}", empty == 0, empty, 0, f"{empty} empty values in {column}")
    if "unique_by" in config:
        keys = [tuple(r.get(c, "") for c in config["unique_by"]) for r in rows]
        duplicates = len(keys) - len(set(keys))
        check("unique_by", duplicates == 0, duplicates, 0,
              f"{duplicates} duplicate rows by {config['unique_by']}")  # fmt: skip
    if "small_cells" in config:
        rule = config["small_cells"]
        column, minimum = rule["count_column"], resolve(rule["min"], params)
        small = sum(1 for r in rows if r.get(column, "") != "" and 0 < float(r[column]) < minimum)
        check("small_cells", small == 0, small, 0,
              f"{small} rows with {column} between 1 and {minimum - 1}")  # fmt: skip
    return checks


# ---------------------------------------------------------- container step


def run_container_step(
    run_dir: Path,
    run_id: str,
    step: dict[str, Any],
    script: str,
    inputs: dict[str, dict[str, Any]],
    outputs: dict[str, str],
    params: dict[str, Any],
    seed: int,
    image_id: str,
) -> dict[str, Any]:
    step_dir = run_dir / "steps" / step["id"]
    spec_dir, scratch, result_dir, out_dir = (step_dir / n for n in ("spec", "scratch", "result", "outputs"))
    for folder in (spec_dir, scratch, result_dir, out_dir):
        folder.mkdir(parents=True)
    # On native Linux Docker the container's user must be able to write these.
    for folder in (scratch, result_dir):
        folder.chmod(0o777)
    spec = {
        "step": step["id"],
        "type": "qc" if "qc" in step else "r",
        "seed": seed,
        "params": params,
        "inputs": {
            name: {"path": f"/run/in/{name}/{i['file']}", **{k: i[k] for k in ("sha256", "bytes", "rows", "columns") if k in i}}
            for name, i in inputs.items()
        },
        "outputs": {name: f"/run/out/{file}" for name, file in outputs.items()},
    }  # fmt: skip
    (spec_dir / "step.json").write_text(json.dumps(spec, indent=2))
    (spec_dir / "script.R").write_text(script)
    mounts = [
        "--mount", f"type=bind,source={WRAPPER},target=/run/datalab,readonly",
        "--mount", f"type=bind,source={spec_dir},target=/run/step,readonly",
        "--mount", f"type=bind,source={scratch},target=/run/out",
        "--mount", f"type=bind,source={result_dir},target=/run/result",
    ]  # fmt: skip
    for name, i in inputs.items():
        mounts += ["--mount", f"type=bind,source={i['dir']},target=/run/in/{name},readonly"]
    name = f"spike-runner-{run_id[-6:]}-{step['id']}".replace("_", "-")
    argv = ["docker", "run", "--name", name, *SANDBOX_FLAGS, *mounts, image_id,
            "Rscript", "--vanilla", "/run/datalab/run_step.R"]  # fmt: skip
    started = time.monotonic()
    try:
        done = subprocess.run(argv, capture_output=True, timeout=STEP_TIMEOUT)
        exit_code, log = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as error:
        docker("kill", name, check=False)
        exit_code, log = -1, (error.stdout or b"") + (error.stderr or b"")
    elapsed = round(time.monotonic() - started, 2)
    (step_dir / "log.txt").write_bytes(log[:MAX_LOG_BYTES])

    result: dict[str, Any] = {"status": "failed", "messages": [{"level": "error", "text": "No result.json."}]}
    result_file = result_dir / "result.json"
    if result_file.is_file() and not result_file.is_symlink() and result_file.stat().st_size <= MAX_RESULT_BYTES:
        result = json.loads(result_file.read_text())
    # Only declared outputs come back: regular files, never links.
    collected: dict[str, Any] = {}
    missing: list[str] = []
    for output_name, file in outputs.items():
        source = scratch / file
        try:
            info = os.lstat(source)
        except FileNotFoundError:
            missing.append(file)
            continue
        if not stat.S_ISREG(info.st_mode):
            missing.append(file)
            continue
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as reader, open(out_dir / file, "xb") as writer:
            shutil.copyfileobj(reader, writer)
        collected[output_name] = {"file": file, "dir": str(out_dir), **file_facts(out_dir / file)}
    undeclared = sorted(set(os.listdir(scratch)) - set(outputs.values()))
    shutil.rmtree(scratch)
    ok = exit_code == 0 and result.get("status") == "ok" and not missing
    return {
        "status": "succeeded" if ok else "failed",
        "exit_code": exit_code,
        "elapsed_s": elapsed,
        "seed": seed,
        "result": result,
        "outputs": collected,
        "missing_outputs": missing,
        "undeclared_files_dropped": len(undeclared),
    }


# ----------------------------------------------------------------- runner


def load_params(workflow: dict, overrides: dict[str, str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, spec in (workflow.get("parameters") or {}).items():
        raw = overrides.get(name, spec.get("default"))
        kind = spec.get("type")
        if kind == "integer":
            params[name] = int(raw)
        elif kind == "boolean":
            params[name] = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes")
        else:
            params[name] = str(raw)
    unknown = set(overrides) - set(params)
    if unknown:
        raise SystemExit(f"Unknown parameters: {sorted(unknown)}")
    return params


def execute(
    workflow_text: str,
    workflow_path: str,
    params: dict[str, Any],
    seed: int,
    *,
    mode: str,
    source: Path | None,
    original: dict[str, Any] | None,
    image: str,
    deliver: bool,
) -> dict[str, Any]:
    workflow = yaml.safe_load(workflow_text)
    problems = check_reads(workflow)
    if problems:
        raise SystemExit("The workflow file doesn't pass the check:\n  " + "\n  ".join(problems))
    run_id, run_dir = new_run_dir()
    (run_dir / "workflow.yaml").write_text(workflow_text)
    facts = image_facts(image)
    record: dict[str, Any] = {
        "record_version": 1,
        "run_id": run_id,
        "mode": mode,
        "of_run": original["run_id"] if original else None,
        "started_at": now(),
        "status": "running",
        "workflow": {
            "name": workflow["name"],
            "path": workflow_path,
            "repo_commit": original["workflow"]["repo_commit"] if original else repo_commit(),
            "sha256": hashlib.sha256(workflow_text.encode()).hexdigest(),
        },
        "image": facts,
        "runner": {
            "wrapper_sha256": sha256_file(WRAPPER / "run_step.R"),
            "sandbox_flags": SANDBOX_FLAGS,
        },
        "params": params,
        "seed": seed,
        "reads": workflow["reads"],
        "steps": [],
        "delivery": None,
    }
    data = FakeDataService(source) if source else None
    allowed = {r.upper() for r in workflow["reads"]}
    done: dict[str, dict[str, Any]] = {}  # step id -> its outputs
    failed = False
    for position, step in enumerate(workflow["steps"]):
        entry: dict[str, Any] = {"id": step["id"], "position": position, "started_at": now()}
        kind = next(k for k in ("sql", "r", "pipeline", "qc") if k in step)
        entry["type"] = kind
        if failed:
            entry["status"] = "skipped"
            record["steps"].append(entry)
            continue
        started = time.monotonic()
        if kind == "sql":
            out_dir = run_dir / "steps" / step["id"] / "outputs"
            out_dir.mkdir(parents=True)
            target = out_dir / step["output"]
            binds = {b: params[b] for b in _binds(step["sql"], params)}
            entry.update(sql=step["sql"], binds=binds)
            if mode == "replay":
                # The original extract, checked against its recorded checksum.
                old = next(s for s in original["steps"] if s["id"] == step["id"])
                kept = Path(original["_run_dir"]) / "steps" / step["id"] / "outputs" / step["output"]
                if sha256_file(kept) != old["outputs"]["final"]["sha256"]:
                    raise SystemExit(f"The kept input for {step['id']} doesn't match its checksum.")
                shutil.copyfile(kept, target)
                entry["query_id"] = old.get("query_id")
                entry["replayed_from"] = original["run_id"]
            else:
                outcome = data.run_query(session_id=run_id, sql=step["sql"], binds=binds,
                                         results_dir=run_dir / "query-results", allowed=allowed)  # fmt: skip
                os.replace(outcome["result_path"], target)
                entry.update(query_id=outcome["query_id"], tables=outcome["tables"])
            entry["outputs"] = {"final": {"file": step["output"], "dir": str(out_dir), **file_facts(target)}}
            entry["status"] = "succeeded"
        elif kind == "r" or (kind == "qc" and "r" in step["qc"]):
            script = step["r"] if kind == "r" else step["qc"]["r"]
            wanted = step.get("inputs") if kind == "r" else step["qc"].get("inputs")
            inputs = {name: done[upstream]["final"] for name, upstream in (wanted or {}).items()}
            outputs = {"final": step["output"]} if "output" in step else {}
            entry["type"] = "r" if kind == "r" else "qc_custom"
            entry["inputs"] = {n: {"step": u, "sha256": done[u]["final"]["sha256"]} for n, u in (wanted or {}).items()}
            entry.update(run_container_step(run_dir, run_id, step, script, inputs, outputs,
                                            params, step_seed(seed, step["id"]), facts["id"]))  # fmt: skip
        elif kind == "qc":
            upstream = step["qc"]["file"]
            path = Path(done[upstream]["final"]["dir"]) / done[upstream]["final"]["file"]
            checks = builtin_qc(step["qc"], path, params)
            entry.update(type="qc_builtin", inputs={"file": {"step": upstream, "sha256": done[upstream]["final"]["sha256"]}},
                         result={"status": "ok" if all(c["status"] == "pass" for c in checks) else "failed", "checks": checks})  # fmt: skip
            entry["status"] = "succeeded" if entry["result"]["status"] == "ok" else "failed"
        else:
            raise SystemExit("Pipeline steps aren't in this prototype (see pipeline_probe.sh).")
        entry.setdefault("elapsed_s", round(time.monotonic() - started, 2))
        entry["finished_at"] = now()
        done[step["id"]] = entry.get("outputs", {})
        failed = entry["status"] != "succeeded"
        record["steps"].append(entry)

    record["status"] = "failed" if failed else "succeeded"
    spec = workflow.get("deliver")
    if spec and not failed and deliver:
        record["delivery"] = deliver_outputs(spec, record, run_dir, done)
    elif spec:
        record["delivery"] = {"skipped": "a step failed" if failed else f"not delivered ({mode})"}
    if original:
        record["comparison"] = compare(original, record)
    record["finished_at"] = now()
    (run_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def _binds(sql: str, params: dict[str, Any]) -> list[str]:
    return [name for name in params if f":{name}" in sql]


def deliver_outputs(spec: dict, record: dict, run_dir: Path, done: dict) -> dict[str, Any]:
    """Deliver through DataLab's real export() and DestinationStore."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript((REPO / "backend/src/datalab/db/migrations/0004_exports.sql").read_text())
    store = exports.DestinationStore(db)
    practice = RUNS / "destinations" / "practice"
    practice.mkdir(parents=True, exist_ok=True)
    store.add("practice-folder", practice)
    # A workflow names a destination; each computer maps the name to a folder.
    destination = next((d for d in store.list() if d.name == spec["destination"]), None)
    if destination is None:
        raise SystemExit(f"No export destination named {spec['destination']!r} on this computer.")
    folder = Path(destination.path) / exports.safe_name(spec["folder"])
    folder.mkdir(exist_ok=True)
    sources = []
    for step_id in spec["files"]:
        out = done[step_id]["final"]
        path = Path(out["dir"]) / out["file"]
        sources.append(exports.ExportSource(
            open=lambda p=path: os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)),
            path=out["file"],
            container_path=f"{record['run_id']}/{step_id}/{out['file']}",
        ))  # fmt: skip
    result = exports.export(
        folder,
        title=record["workflow"]["name"],
        tag=record["run_id"],
        sources=sources,
        about={
            "profile": "practice",
            "contains_study_data": False,
            "workflow": {**record["workflow"], "image": record["image"]["id"]},
            "run": {"id": record["run_id"], "mode": record["mode"], "of_run": record["of_run"],
                    "params": record["params"],
                    "qc": [{"step": s["id"], "status": s["status"]} for s in record["steps"] if s["type"].startswith("qc")]},
        },
    )  # fmt: skip
    return {
        "destination_id": destination.id,
        "destination_name": destination.name,
        "destination_path": destination.path,
        "folder": str(result.folder),
        "files": result.files,
        "manifest_sha256": sha256_file(result.folder / exports.MANIFEST),
    }


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Byte-for-byte: every output of every step, and every QC result."""
    rows = []
    for old in a["steps"]:
        new = next((s for s in b["steps"] if s["id"] == old["id"]), {})
        for name, out in (old.get("outputs") or {}).items():
            mine = (new.get("outputs") or {}).get(name, {})
            same_bytes = False
            if mine:
                pa = Path(out["dir"]) / out["file"]
                pb = Path(mine["dir"]) / mine["file"]
                same_bytes = pa.read_bytes() == pb.read_bytes()
            rows.append({"step": old["id"], "output": out["file"], "a": out["sha256"][:12],
                         "b": mine.get("sha256", "")[:12], "identical": same_bytes})  # fmt: skip
        if "checks" in (old.get("result") or {}):
            same = old["result"]["checks"] == (new.get("result") or {}).get("checks")
            rows.append({"step": old["id"], "output": "(qc checks)", "identical": same})
    return {"identical": all(r["identical"] for r in rows), "items": rows}


def load_record(run_dir: str) -> dict[str, Any]:
    record = json.loads((Path(run_dir) / "record.json").read_text())
    record["_run_dir"] = str(Path(run_dir).resolve())
    return record


def summarize(record: dict[str, Any]) -> None:
    print(f"{record['run_id']} [{record['mode']}] {record['status']}")
    for s in record["steps"]:
        outs = ", ".join(f"{o['file']} {o['sha256'][:12]} ({o.get('rows', '?')} rows)" for o in (s.get("outputs") or {}).values())
        checks = [f"{c['id']}={c['status']}" for c in (s.get("result") or {}).get("checks", [])]
        print(f"  {s['id']:<14} {s['type']:<10} {s.get('status', ''):<9} {s.get('elapsed_s', '')!s:>5}s  {outs} {' '.join(checks)}")
    print(f"  delivery: {record['delivery']}")
    if "comparison" in record:
        c = record["comparison"]
        print(f"  compared with {record['of_run']}: {'IDENTICAL' if c['identical'] else 'DIFFERENT'}")
        for item in c["items"]:
            print(f"    {item}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("workflow")
    run.add_argument("--source", required=True)
    run.add_argument("--param", action="append", default=[])
    run.add_argument("--seed", type=int)
    run.add_argument("--no-deliver", action="store_true")
    again = sub.add_parser("again")
    again.add_argument("run_dir")
    again.add_argument("--source", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("run_dir")
    replay.add_argument("--seed", type=int, help="override, to show the seed matters")
    cmp = sub.add_parser("compare")
    cmp.add_argument("a")
    cmp.add_argument("b")
    args = parser.parse_args()

    if args.command == "run":
        text = Path(args.workflow).read_text()
        workflow = yaml.safe_load(text)
        params = load_params(workflow, dict(p.split("=", 1) for p in args.param))
        seed = args.seed if args.seed is not None else secrets.randbelow(2**31)
        record = execute(text, args.workflow, params, seed, mode="run", source=Path(args.source),
                         original=None, image=DEFAULT_IMAGE, deliver=not args.no_deliver)  # fmt: skip
    elif args.command == "again":
        original = load_record(args.run_dir)
        text = (Path(args.run_dir) / "workflow.yaml").read_text()
        record = execute(text, original["workflow"]["path"], original["params"], original["seed"],
                         mode="run_again", source=Path(args.source), original=original,
                         image=original["image"]["id"], deliver=True)  # fmt: skip
    elif args.command == "replay":
        original = load_record(args.run_dir)
        text = (Path(args.run_dir) / "workflow.yaml").read_text()
        if hashlib.sha256(text.encode()).hexdigest() != original["workflow"]["sha256"]:
            raise SystemExit("The kept workflow file doesn't match the record.")
        seed = args.seed if args.seed is not None else original["seed"]
        record = execute(text, original["workflow"]["path"], original["params"], seed,
                         mode="replay", source=None, original=original,
                         image=original["image"]["id"], deliver=False)  # fmt: skip
    else:
        a, b = load_record(args.a), load_record(args.b)
        print(json.dumps(compare(a, b), indent=2))
        return
    summarize(record)


if __name__ == "__main__":
    main()
