"""The release parity check: the prototype and v1 on the same synthetic inputs.

    cd backend && uv run python ../scripts/parity/parity.py all \\
        --work <scratch folder> --prototype <prototype checkout> \\
        --pipelines <ihs-pipelines checkout> [--commit <sha>]

See README.md for what each step does, what it covers, and what it doesn't.
Synthetic data only: v1 runs in the practice profile, which refuses any
database without the synthetic marker, and the prototype is pointed at the
same local database. Nothing printed or written to the report holds a row
or a cell; the run folders under --work do, and stay there.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from compare import Comparison, compare_file, compare_json_qc
from report import write_report

ROOT = Path(__file__).resolve().parents[2]
V1_PORT = 8802
BROKER_PORT = 8812
FORBIDDEN_PORTS = {8765, 8766, 8767, 8780}
ROUTINES = [
    "fitbit_daily_2025",
    "garmin_sleep_summary_2025",
    "fitbit_sleep_logs_2025",
    "mood_daily_2025",
    "garmin_daily_summary_2025",
    "healthkit_daily_steps_2025",
    "healthkit_sleep_intervals_2025",
    "smoking_survey_responses_dropbox_testing",
]
PIPELINE = "daily_metrics_2025"
# v1 also has one workflow per daily metric. The prototype has no routine for
# these; its daily_metrics_2025 run writes the same files, so each is
# compared with that run's file of the same name.
FEATURES = ["steps_day", "daily_rhr_2025", "active_minutes_2025", "sleep_2025", "daily_hrv_2025"]
PROTOTYPE_IMAGE = "lab-ai-routine-r:local"


# Setup ---------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def snapshot_pipelines(repo: Path, commit: str, work: Path) -> Path:
    """The pipelines repo at one commit, as files: what v1 reads its workflows from."""
    sha = _git(repo, "rev-parse", commit)
    target = work / f"pipelines-{sha[:12]}"
    if not target.exists():
        target.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "-C", str(repo), "archive", sha], check=True, capture_output=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)
    return target


def clone_prototype(prototype: Path, work: Path) -> Path:
    """A `--no-local` clone of the prototype at its HEAD, with its own venv."""
    clone = work / "proto"
    if not clone.exists():
        subprocess.run(["git", "clone", "--no-local", "-q", str(prototype), str(clone)], check=True)
    if not (clone / ".venv").exists():
        subprocess.run(["uv", "venv", "-q", "--python", "3.13", str(clone / ".venv")], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(clone / ".venv/bin/python"),
             "-e", f"{clone}[oracle]"],
            check=True,
        )  # fmt: skip
    return clone


def practice_password() -> str:
    """The synthetic database's read-only password, from where v1 keeps it. Never printed."""
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from datalab.config import PRACTICE_ORACLE
    from datalab.credentials import oracle_password

    return oracle_password(PRACTICE_ORACLE)


# v1 ------------------------------------------------------------------------


class V1Server:
    """A private practice DataLab on port 8802, in its own data folder."""

    def __init__(self, work: Path, workflows: Path, catalog: Path) -> None:
        # A new folder every time: Docker Desktop often can't mount a folder
        # that was deleted and made again at the same path (the runner spike, §1).
        self.data_dir = work / f"v1-data-{datetime.now(UTC):%Y%m%dT%H%M%S}"
        self.data_dir.mkdir(parents=True)
        shutil.copytree(catalog, self.data_dir / "catalog")
        (self.data_dir / "settings.toml").write_text(
            f"port = {V1_PORT}\n"
            f"catalog_dir = {json.dumps(str(self.data_dir / 'catalog'))}\n"
            f"\n[workflows]\nfolder = {json.dumps(str(workflows))}\n"
        )
        sys.path.insert(0, str(ROOT / "backend" / "src"))
        from datalab.sessions.containers import instance_of

        self.instance = instance_of(self.data_dir)
        self.log_path = work / "v1-server.log"
        self.process: subprocess.Popen[bytes] | None = None
        self.client: httpx.Client | None = None

    def __enter__(self) -> V1Server:
        if _listening(V1_PORT):
            raise SystemExit(f"Port {V1_PORT} is in use; stop whatever holds it first.")
        env = {
            **_clean_environ(),
            "DATALAB_PROFILE": "practice",
            "DATALAB_DATA_DIR": str(self.data_dir),
            "DATALAB_ORACLE_PASSWORD": practice_password(),
        }
        log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            ["uv", "run", "datalab", "--profile", "practice", "serve", "--no-browser"],
            cwd=ROOT / "backend", env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )  # fmt: skip
        self.client = self._sign_in()
        return self

    def _sign_in(self) -> httpx.Client:
        pattern = re.compile(rf"http://127\.0\.0\.1:{V1_PORT}(/sign-in\?token=[\w-]+)")
        deadline = time.time() + 180
        while time.time() < deadline:
            match = pattern.search(self.log_path.read_text(errors="replace"))
            if match:
                client = httpx.Client(base_url=f"http://127.0.0.1:{V1_PORT}", timeout=60)
                for _ in range(120):
                    with contextlib.suppress(httpx.TransportError):
                        client.get(match.group(1))
                        break
                    time.sleep(0.5)
                client.get("/api/workflows/status").raise_for_status()
                health = client.get("/api/health").json()
                if health.get("profile") != "practice":
                    raise SystemExit("The v1 server isn't the practice profile.")
                return client
            assert self.process is not None
            if self.process.poll() is not None:
                break
            time.sleep(0.5)
        raise SystemExit(f"The v1 server didn't start; see {self.log_path}")

    def __exit__(self, *_exc: object) -> None:
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=90)
            except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.process.pid, signal.SIGKILL)
        left = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=datalab.instance={self.instance}"],
            capture_output=True, text=True,
        ).stdout.split()  # fmt: skip
        if left:
            subprocess.run(["docker", "rm", "-f", *left], capture_output=True)

    def run(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.client is not None
        started = self.client.post(
            "/api/workflows/runs", json={"path": path, "params": params or {}}
        )
        if started.status_code != 201:
            return {"status": "refused", "error": started.text[:500]}
        run_id = started.json()["id"]
        deadline = time.time() + 3600
        while time.time() < deadline:
            run = self.client.get(f"/api/workflows/runs/{run_id}").json()
            # A run is marked finished just before its delivery is.
            delivering = run.get("delivery_status") in {"pending", "delivering"}
            if run["status"] in {"succeeded", "failed", "cancelled", "interrupted"} and not (
                run["status"] == "succeeded" and delivering
            ):
                return run
            time.sleep(1)
        raise SystemExit(f"The v1 run {run_id} didn't finish in an hour.")

    def workflow_paths(self) -> dict[str, str]:
        assert self.client is not None
        listed = self.client.get("/api/workflows").json()
        return {w["name"]: w["path"] for w in listed if w.get("name")}

    def run_dir(self, run_id: str) -> Path:
        # The record is written just after the run is marked finished.
        folder = self.data_dir / "runs" / run_id
        for _ in range(120):
            if (folder / "record.json").is_file():
                return folder
            time.sleep(0.5)
        raise SystemExit(f"No run record for {run_id}.")


def collect_v1(server: V1Server, name: str, run: dict[str, Any], out: Path) -> None:
    """The run's files, laid out as proto_driver.py lays out the prototype's."""
    target = out / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    status: dict[str, Any] = {"name": name, "side": "v1", "status": run["status"]}
    if run["status"] == "refused":
        status["error"] = run.get("error")
        (target / "status.json").write_text(json.dumps(status, indent=2))
        return
    status["run_id"] = run["id"]
    status["delivery_status"] = run.get("delivery_status")
    run_dir = server.run_dir(run["id"])
    qc: list[dict[str, Any]] = []
    status["steps"] = []
    for step in run.get("steps", []):
        kind = step["kind"]
        entry = {"id": step["step_id"], "type": kind, "state": step["status"]}
        if step.get("message"):
            entry["message"] = str(step["message"])[:300]
        status["steps"].append(entry)
        step_dir = run_dir / "steps" / step["step_id"]
        if kind == "sql":
            _copy_outputs(step, step_dir / "outputs", target / "extract")
        elif kind in {"r", "pipeline"}:
            _copy_outputs(step, step_dir / "outputs", target / "outputs")
            if kind == "pipeline":
                (target / "extract").mkdir(exist_ok=True)
                for extract in (step_dir / "extracts").glob("*.csv"):
                    shutil.copyfile(extract, target / "extract" / extract.name)
        elif kind.startswith("qc"):
            checks = (step.get("result") or {}).get("checks") or []
            rows = next((c["observed"] for c in checks if c.get("id") == "min_rows"), None)
            qc.append(
                {
                    "step": step["step_id"],
                    "passed": step["status"] == "succeeded",
                    "row_count": rows,
                }
            )
    for delivery in run.get("deliveries") or []:
        folder = Path(delivery["folder"])
        for file in delivery["files"]:
            source = folder / file["path"]
            # The prototype's layout: <subfolder>/<file>, without v1's dated
            # folder and files/ prefix, which the report lists separately.
            dest = target / "delivered" / folder.parent.name / Path(file["path"]).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        status.setdefault("delivery_folders", []).append(folder.name.split(" ", 2)[-1])
    (target / "qc.json").write_text(json.dumps(qc, indent=2))
    (target / "status.json").write_text(json.dumps(status, indent=2))


def _copy_outputs(step: dict[str, Any], source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for output in (step.get("outputs") or {}).values():
        path = source / output["file"]
        if path.is_file():
            shutil.copyfile(path, dest / output["file"])


def run_v1(args: argparse.Namespace, workflows: Path, names: list[str]) -> None:
    out = args.work / "out" / "v1"
    with V1Server(args.work, workflows, args.catalog) as server:
        paths = server.workflow_paths()
        for name in names:
            path = paths.get(name)
            if path is None:
                target = out / name
                target.mkdir(parents=True, exist_ok=True)
                (target / "status.json").write_text(
                    json.dumps({"name": name, "side": "v1", "status": "missing"}, indent=2)
                )
                print(f"v1 {name}: no workflow file", flush=True)
                continue
            run = server.run(path)
            collect_v1(server, name, run, out)
            print(f"v1 {name}: {run['status']}", flush=True)


# Prototype -----------------------------------------------------------------


def run_prototype(args: argparse.Namespace, clone: Path, mode: str, names: list[str]) -> None:
    """Run proto_driver.py with the prototype's Python; live or on v1's extracts."""
    out = args.work / "out" / f"proto-{mode}"
    config: dict[str, Any] = {
        "clone": str(clone),
        # A new folder every time, for the same Docker Desktop reason as v1's.
        "state": str(args.work / f"proto-state-{mode}-{datetime.now(UTC):%Y%m%dT%H%M%S}"),
        "out": str(out),
        "mode": mode,
        "broker_port": BROKER_PORT,
        "routines": [n for n in names if n in ROUTINES],
        "pipelines": [n for n in names if n == PIPELINE],
    }
    if mode == "extracts":
        v1 = args.work / "out" / "v1"
        config["extracts"] = {
            n: str(f) for n in names if (f := _only_file(v1 / n / "extract")) is not None
        }
        config["pipeline_extracts"] = {
            PIPELINE: {
                p.stem.split(".", 1)[1]: str(p)
                for p in sorted((v1 / PIPELINE / "extract").glob("IHS_2025.*.csv"))
            }
        }
    env = _clean_environ()
    if mode == "live":
        if _listening(BROKER_PORT):
            raise SystemExit(f"Port {BROKER_PORT} is in use; stop whatever holds it first.")
        env["LAB_AI_ORACLE_PASSWORD"] = practice_password()
    config_path = args.work / f"proto-{mode}.json"
    config_path.write_text(json.dumps(config, indent=2))
    before = _container_names()
    try:
        subprocess.run(
            [str(clone / ".venv/bin/python"), str(Path(__file__).with_name("proto_driver.py")),
             str(config_path)],
            cwd=clone, env=env, check=False,
        )  # fmt: skip
    finally:
        # The prototype's containers are --rm and named routine_r_…; remove
        # any this run left, and only those.
        left = [n for n in _container_names() - before if n.startswith("routine_r_")]
        if left:
            subprocess.run(["docker", "rm", "-f", *left], capture_output=True)


def _only_file(folder: Path) -> Path | None:
    files = sorted(folder.glob("*.csv")) if folder.is_dir() else []
    return files[0] if len(files) == 1 else None


def _container_names() -> set[str]:
    listed = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True
    )
    return set(listed.stdout.split())


def _clean_environ() -> dict[str, str]:
    """This shell's environment without DATALAB_* and LAB_AI_* settings.

    Each side gets only the settings the harness sets, so a variable left in
    the shell (another data folder, a real database, a token) can't change
    which database or folder a run uses.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith(("DATALAB_", "LAB_AI_"))}


def _listening(port: int) -> bool:
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"Port {port} belongs to another DataLab; the harness never uses it.")
    found = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    return bool(found.stdout.strip())


# Comparing -----------------------------------------------------------------


def compare_side(proto: Path, v1: Path, names: list[str]) -> list[dict[str, Any]]:
    results = []
    for name in names:
        if name in FEATURES:
            results.append(compare_feature(name, proto / PIPELINE, v1 / name))
        else:
            results.append(compare_one(name, proto / name, v1 / name))
    return results


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.is_file() else None


def compare_feature(name: str, proto: Path, v1: Path) -> dict[str, Any]:
    """One daily-metric workflow of v1 against the prototype's daily_metrics_2025 file."""
    entry: dict[str, Any] = {
        "name": name,
        "prototype_status": (_load(proto / "status.json") or {"status": "not run"}),
        "v1_status": (_load(v1 / "status.json") or {"status": "not run"}),
        "compared_with": f"prototype {PIPELINE}",
    }
    files = sorted(p.name for p in (v1 / "outputs").glob("*"))
    entry["files"] = [
        compare_file(
            f"outputs/{file}", _exists(proto / "outputs" / file), v1 / "outputs" / file
        ).to_dict()
        for file in files
    ]
    return entry


def compare_one(name: str, proto: Path, v1: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "prototype_status": (_load(proto / "status.json") or {"status": "not run"}),
        "v1_status": (_load(v1 / "status.json") or {"status": "not run"}),
        "files": [],
    }
    comparisons: list[Comparison] = []
    # Extracts: a routine's one SQL output, or a pipeline's per-object files.
    p_ex, v_ex = sorted((proto / "extract").glob("*.csv")), sorted((v1 / "extract").glob("*.csv"))
    if name == PIPELINE:
        names = sorted({p.name for p in p_ex} | {p.name for p in v_ex})
        for file in names:
            comparisons.append(
                compare_file(
                    f"extract/{file}",
                    _exists(proto / "extract" / file),
                    _exists(v1 / "extract" / file),
                    order_defined=False,
                )
            )
    elif p_ex or v_ex:
        label = f"extract/{(p_ex or v_ex)[0].name}"
        comparisons.append(
            compare_file(
                label, p_ex[0] if p_ex else None, v_ex[0] if v_ex else None, order_defined=False
            )
        )
    # Outputs, by file name. The routines' R steps keep the extract's order
    # (which the SQL doesn't fix); the pipeline's R code sorts its results.
    outputs = sorted(
        {p.name for p in (proto / "outputs").glob("*")}
        | {p.name for p in (v1 / "outputs").glob("*")}
    )
    for file in outputs:
        comparisons.append(
            compare_file(
                f"outputs/{file}",
                _exists(proto / "outputs" / file),
                _exists(v1 / "outputs" / file),
                order_defined=name == PIPELINE,
            )
        )
    # QC outcomes.
    p_qc, v_qc = _load(proto / "qc.json"), _load(v1 / "qc.json")
    if name == PIPELINE and not p_qc and v_qc:
        comparisons.append(
            Comparison(
                "qc",
                "v1_only",
                [
                    "The prototype runs this pipeline with no QC steps of its own; the "
                    "assert_*_qc checks inside the R code run on both sides and stop a run "
                    f"that fails them. v1's workflow adds {len(v_qc)} checks, "
                    f"{sum(q['passed'] for q in v_qc)} passed."
                ],
            )
        )
    elif p_qc is not None or v_qc is not None:
        strip = [{k: q[k] for k in ("passed", "row_count")} for q in (p_qc or [])]
        vstrip = [{k: q[k] for k in ("passed", "row_count")} for q in (v_qc or [])]
        comparisons.append(compare_json_qc("qc", {"checks": strip}, {"checks": vstrip}))
    # Delivered files: same subfolder and file name, same bytes.
    p_dl = {
        str(p.relative_to(proto / "delivered"))
        for p in (proto / "delivered").rglob("*")
        if p.is_file()
    }
    v_dl = {
        str(p.relative_to(v1 / "delivered")) for p in (v1 / "delivered").rglob("*") if p.is_file()
    }
    if name == PIPELINE and v_dl and not p_dl:
        comparisons.append(
            Comparison(
                "delivered",
                "v1_only",
                [
                    f"v1 delivered {len(v_dl)} files; the prototype's package run keeps its "
                    "outputs and doesn't deliver them."
                ],
            )
        )
        v_dl = set()
    for file in sorted(p_dl | v_dl):
        comparisons.append(
            compare_file(
                f"delivered/{file}",
                _exists(proto / "delivered" / file),
                _exists(v1 / "delivered" / file),
                order_defined=False,
            )
        )
    entry["files"] = [c.to_dict() for c in comparisons]
    return entry


def _exists(path: Path) -> Path | None:
    return path if path.is_file() else None


# Main ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("step", choices=["v1", "prototype", "compare", "all"])
    parser.add_argument("--work", type=Path, required=True, help="scratch folder (synthetic only)")
    parser.add_argument(
        "--prototype", type=Path, required=True, help="prototype checkout (read only)"
    )
    parser.add_argument("--pipelines", type=Path, required=True, help="ihs-pipelines checkout")
    parser.add_argument("--commit", default="HEAD", help="pipelines commit to test")
    parser.add_argument("--catalog", type=Path, help="catalog folder for v1")
    parser.add_argument("--only", nargs="*", help="workflow names (default: all fourteen)")
    parser.add_argument("--report", type=Path, help="write the Markdown report here too")
    args = parser.parse_args()
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    names = args.only or [*ROUTINES, PIPELINE, *FEATURES]

    workflows = snapshot_pipelines(args.pipelines, args.commit, args.work)
    clone = clone_prototype(args.prototype, args.work)
    if args.step in {"v1", "all"}:
        if args.catalog is None:
            parser.error("--catalog is needed to run v1")
        run_v1(args, workflows, names)
    if args.step in {"prototype", "all"}:
        run_prototype(args, clone, "live", names)
        run_prototype(args, clone, "extracts", names)
    if args.step in {"compare", "all"}:
        out = args.work / "out"
        meta = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "datalab_commit": _git(ROOT, "rev-parse", "HEAD"),
            "datalab_dirty": bool(_git(ROOT, "status", "--porcelain", "--", "backend/src")),
            "prototype_commit": _git(clone, "rev-parse", "HEAD"),
            "pipelines_commit": _git(args.pipelines, "rev-parse", args.commit),
            "prototype_image": PROTOTYPE_IMAGE,
        }
        results = {
            "live": compare_side(out / "proto-live", out / "v1", names),
            "extracts": compare_side(out / "proto-extracts", out / "v1", names),
        }
        (args.work / "parity.json").write_text(json.dumps({"meta": meta, **results}, indent=2))
        text = write_report(meta, results)
        (args.work / "parity.md").write_text(text)
        if args.report:
            args.report.write_text(text)
        print(f"Report: {args.work / 'parity.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
