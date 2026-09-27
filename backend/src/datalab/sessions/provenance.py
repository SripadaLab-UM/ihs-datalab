"""Provenance: what produced each number, table, and figure in an answer, and each output file.

DataLab doesn't watch what happens inside the container. What it does know:
each turn's commands (and their output), its queries (the Data accessed log),
and, from the checkpoints, which turn left each file as it is. Provenance
ties those together, and says plainly where it can't be exact: a number
"appears in" a command's output (not "was computed by" it), and a file first
saved in the checkpoint after turn 3 was written "by one of the commands
then" when no single command names it.

It never passes on data. Query results and command output are used only to
match numbers and file names; what's returned names the query (for the Data
accessed log) and the command, not what they printed. Script contents can be
shown elsewhere: they're workspace files the person already sees.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from datalab.sessions.tracing import Source, numbers_in_answer, trace_sources

# Caps: answers and turns can be long, and an event or a chain must stay small.
MAX_NUMBERS = 100  # numbers in one answer's provenance
MAX_SOURCES = 5  # places one number appears
MAX_FILES = 20  # output files one answer names
MAX_COMMANDS = 20  # commands one chain lists
MAX_SCRIPTS = 10  # scripts one chain lists
MAX_QUERIES = 20  # queries one chain lists
MAX_COMMAND_TEXT = 500  # of a command's text, as listed
_MAX_SCRIPT_READ = 256 * 1024  # of a script, to see which files it names
_MAX_DATA_READ = 1024**2  # of an output data file, to match numbers in it

# A script a command runs, or a script file named anywhere: its path, by extension.
_SCRIPT = re.compile(r"(?<![\w.-])((?:/?[\w.-]+/)*[\w.-]+\.(?:py|R|r|sql|sh|qmd|Rmd|jl))(?![\w.-])")
_DATA_FILE = (".csv", ".tsv")


# --- An answer: its numbers, and the files it names ------------------------


def answer_provenance(
    answer: str,
    evidence: list[tuple[Source, str]],
    output_paths: Iterable[str],
    answer_id: str | None = None,
) -> dict[str, Any]:
    """The `provenance` event for one answer (`answer_id`, as the answer event
    names it): each number with where it appears (at most MAX_SOURCES places),
    and the output files the answer names."""
    # Capped before matching, so a long answer can't make it slow.
    claims = trace_sources(answer, evidence, limit=MAX_SOURCES, max_numbers=MAX_NUMBERS)
    return {
        "answer": answer_id,
        "numbers": [
            {"text": c.text, "sources": [{"kind": s.kind, "ref": s.ref} for s in c.sources]}
            for c in claims
        ],
        "more_numbers": max(0, len(numbers_in_answer(answer)) - MAX_NUMBERS),
        "files": files_named(answer, output_paths)[:MAX_FILES],
    }


def files_named(answer: str, output_paths: Iterable[str]) -> list[str]:
    """The output files (paths in /work, as "outputs/fig1.png") an answer names:
    by their path, or by a file name only one output has."""
    paths = sorted(set(output_paths))
    by_name: dict[str, list[str]] = {}
    for path in paths:
        by_name.setdefault(os.path.basename(path), []).append(path)
    named = []
    for path in paths:
        name = os.path.basename(path)
        unique = len(by_name[name]) == 1
        if _names(answer, path) or (unique and _names(answer, name)):
            named.append(path)
    return named


def _names(text: str, name: str) -> bool:
    """Whether `text` names a file `name` (not just contains it: "a.csv" isn't in "data.csv")."""
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-]|\.\w)", text) is not None


def output_evidence(entries: dict[str, Any], read: Any) -> list[tuple[Source, str]]:
    """The data files in /work/outputs (CSV, TSV) as evidence a number can
    appear in. Reports the agent wrote aren't: they'd "trace" whatever they
    say. `read(entry, limit)` gives a checkpointed file's bytes."""
    evidence = []
    budget = 2 * _MAX_DATA_READ
    for path in sorted(entries):
        if budget <= 0:
            break
        if not path.startswith("outputs/") or not path.lower().endswith(_DATA_FILE):
            continue
        data = read(entries[path], min(budget, _MAX_DATA_READ))
        budget -= len(data)
        evidence.append((Source("file", path), data.decode("utf-8", "replace")))
    return evidence


def turn_answer(events: list[Any]) -> Any | None:
    """A turn's final answer: its last answer before any rigor review started."""
    review = next((i for i, e in enumerate(events) if e.type == "review_started"), len(events))
    answers = [e for e in events[:review] if e.type == "answer" and e.data.get("text")]
    final = [e for e in answers if e.data.get("phase") == "final_answer"] or answers
    return final[-1] if final else None


def record_turn(
    store: Any,
    conversation_id: str,
    since: int,
    sources: list[tuple[Source, str]],
    checkpoints: Any,
    checkpoint: int | None,
) -> dict[str, Any] | None:
    """After a turn: append the `provenance` event for its answer, if the answer
    states any numbers or names any output files. `sources` is what the turn
    produced (SessionManager.turn_sources); the turn's `checkpoint` adds the
    output data files and names the outputs (never the live folder, which the
    agent can change). Returns the event's data, or None if there was nothing
    to say."""
    answer = turn_answer(store.all_events_after(conversation_id, since))
    if answer is None:
        return None
    entries = checkpoints.entries(checkpoint) if checkpoint is not None else {}

    def read(entry: Any, limit: int) -> bytes:
        with os.fdopen(checkpoints.open_object(entry), "rb") as source:
            return source.read(limit)

    outputs = [path for path in entries if path.startswith("outputs/")]
    data = answer_provenance(
        str(answer.data["text"]),
        sources + output_evidence(entries, read),
        outputs,
        answer_id=answer.data.get("id"),
    )
    if not data["numbers"] and not data["files"]:
        return None
    store.append(conversation_id, "provenance", data)
    return data


# --- The turns, from the event log -------------------------------------------


@dataclass
class Command:
    id: str
    command: str
    exit_code: int | None = None
    output: str = ""  # for matching file names only; never passed on


@dataclass
class Turn:
    number: int  # as checkpoints count them: the nth question
    started_at: str
    ended_at: str | None = None  # when the next turn started, if one has
    commands: list[Command] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # files the agent edited directly
    # Its rigor review's own work: not the turn's, but it can change files too.
    review_commands: list[Command] = field(default_factory=list)
    review_changed: list[str] = field(default_factory=list)
    _by_id: dict[str, Command] = field(default_factory=dict, repr=False)


def turns_from_events(events: Iterable[Any]) -> dict[int, Turn]:
    """Each turn's commands and edited files, numbered as checkpoints number
    them (turn n starts at the nth question), with its rigor review's apart."""
    turns: dict[int, Turn] = {}
    turn: Turn | None = None
    reviewing = False
    for event in events:
        if event.type == "user_message":
            if turn is not None:
                turn.ended_at = event.created_at
            turn = Turn(len(turns) + 1, event.created_at)
            turns[turn.number] = turn
            reviewing = False
        elif turn is not None:
            reviewing = _add(turn, event, reviewing)
    return turns


def one_turn(events: Iterable[Any], number: int, ended_at: str | None = None) -> Turn | None:
    """Turn `number` alone, from events that start at its question (and end
    before the next one's, which started at `ended_at`)."""
    turn: Turn | None = None
    reviewing = False
    for event in events:
        if event.type == "user_message":
            if turn is not None:
                break
            turn = Turn(number, event.created_at, ended_at)
        elif turn is not None:
            reviewing = _add(turn, event, reviewing)
    return turn


def _add(turn: Turn, event: Any, reviewing: bool) -> bool:
    """Add one event to the turn; whether its rigor review is running after it."""
    data = event.data
    if event.type == "review_started":
        return True
    if event.type == "review_finished":
        return False
    if event.type == "command_started":
        command = Command(str(data.get("id")), str(data.get("command") or ""))
        (turn.review_commands if reviewing else turn.commands).append(command)
        turn._by_id[command.id] = command
    elif event.type == "command_output":
        command = turn._by_id.get(str(data.get("id")))
        if command is not None and len(command.output) < _MAX_SCRIPT_READ:
            command.output += str(data.get("text") or "")
    elif event.type == "command_finished":
        command = turn._by_id.get(str(data.get("id")))
        if command is not None:
            command.exit_code = data.get("exit_code")
            if not command.output:
                command.output = str(data.get("output") or "")[:_MAX_SCRIPT_READ]
    elif event.type == "files_changed":
        paths = [_in_work(str(p)) for p in data.get("paths") or [] if p]
        (turn.review_changed if reviewing else turn.changed).extend(paths)
    return reviewing


# --- A file: how it was made ------------------------------------------------

# The label DataLab gives the checkpoint after a turn's rigor review
# ("After turn 3's review", sessions/manager.py).
_REVIEW_LABEL = "'s review"


@dataclass(frozen=True)
class Version:
    """A checkpoint's summary, as the chain needs it (its files are read only if needed)."""

    number: int
    turn: int | None
    label: str
    review: bool = False  # taken after the turn's rigor review (checkpoints.Checkpoint.review)

    @property
    def after_review(self) -> bool:
        # Checkpoints saved before the flag existed say so only in their label.
        return self.review or self.label.endswith(_REVIEW_LABEL)


def file_chain(
    path: str,
    versions: list[Version],
    entries: Callable[[int], dict[str, Any]],
    turn_of: Callable[[int], Turn | None],
    queries: list[Any],
    read: Callable[[Any, int], bytes],
) -> dict[str, Any]:
    """How the file at `path` (in /work) came to be as it is in the latest checkpoint.

    From the checkpoints (newest first, reading each one's files only while
    the content is the same): the one that first saved its current content,
    and the turn, or the turn's rigor review, that it came after. From that:
    its commands (the likeliest writers first), the scripts they ran as they
    were then, and the queries whose results those read, plus the turn's
    others. `entries(n)` gives checkpoint n's files, `turn_of(n)` turn n, and
    `read(entry, limit)` a checkpointed file's bytes.
    """
    path = _in_work(path)
    current = entries(versions[-1].number).get(path) if versions else None
    if current is None:
        return {
            "path": path,
            "found": False,
            "summary": "This file isn't in the latest checkpoint.",
        }
    made = versions[-1]
    for version in reversed(versions[:-1]):
        entry = entries(version.number).get(path)
        if entry is None or entry.sha256 != current.sha256:
            break
        made = version
    in_review = made.after_review
    # Saved after the review, but the turn's own checkpoint wasn't saved: this
    # one holds the turn's changes too, so both its and the review's commands count.
    turn_not_saved = in_review and not any(
        v.turn == made.turn and not v.after_review and v.number < made.number for v in versions
    )
    chain: dict[str, Any] = {
        "path": path,
        "found": True,
        "checkpoint": made.number,
        "turn": made.turn,
        "in_review": in_review,
        "turn_not_saved": turn_not_saved,
        "commands": [],
        "more_commands": 0,
        "edited_directly": False,
        "scripts": [],
        "queries": [],
        "more_queries": 0,
    }
    turn = turn_of(made.turn) if made.turn is not None else None
    if turn is None:
        chain["summary"] = (
            f"Its current content was first saved in checkpoint {made.number} ({made.label}), "
            "which no turn made: a restore, or a file already there."
        )
        return chain

    if turn_not_saved:
        commands = turn.commands + turn.review_commands
    else:
        commands = turn.review_commands if in_review else turn.commands
    made_entries = entries(made.number)
    # Every script those commands ran, as it was then.
    scripts: dict[str, str] = {}  # path -> its text (read only to see what it names)
    ran: dict[str, list[str]] = {}  # command id -> the scripts it ran
    for command in commands:
        for found in _SCRIPT.findall(command.command):
            script = _in_work(found)
            entry = made_entries.get(script)
            if entry is None or script == path:
                continue
            if script not in scripts:
                scripts[script] = read(entry, _MAX_SCRIPT_READ).decode("utf-8", "replace")
            ran.setdefault(command.id, []).append(script)
    naming_scripts = [s for s, text in scripts.items() if _mentions(text, path)]

    def via(command: Command) -> str | None:
        return next((s for s in ran.get(command.id, []) if s in naming_scripts), None)

    # The likeliest writers first, without claiming one did: commands whose
    # text names the file, then those that ran a script that does, then those
    # whose output mentions it (listing or reading it, perhaps), then the rest.
    named = [c for c in commands if _mentions(c.command, path)]
    through = [c for c in commands if c not in named and via(c)]
    seen = [
        c for c in commands if c not in named and c not in through and _mentions(c.output, path)
    ]
    others = [c for c in commands if c not in named and c not in through and c not in seen]
    listed = (named + through + seen + others)[:MAX_COMMANDS]
    chain["commands"] = [
        {
            "id": c.id,
            "command": c.command[:MAX_COMMAND_TEXT],
            "exit_code": c.exit_code,
            "names_file": c in named,  # in the command's own text
            "via_script": via(c),  # a script it ran that names the file, if any
            "seen_in_output": c in seen,  # its output mentions the file (never shown)
        }
        for c in listed
    ]
    chain["more_commands"] = max(0, len(commands) - len(listed))
    changed = (
        turn.changed + turn.review_changed
        if turn_not_saved
        else (turn.review_changed if in_review else turn.changed)
    )
    chain["edited_directly"] = path in changed
    ordered = naming_scripts + [s for s in scripts if s not in naming_scripts]
    chain["scripts"] = [
        {"path": s, "sha256": made_entries[s].sha256, "names_file": s in naming_scripts}
        for s in ordered[:MAX_SCRIPTS]
    ]

    # Queries: those whose result file a listed script or command reads, then
    # the turn's others. Named by their Data accessed entry, never their rows.
    readers = [({"kind": "script", "ref": s}, scripts[s]) for s in ordered[:MAX_SCRIPTS]]
    readers += [({"kind": "command", "ref": c.id}, c.command) for c in listed]
    listed_queries = []
    for query in queries:
        if query.status != "succeeded":
            continue
        result = os.path.basename(query.result_path) if query.result_path else ""
        read_by = [who for who, text in readers if result and _names(text, result)]
        in_turn = turn.started_at <= query.started_at and (
            turn.ended_at is None or query.started_at < turn.ended_at
        )
        if read_by or in_turn:
            listed_queries.append(
                {
                    "id": query.id,
                    "started_at": query.started_at,
                    "tables": list(query.tables),
                    "row_count": query.row_count,
                    "result_file": result or None,
                    "read_by": read_by,  # the scripts and commands that name its result file
                    "in_turn": in_turn,
                }
            )
    listed_queries.sort(key=lambda q: (not q["read_by"], q["started_at"]))
    chain["queries"] = listed_queries[:MAX_QUERIES]
    chain["more_queries"] = max(0, len(listed_queries) - MAX_QUERIES)
    chain["summary"] = _summary(chain, len(named), len(through), len(seen), len(commands))
    return chain


def _summary(chain: dict[str, Any], named: int, through: int, seen: int, commands: int) -> str:
    """What's known, in a few sentences, without claiming more."""
    after = (
        f"turn {chain['turn']}'s rigor review" if chain["in_review"] else f"turn {chain['turn']}"
    )
    if chain["turn_not_saved"]:
        after += " (the turn's own checkpoint wasn't saved, so it may be the turn's change)"
    said = (
        f"Its current content was first saved in the checkpoint after {after} (checkpoint "
        f"{chain['checkpoint']})."
    )
    if chain["edited_directly"]:
        said += " The agent edited it directly then."
    if named:
        said += " One command then names it." if named == 1 else f" {named} commands then name it."
    elif through:
        script = next(c["via_script"] for c in chain["commands"] if c["via_script"])
        said += (
            f" No command names it, but {'one' if through == 1 else through} ran a script "
            f"that does ({script})."
        )
    elif commands:
        said += (
            f" No command names it, so it was written by one of the {commands} "
            f"command{'s' if commands != 1 else ''} then, or by a script they ran."
        )
        if seen:
            said += (
                f" {'One' if seen == 1 else seen} printed its name (listing or reading it, "
                "perhaps)."
            )
    elif not chain["edited_directly"]:
        said += " No commands ran then."
    return said + (
        " DataLab doesn't see which command writes a file inside the workspace, and this "
        "describes the file as the latest checkpoint saved it, not as it may be now."
    )


def _mentions(text: str, path: str) -> bool:
    """Whether `text` names the file at `path` (in /work): by that path (as
    /work/..., ./..., or relative to /work), or by its bare name. Another
    folder's file of the same name ("drafts/fig1.png") doesn't count."""
    name = os.path.basename(path)
    for match in re.finditer(rf"(?<![\w./-])([\w./-]*?){re.escape(name)}(?![\w-]|\.\w)", text):
        prefix = match.group(1)
        if not prefix or _in_work(prefix + name) == path:
            return True
    return False


def _in_work(path: str) -> str:
    """A path as checkpoints key it: relative to /work."""
    path = path.strip()
    for prefix in ("/work/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path
