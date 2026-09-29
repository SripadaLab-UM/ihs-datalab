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
        "pipelines": ("Pipelines", "data"),
        "workflows": ("Workflow authoring", "data"),
        "knowledge": ("Knowledge writing", "data"),
        "sql": ("SQL drafting", "data"),
        "research": ("Research", "research"),
    }
    assert all(m.description and m.starters for m in MODES.values())
    # These are docked by their tabs, not offered for a new conversation.
    docked = {m.id for m in MODES.values() if m.tab_only}
    assert docked == {"workflows", "knowledge", "sql", "pipelines"}
    # The docked chats' starters are short: one or two, a line each.
    for mode in (m for m in MODES.values() if m.tab_only):
        assert 1 <= len(mode.starters) <= 2
        assert all(len(s) <= 70 for s in mode.starters), mode.id


def test_every_mode_gets_the_knowledge_base_note():
    for mode_id in MODES:
        assert modes.instructions(mode_id).endswith(modes.KNOWLEDGE)
        # Or, where DataLab copies none (practice), that there isn't one.
        without = modes.instructions(mode_id, knowledge_base=False)
        assert without.endswith(modes.NO_KNOWLEDGE) and "/work/kb: use it" not in without


def test_workflow_authoring_queries_like_data_extraction():
    workflows, extraction = MODES["workflows"], MODES["extraction"]
    assert (workflows.kind, workflows.queries) == (extraction.kind, extraction.queries)
    assert (workflows.kind, workflows.queries) == ("data", True)
    # Only Data extraction suggests Knowledge updates.
    assert workflows.tools_off == ("propose_sql", "suggest_kb_update")
    assert extraction.tools_off == ("propose_sql",)
    server = config("workflows")["mcp_servers"]["ihs-data"]
    assert server["disabled_tools"] == ["propose_sql", "suggest_kb_update"]
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
    assert "DataLab checks the file again before Save & share" in text
    assert "A file that fails can't be saved" in text
    # Read-only SQL with the extraction guardrails.
    assert "describe_table" in text and "read-only" in text
    assert "never paste results" in text


def test_knowledge_writing_has_the_catalog_only_and_no_attachments():
    mode = MODES["knowledge"]
    assert (mode.kind, mode.queries, mode.attachments) == ("data", False, False)
    assert mode.tools == CATALOG_TOOLS
    server = config("knowledge")["mcp_servers"]["ihs-data"]
    assert server["disabled_tools"] == [
        "query", "check_workflow", "propose_plan", "ask_research_helper", "propose_sql",
        "suggest_kb_update",
    ]  # fmt: skip
    assert config("knowledge")["web_search"] == "disabled"
    # Every other mode keeps its tools and attachments, all but the SQL tab's own.
    for other in set(MODES) - {"knowledge", "sql", "pipelines"}:
        assert MODES[other].tools is None and "propose_sql" in MODES[other].tools_off
        assert set(MODES[other].tools_off) <= {"propose_sql", "suggest_kb_update"}
        assert MODES[other].queries and MODES[other].attachments


def test_only_the_sql_playgrounds_chat_proposes_sql():
    """propose_sql fills the Playground's editor, so only its chat has it;
    it queries (small checks) but can't plan, check workflows or attach files."""
    mode = MODES["sql"]
    assert (mode.kind, mode.queries, mode.attachments, mode.tab_only) == ("data", True, False, True)
    assert "propose_sql" in mode.allowed_tools
    assert [m.id for m in MODES.values() if "propose_sql" in m.allowed_tools] == ["sql"]
    assert config("sql")["mcp_servers"]["ihs-data"]["disabled_tools"] == [
        "check_workflow", "propose_plan", "suggest_kb_update"
    ]  # fmt: skip
    text = mode.instructions
    assert "call `propose_sql` exactly once" in text
    assert "Only that query reaches the editor" in text
    assert "you never run the final extraction" in " ".join(text.split())
    assert "call propose_sql again with the whole revised query" in " ".join(text.split())


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
    blocks = [b.split("```")[0] for b in text.split("```yaml\n")[1:]]
    example = next(b for b in blocks if b.startswith("name:"))
    example = example.replace("SELECT ... FROM", "SELECT STUDY_PARTICIPANT_ID, RECORD_DATE FROM")
    example = example.replace("      ...\n", "      out <- x\n")
    workflow = load_workflow(example, allowed_schemas=None, require_small_cells=True)
    assert workflow.read_objects == {"IHS_2025.VFITBITDAILYDATA"}


def test_the_pipelines_tabs_chat_explains_edits_and_tests_code():
    """Its own tab-only mode: Data engineering's rules, word for word, with
    the file open in the tab; the catalog, small checks and workflow checks,
    but no analysis plans, no SQL proposals, and nothing attached."""
    mode = MODES["pipelines"]
    assert (mode.kind, mode.queries, mode.attachments, mode.tab_only) == ("data", True, False, True)
    assert mode.allowed_tools == CATALOG_TOOLS | {"query", "check_workflow", "ask_research_helper"}
    assert config("pipelines")["mcp_servers"]["ihs-data"]["disabled_tools"] == [
        "propose_plan", "propose_sql", "suggest_kb_update"
    ]  # fmt: skip
    assert config("pipelines")["web_search"] == "disabled"
    text = mode.instructions
    assert text.endswith(modes.ENGINEERING_RULES)
    assert modes.ENGINEERING_RULES in MODES["engineering"].instructions
    assert "explain what it does" in text and "change nothing" in text
    assert "comes back as a proposal" in text and "Only a person saves a change" in text
    # Engineering's safety rules come along.
    assert "Never look for credentials" in " ".join(text.split())
    assert "Add a test for every change" in text


def test_each_tabs_chat_works_on_what_the_tab_has_open():
    sql = " ".join(MODES["sql"].instructions.split())
    assert "When they ask what a query does, describe it" in sql
    assert "propose nothing unless they ask" in sql
    workflows = " ".join(MODES["workflows"].instructions.split())
    assert '("The workflow file <path>")' in workflows
    assert "changes that same file, keeping its `name:`" in workflows
    knowledge = " ".join(MODES["knowledge"].instructions.split())
    assert '("The page open in the Knowledge tab (<path>)")' in knowledge
    assert "changes that same file in /work/kb" in knowledge


def test_only_workspace_modes_with_durable_findings_suggest_knowledge_updates():
    """suggest_kb_update: Analysis, Data extraction and Data engineering. Not
    the tabs' own chats (SQL, Pipelines, Workflows, Knowledge writing), nor
    Research, which has no data tools."""
    having = [m.id for m in MODES.values() if "suggest_kb_update" in m.allowed_tools]
    assert having == ["analysis", "extraction", "engineering"]
    for mode_id in having:
        text = " ".join(MODES[mode_id].instructions.split())
        assert "call `suggest_kb_update`" in text
        assert "at most one or two an answer" in text
        assert "none for a one-off result" in text
        assert "counts of fewer than 11 people" in text
        assert config(mode_id)["mcp_servers"]["ihs-data"]["disabled_tools"] == ["propose_sql"]
    for mode_id in set(MODES) - set(having):
        assert "suggest_kb_update" not in MODES[mode_id].instructions
