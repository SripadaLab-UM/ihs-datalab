"""Modes: what each tells the agent, and what each lets it do."""

from __future__ import annotations

import tomllib

from datalab.sessions import codex_config, modes
from datalab.sessions.modes import CATALOG_TOOLS, MODES
from datalab.workflows.model import load_workflow


def config(mode_id: str) -> dict:
    mode = MODES[mode_id]
    text = codex_config.render(
        mode.kind, model="m", tool_timeout_seconds=1, tools_off=mode.tools_off
    )
    return tomllib.loads(text)


def test_every_mode_has_its_label_kind_and_starters():
    assert {m.id: (m.label, m.kind) for m in MODES.values()} == {
        "analysis": ("Analysis", "data"),
        "extraction": ("Data extraction", "data"),
        "engineering": ("Data engineering", "data"),
        "workflows": ("Workflow authoring", "data"),
        "knowledge": ("Knowledge writing", "data"),
        "research": ("Research", "research"),
    }
    assert all(m.description and m.starters for m in MODES.values())
    # The two new ones are docked by their tabs, not offered for a new conversation.
    assert {m.id for m in MODES.values() if m.tab_only} == {"workflows", "knowledge"}


def test_every_mode_gets_the_knowledge_base_note():
    for mode_id in MODES:
        assert modes.instructions(mode_id).endswith(modes.KNOWLEDGE)


def test_workflow_authoring_queries_like_data_extraction():
    workflows, extraction = MODES["workflows"], MODES["extraction"]
    assert (
        (workflows.kind, workflows.queries, workflows.tools_off)
        == (
            extraction.kind,
            extraction.queries,
            extraction.tools_off,
        )
        == ("data", True, ())
    )
    server = config("workflows")["mcp_servers"]["ihs-data"]
    assert "disabled_tools" not in server
    assert config("workflows")["web_search"] == "disabled"


def test_workflow_authorings_instructions():
    text = MODES["workflows"].instructions
    # Where it drafts, and who saves.
    assert "/work/pipelines/workflows/" in text
    assert "Pipelines tab" in text and "You never save or share anything yourself" in text
    assert "/work/outputs/<name>.yaml" in text  # when the repo isn't set up
    # WORKFLOWS.md's rules.
    assert "`reads:` lists every Oracle object" in text
    assert "small_cells" in text and "deliver.without_small_cells" in text
    assert "`min` at least 11" in text
    assert "destination key" in text and "never a\n  path" in text
    assert "every bind (`:start_date`)\n  must be a declared parameter" in text
    # It checks its own drafts, and says what DataLab checks again.
    assert "call `check_workflow`" in text
    assert "won't run a file that\n  fails" in text
    # Read-only SQL with the extraction guardrails.
    assert "describe_table" in text and "read-only" in text
    assert "never paste results" in text


def test_knowledge_writing_has_the_catalog_only_and_no_attachments():
    mode = MODES["knowledge"]
    assert (mode.kind, mode.queries, mode.attachments) == ("data", False, False)
    assert mode.tools == CATALOG_TOOLS
    server = config("knowledge")["mcp_servers"]["ihs-data"]
    assert server["disabled_tools"] == [
        "query", "check_workflow", "propose_plan", "ask_research_helper"
    ]  # fmt: skip
    assert config("knowledge")["web_search"] == "disabled"
    # Every other mode keeps its tools and attachments.
    for other in set(MODES) - {"knowledge"}:
        assert MODES[other].tools is None and MODES[other].tools_off == ()
        assert MODES[other].queries and MODES[other].attachments


def test_knowledge_writings_instructions():
    text = MODES["knowledge"].instructions
    assert "/work/kb" in text and "kb-propose" in text and "kb-use" in text
    assert "never write or\n  change `reviewed_by` or `reviewed_on`" in text
    assert "Never change a page's `status`" in text
    assert "proposed knowledge edits" in text and "You never save anything" in text
    assert "can't query the\n  database in this mode" in text
    assert "Files can't be attached here" in text
    assert "write the draft to /work/outputs/ instead" in text  # no knowledge base here
    assert "No participant-level data" in text
    for tool in ("search_catalog", "describe_table", "join_paths", "find_concept"):
        assert tool in text


def test_workflow_authorings_example_passes_the_real_profiles_check():
    """The file shape in the instructions, its two elisions filled in, is a
    file DataLab would run: they can't drift apart."""
    text = MODES["workflows"].instructions
    example = text.split("```yaml\n")[1].split("```")[0]
    example = example.replace("SELECT ... FROM", "SELECT STUDY_PARTICIPANT_ID, RECORD_DATE FROM")
    example = example.replace("      ...\n", "      out <- x\n")
    workflow = load_workflow(example, allowed_schemas=None, require_small_cells=True)
    assert workflow.read_objects == {"IHS_2025.VFITBITDAILYDATA"}
