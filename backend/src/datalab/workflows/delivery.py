"""Delivering a run's files once every step passed, through `exports.export()`
only, into a destination folder checked as it's written to; and what the
delivery is called and says."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

from datalab import export_folders, exports
from datalab.config import Settings
from datalab.data.access_log import AccessLog
from datalab.exports import DestinationStore, ExportError, ExportSource
from datalab.sessions.titles import scrub_title
from datalab.workflows.model import WORKFLOW_NAME
from datalab.workflows.records import RunStore, now
from datalab.workflows.runstate import Output, Plan
from datalab.workflows.stepfiles import lookup_output, number_or_none

log = logging.getLogger(__name__)

# A cohort year: 2000 to 2029.
_YEAR = re.compile(r"20[012]\d")


def delivery_message(destination: export_folders.Target, files: int) -> str:
    """What a finished delivery says: saved on this computer, never "synced"."""
    count = "1 file" if files == 1 else f"{files} files"
    message = f"{export_folders.saved_to(destination.name)}: {count}."
    note = export_folders.sync_note(destination.sync_provider, files=files)
    return f"{message} {note}" if note else message


def delivery_title(name: str) -> str:
    """The workflow's name as words, for its dated delivery folder.

    The export scrubs the title of anything shaped like a study identifier,
    and read as one word, `fitbit_daily_2025` mixes letters and digits the
    way IDs do, so the whole name went and the folder was called "export".
    So the name is split into pieces, and each is kept only if it can't be
    part of an identifier:

    - a word of letters, if the scrub keeps it;
    - never a piece mixing letters and digits (`p0001`, `syn25`);
    - a piece of digits only if it is a cohort year (2000 to 2029) and the
      piece before it was kept, so `steps_0001` can't come out as
      "steps 0001", nor `syn_25_2001` or `syn25_2001` keep part of a code;
    - and if the scrub would still change the joined words (`participant
      2025`), none of it: the folder is called "export". So is a name the
      file check wouldn't accept.
    """
    if not WORKFLOW_NAME.fullmatch(name):
        return "export"
    kept: list[str] = []
    previous_kept = True
    for piece in re.split(r"[\W_]+", name):
        if not piece:
            continue
        if piece.isalpha():
            keep = scrub_title(piece) == piece
        elif piece.isdigit():
            keep = previous_kept and bool(_YEAR.fullmatch(piece))
        else:
            keep = False
        if keep:
            kept.append(piece)
        previous_kept = keep
    title = " ".join(kept)
    return title if title and scrub_title(title) == title else "export"


@dataclass
class Delivery:
    """What a run's delivery writes to: the destination folders, the run's
    record, and the audit log (the runner's own, WorkflowRunner)."""

    settings: Settings
    store: RunStore
    access_log: AccessLog
    destinations: DestinationStore
    started_by: str

    async def deliver(
        self, plan: Plan, done: dict[str, dict[str, Output]], failed: str | None
    ) -> None:
        spec = plan.workflow.deliver
        if spec is None:
            return
        if failed:
            self.store.update_run(
                plan.run_id,
                delivery_status="skipped",
                delivery_message="A step failed, so nothing was delivered.",
            )
            return
        if not plan.deliver:
            self.store.update_run(
                plan.run_id, delivery_status="skipped", delivery_message=plan.no_delivery
            )
            return
        try:
            destination = self.destination(spec.destination)
            folder = destination.path
            sources = []
            chosen = []
            for ref in spec.files:
                output = lookup_output(ref, done)
                chosen.append(output)
                sources.append(
                    ExportSource(
                        open=lambda p=output.path: os.open(
                            p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        ),
                        path=output.file,
                        container_path=f"{plan.run_id}/{output.step}/{output.file}",
                    )
                )
            about = self.manifest_about(plan, chosen)
            title = delivery_title(plan.workflow.name)
            subfolder = exports.safe_name(spec.folder)

            def write() -> exports.ExportResult:
                # Everything goes inside the very folder that was checked,
                # and its subfolder is never followed through a link.
                with export_folders.open_target(destination) as root:
                    with contextlib.suppress(FileExistsError):
                        root.mkdir(subfolder)
                    with root.child(subfolder) as target:
                        return exports.export(
                            target, title=title, tag=plan.run_id, sources=sources, about=about
                        )

            result = await asyncio.to_thread(write)
        except ExportError as error:
            self.store.update_run(
                plan.run_id, delivery_status="failed", delivery_message=str(error)
            )
            return
        except OSError as error:
            log.warning("Delivery for %s failed: %s", plan.run_id, error)
            self.store.update_run(
                plan.run_id,
                delivery_status="failed",
                delivery_message="DataLab couldn't write to the export folder.",
            )
            return
        written = result.entries
        self.store.add_delivery(
            {
                "id": f"dl_{secrets.token_hex(6)}",
                "run_id": plan.run_id,
                "destination_key": spec.destination,
                "destination_id": destination.id,
                "destination_path": str(folder),
                "destination_name": destination.name,
                "sync_provider": destination.sync_provider,
                "folder": str(result.folder),
                "files": [{k: f[k] for k in ("path", "bytes", "sha256")} for f in written],
                "manifest_sha256": result.manifest_sha256,
                "delivered_at": now(),
            }
        )
        self.access_log.record_export(
            session_id=plan.run_id,
            destination=spec.destination,
            files=len(result.files),
            contains_study_data=self.settings.profile == "real",
        )
        self.store.update_run(
            plan.run_id,
            delivery_status="delivered",
            delivery_message=delivery_message(destination, len(result.files)),
        )

    def destination(self, key: str) -> export_folders.Target:
        """The folder a destination key names on this computer, checked now.

        The practice profile delivers only to its own practice folder, so
        nothing from it can end up somewhere real.
        """
        return export_folders.target_for_key(self.settings, self.destinations, key)

    def manifest_about(self, plan: Plan, chosen: list[Output]) -> dict[str, Any]:
        run = self.store.get_run(plan.run_id) or {}
        steps = self.store.steps(plan.run_id)
        return {
            "profile": self.settings.profile,
            "contains_study_data": self.settings.profile == "real",
            "workflow": {
                "name": plan.workflow.name,
                "path": plan.file.path,
                "source": plan.file.source,
                "blob": plan.file.blob,
                "commit": plan.file.commit,
            },
            "run": {
                "id": plan.run_id,
                "mode": plan.mode,
                "of_run": run.get("of_run"),
                "params": plan.params,
                "seed": plan.seed,
                "image": run.get("image_digest"),
                "started_by": self.started_by,
            },
            "qc": [
                {
                    "step": s["step_id"],
                    "status": s["status"],
                    "checks": [
                        {
                            "id": c.get("id"),
                            "status": c.get("status"),
                            # Numbers only: a custom check's text could carry values.
                            "observed": number_or_none(c.get("observed")),
                        }
                        for c in ((s.get("result") or {}).get("checks") or [])
                    ],
                }
                for s in steps
                if s["kind"].startswith("qc")
            ],
            "outputs": [
                {"step": o.step, "file": o.file, "sha256": o.facts.get("sha256")} for o in chosen
            ],
            # Delivered CSVs no small_cells check covered, and why (from the workflow file).
            "without_small_cells": dict(
                plan.workflow.deliver.without_small_cells if plan.workflow.deliver else {}
            ),
        }
