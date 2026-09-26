"""Conversation modes: a starting point that sets the agent's instructions.

The full mode instructions come in milestone 3, built from the prototype's
reviewed prompts (see docs/WORKSPACE.md). These are short placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass

from datalab.sessions.tokens import SessionKind


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    kind: SessionKind
    instructions: str


MODES = {
    mode.id: mode
    for mode in (
        Mode(
            "analysis",
            "Analysis",
            "data",
            "You are helping a researcher answer a scientific question with Intern Health "
            "Study data. Make the question, unit of analysis, and cohort explicit. Check data "
            "quality before modelling, report sample sizes, and don't overstate causal claims.",
        ),
        Mode(
            "extraction",
            "Data extraction",
            "data",
            "You are helping extract a clean, documented dataset from the IHS database. Find "
            "tables with the catalog, check columns before relying on them, keep exploratory "
            "queries separate from the final extraction, and always show the exact SQL you ran.",
        ),
        Mode(
            "engineering",
            "Data engineering",
            "data",
            "You are helping maintain the lab's R data pipelines. Follow the package's "
            "conventions, run its tests, and hand back a reviewable change.",
        ),
        Mode(
            "research",
            "Research",
            "research",
            "You are helping with literature, methods, and tools. You have internet access but "
            "no study data. Cite your sources.",
        ),
    )
}


def instructions(mode_id: str) -> str:
    return MODES[mode_id].instructions
