"""Replay's side of a run: whether a run can be replayed exactly (and what
can't be pinned), where its kept inputs and pipelines are, and whether the
replay reproduced it."""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datalab import __version__
from datalab.config import Settings
from datalab.workflows.model import Pipeline, PipelineLookup, WorkflowInvalid, load_pipeline_file
from datalab.workflows.sandbox import ImageFacts, Sandbox, SandboxError
from datalab.workflows.source import git_blob_id
from datalab.workflows.stepfiles import matches, wrapper_sha256


@dataclass(frozen=True)
class ReplayCheck:
    exact: bool
    reasons: list[str]  # why it wouldn't be exact
    blocking: list[str]  # why it can't run at all


async def replay_check(
    original: dict[str, Any],
    *,
    settings: Settings,
    sandbox: Sandbox,
    image_or_none: Callable[[str], Awaitable[ImageFacts | None]],
) -> ReplayCheck:
    """Whether `original` can be replayed with everything pinned: what can't
    be pinned (`reasons`), and what stops it running at all (`blocking`)."""
    reasons: list[str] = []
    blocking: list[str] = []
    if original["status"] != "succeeded":
        blocking.append("Only a run that finished can be replayed.")
    if not original["inputs_kept"]:
        blocking.append("This run's extracted inputs have been removed.")
    for step in original["steps"]:
        if step["kind"] == "sql" and step["status"] == "succeeded":
            kept = step["outputs"]["final"]
            if not matches(kept_path(settings, original, step["step_id"], kept), kept["sha256"]):
                blocking.append(f"The kept extract for {step['step_id']} is missing or changed.")
        if step["kind"] == "pipeline" and step["status"] == "succeeded":
            for obj, kept in step["inputs"].items():
                if kept.get("folder") == "extracts" and not matches(
                    kept_path(settings, original, step["step_id"], kept), kept["sha256"]
                ):
                    blocking.append(f"The kept extract {obj} for {step['step_id']} is missing.")
    text_blob = original["workflow_text"].encode()
    if original["workflow_source"] == "file":
        kept = {"sha256:" + hashlib.sha256(text_blob).hexdigest()}
    else:
        kept = {git_blob_id(text_blob), git_blob_id(text_blob.replace(b"\r\n", b"\n"))}
    if original["workflow_blob"] not in kept:
        blocking.append("The kept workflow file doesn't match its recorded blob id.")
    uses_containers = any(s["kind"] in ("r", "pipeline", "qc_custom") for s in original["steps"])
    if uses_containers:
        image = await image_or_none(original["image_digest"])
        if image is None:
            reasons.append(
                f"The agent image this run used ({original['image_digest'][:19]}…) isn't on "
                "this computer, so the current image would be used."
            )
        try:
            host = await sandbox.host_platform()
        except SandboxError:
            blocking.append("Docker isn't running.")
            host = original["host_platform"]
        if host != original["host_platform"]:
            reasons.append(
                f"This computer runs Docker on {host}; the run was on "
                f"{original['host_platform']}. Results can differ in the last digits."
            )
        if not original["runner_version"].endswith(f"sha256:{wrapper_sha256()}"):
            reasons.append("DataLab's step wrapper has changed since this run.")
        for pipeline in original["pipelines"]:
            library = settings.data_dir / pipeline.get("library", "-") / "library.json"
            with contextlib.suppress(OSError, ValueError):
                if json.loads(library.read_text(encoding="utf-8"))[
                    "library_sha256"
                ] == pipeline.get("library_sha256"):
                    continue
            reasons.append(
                f"The {pipeline['name']} pipeline's library would be rebuilt from the kept "
                "source, so its build may differ."
            )
    if not original["runner_version"].startswith(f"datalab {__version__};"):
        reasons.append("DataLab's version has changed since this run; its checks may differ.")
    return ReplayCheck(exact=not reasons and not blocking, reasons=reasons, blocking=blocking)


def compare(original: dict[str, Any], steps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Whether every output and every check of a replay (its `steps`) matched
    the original's, and what didn't."""
    mine = {s["step_id"]: s for s in steps}
    notes = []
    for old in original["steps"]:
        new = mine.get(old["step_id"], {})
        for name, out in (old.get("outputs") or {}).items():
            theirs = (new.get("outputs") or {}).get(name) or {}
            if theirs.get("sha256") != out.get("sha256"):
                notes.append(f"Step {old['step_id']}'s {out['file']} differs from the original.")
        old_checks = (old.get("result") or {}).get("checks")
        if old_checks is not None and old_checks != (new.get("result") or {}).get("checks"):
            notes.append(f"Step {old['step_id']}'s checks differ from the original.")
        if old["status"] != new.get("status"):
            notes.append(f"Step {old['step_id']} {new.get('status')}; it {old['status']} before.")
    return not notes, notes


def kept_path(
    settings: Settings, original: dict[str, Any], step_id: str, kept: dict[str, Any]
) -> Path:
    """Where the original run keeps one of its steps' files."""
    folder = kept.get("folder", "outputs")
    return settings.data_dir / original["run_dir"] / "steps" / step_id / folder / kept["file"]


def kept_pipelines(settings: Settings, original: dict[str, Any]) -> PipelineLookup:
    """The pipelines as the original run kept them, for its Replay."""
    root = settings.data_dir / original["run_dir"] / "pipelines"

    def lookup(name: str) -> Pipeline | None:
        folder = root / name
        try:
            spec = load_pipeline_file((folder / "pipeline.yaml").read_text(encoding="utf-8"))
            return Pipeline(name, spec, (folder / "run.R").read_text(encoding="utf-8"))
        except (OSError, WorkflowInvalid):
            return None

    return lookup
