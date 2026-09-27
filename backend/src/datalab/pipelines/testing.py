"""Running the package's tests: testthat, in a no-network container.

The tests run on a git tree (a proposal's, or a rebased commit before it's
pushed), never on the conversation's live folder: DataLab writes the tree's
files into a fresh folder of its own, and the container gets that folder
read-only, as the workflow runner gives a pipeline's package (runner.py,
`_build`). The container is the workflow sandbox's: the agent image, no
network, a read-only root, no capabilities, a non-root user, time and
memory limits (workflows/sandbox.py).

Inside, the package is copied to /run/work (its tests may write snapshots,
and compiled code must be able to run: unlike /tmp, which is noexec in every
step container, /run/work is a folder of this run's own, for tests only)
and `testthat::test_local()` runs it. A small R wrapper writes each test's
counts to /run/out/results.csv with base R only, so the image needs nothing
beyond testthat. The log (up to 1 MB) is kept in
`<data folder>/pipeline-tests/<id>.log`, for the person to read in the
Pipelines tab; nothing from it leaves this computer.

**A quality check, not a safety gate.** results.csv is written by the same R
process that runs the code under test, so that code could write whatever
counts it likes. The tests tell the person the change works as its tests
say; the sandbox (no network, nothing of DataLab's mounted) and the person's
review of the diff are what keep it safe.

Recorded against the tree, so a result holds for exactly those files: Save &
share pushes only a commit whose tree passed. At most `MAX_AT_ONCE` run at
a time; the rest wait their turn (still "running").
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from datalab.pipelines.proposals import PipelineStore, TestRun
from datalab.repos.git import Clone, GitError
from datalab.workflows.sandbox import Bind, ContainerStep, Sandbox, SandboxError
from datalab.workflows.source import PACKAGE

log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 200 * 1024**2
MAX_LISTED = 200  # files and failures kept in a summary
LOG_TAIL_BYTES = 64 * 1024
MAX_AT_ONCE = 2
_R = (
    'r <- testthat::test_local(".", stop_on_failure = FALSE, stop_on_warning = FALSE); '
    "d <- as.data.frame(r); "
    'cols <- c("file", "test", "nb", "failed", "skipped", "error", "warning"); '
    "cols <- intersect(cols, names(d)); "
    'utils::write.csv(d[, cols, drop = FALSE], "/run/out/results.csv", row.names = FALSE)'
)
COMMAND = (
    "sh", "-c",
    f"mkdir -p /run/work/tmp && cp -r /run/src/{PACKAGE} /run/work/pkg && cd /run/work/pkg "
    f"&& Rscript --vanilla -e '{_R}'",
)  # fmt: skip
# R's own temporary files (and anything compiled there) go to the exec-able folder.
ENV = {"TMPDIR": "/run/work/tmp"}


class PackageTests:
    def __init__(self, *, sandbox: Sandbox, image: str, store: PipelineStore, folder: Path) -> None:
        self.sandbox = sandbox
        self.image = image
        self.store = store
        self.folder = folder  # <data folder>/pipeline-tests
        self._slots = asyncio.Semaphore(MAX_AT_ONCE)

    def log_path(self, test_id: str) -> Path:
        return self.folder / f"{test_id}.log"

    def log_tail(self, test_id: str) -> tuple[str, bool]:
        """The end of a run's log, and whether it was cut."""
        path = self.log_path(test_id)
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - LOG_TAIL_BYTES))
                return handle.read().decode("utf-8", "replace"), size > LOG_TAIL_BYTES
        except OSError:
            return "", False

    def begin(self, commit: str, tree: str, *, proposal_id: str | None) -> TestRun:
        """Record a run of the tests on `commit` (whose tree is `tree`), not started yet."""
        return self.store.add_test(tree, proposal_id=proposal_id, commit=commit)

    async def execute(self, run: TestRun, clone: Clone) -> TestRun:
        """Run the tests `begin` recorded; the finished run."""
        assert run.commit is not None
        commit = run.commit
        place = self.folder / run.id
        try:
            async with self._slots:
                return await self._run(run, clone, commit, place)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.store.finish_test, run, "error", message="The test run was stopped."
            )
            raise
        except Exception as error:
            log.exception("the package's tests couldn't run (%s)", run.id)
            return await asyncio.to_thread(
                self.store.finish_test,
                run,
                "error",
                message=f"The tests couldn't run ({type(error).__name__}).",
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, place, True)

    async def _run(self, run: TestRun, clone: Clone, commit: str, place: Path) -> TestRun:
        facts = await self.sandbox.image(self.image)
        if facts is None:
            return await asyncio.to_thread(
                self.store.finish_test,
                run,
                "error",
                message=f"The agent image ({self.image}) isn't on this computer.",
            )
        # The package, read-only; and what the run may write: its results, and
        # its working copy of the package.
        source, writable = place / "src", place / "rw"
        out, work = writable / "out", writable / "work"
        await asyncio.to_thread(_write_tree, clone, commit, source)
        out.mkdir(parents=True)
        work.mkdir()
        if not (source / PACKAGE / "DESCRIPTION").is_file():
            return await asyncio.to_thread(
                self.store.finish_test,
                run,
                "error",
                message=f"There's no {PACKAGE} package (with a DESCRIPTION) in the repo.",
                image_digest=facts.digest,
            )
        try:
            outcome = await self.sandbox.run(
                ContainerStep(
                    run_id=run.id,
                    name="tests",
                    image=facts.digest,
                    binds=[
                        Bind(source, "/run/src"),
                        Bind(out, "/run/out", readonly=False),
                        Bind(work, "/run/work", readonly=False),
                    ],
                    watch=writable,
                    max_bytes=MAX_OUTPUT_BYTES,
                    command=COMMAND,
                    env=dict(ENV),
                )
            )
        finally:
            await self.sandbox.remove_run(run.id)
        self.folder.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.log_path(run.id).write_bytes, outcome.log)
        if outcome.timed_out or outcome.over_cap:
            why = "took too long" if outcome.timed_out else "wrote too much"
            return await asyncio.to_thread(
                self.store.finish_test, run, "error", message=f"The tests {why} and were stopped.",
                image_digest=facts.digest,
            )  # fmt: skip
        summary = read_results(out / "results.csv")
        if summary is None:
            return await asyncio.to_thread(
                self.store.finish_test,
                run,
                "error",
                message=f"The tests didn't run to the end (exit {outcome.exit_code}). See the log.",
                image_digest=facts.digest,
            )
        bad = summary["failed"] + summary["errors"]
        if outcome.exit_code != 0 or bad:
            message = (
                f"{bad} of {summary['tests']} tests failed."
                if bad
                else f"The tests ended with exit {outcome.exit_code}. See the log."
            )
            return await asyncio.to_thread(
                self.store.finish_test, run, "failed", summary=summary, message=message,
                image_digest=facts.digest,
            )  # fmt: skip
        if summary["tests"] == 0:
            return await asyncio.to_thread(
                self.store.finish_test, run, "failed", summary=summary,
                message="The package has no tests that ran.", image_digest=facts.digest,
            )  # fmt: skip
        return await asyncio.to_thread(
            self.store.finish_test, run, "passed", summary=summary,
            message=f"All {summary['tests']} tests passed.", image_digest=facts.digest,
        )  # fmt: skip


def _write_tree(clone: Clone, commit: str, folder: Path) -> None:
    folder.mkdir(parents=True)
    with clone.lock:
        try:
            clone.copy_tree(commit, folder, skip=lambda path: path.startswith(".github/"))
        except GitError as error:
            raise SandboxError(str(error)) from None


def read_results(path: Path) -> dict[str, Any] | None:
    """The counts from results.csv: one row per test (as testthat's data frame has them)."""
    # Written by the container: never a link (it would point at the host's
    # files) or anything but a plain file (a pipe would never end).
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        text = handle.read(8 * 1024**2).decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    per_file: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    totals = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "warnings": 0}
    for row in rows:
        name = (row.get("file") or "").strip()[:200]
        test = (row.get("test") or "").strip()[:300]
        failed = _count(row.get("failed")) > 0
        error = _true(row.get("error"))
        skipped = _true(row.get("skipped"))
        warnings = _count(row.get("warning"))
        entry = per_file.setdefault(
            name, {"file": name, "tests": 0, "failed": 0, "skipped": 0, "errors": 0}
        )
        entry["tests"] += 1
        totals["tests"] += 1
        totals["warnings"] += warnings
        if error:
            entry["errors"] += 1
            totals["errors"] += 1
        elif failed:
            entry["failed"] += 1
            totals["failed"] += 1
        elif skipped:
            entry["skipped"] += 1
            totals["skipped"] += 1
        else:
            totals["passed"] += 1
        if (error or failed) and len(failures) < MAX_LISTED:
            failures.append({"file": name, "test": test, "kind": "error" if error else "failure"})
    files = sorted(per_file.values(), key=lambda f: f["file"])
    return {
        **totals,
        "files": files[:MAX_LISTED],
        "more_files": max(0, len(files) - MAX_LISTED),
        "failures": failures,
    }


def _count(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _true(value: str | None) -> bool:
    return (value or "").strip().upper() in ("TRUE", "T", "1")
