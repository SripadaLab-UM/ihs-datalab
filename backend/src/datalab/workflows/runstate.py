"""A run in progress, as the runner's parts share it: its plan, the outputs
its steps have made, and a step failing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datalab.workflows.model import PipelineLookup, Scalar, Workflow
from datalab.workflows.sandbox import ImageFacts
from datalab.workflows.source import WorkflowFile, WorkflowFolder


@dataclass
class Output:
    step: str
    name: str
    file: str
    folder: Path
    facts: dict[str, Any]

    @property
    def path(self) -> Path:
        return self.folder / self.file


@dataclass
class Plan:
    run_id: str
    mode: str
    workflow: Workflow
    file: WorkflowFile
    params: dict[str, Scalar]
    seed: int
    image: ImageFacts | None
    host_platform: str
    pipelines: PipelineLookup
    deliver: bool = True
    # What the record says when `deliver` is off.
    no_delivery: str = "Replays don't deliver unless asked to."
    original: dict[str, Any] | None = None
    set_id: str | None = None
    replay_exact: bool | None = None
    replay_notes: list[str] = field(default_factory=list)
    # The run's own copy of the workflow files and the package (a Replay has
    # the original's kept copies instead).
    folder: WorkflowFolder | None = None
    run_dir: Path = Path()
    budget: int = 0
    libraries: dict[str, dict[str, Any]] = field(default_factory=dict)


class StepFailed(Exception):
    def __init__(self, message: str, fields: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
