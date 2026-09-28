"""The code a conversation wrote: scripts, SQL and notebooks, each version, and inline snippets.

Everything comes from what DataLab already keeps, never from the live folder:

- **Files** from the checkpoints (checkpoints.py). A code file's versions are
  the checkpoints where its content changed. Files DataLab copied into
  `/work` before the first turn (the knowledge base, the pipelines repo) are
  the conversation's *baseline*: one that's still as copied is unchanged and
  hidden unless asked for, one the agent changed is "modified", and anything
  else is "new".
- **Inline code** from the event log: Python or R the agent ran without
  saving a file (`python3 -c`, `Rscript -e`, a heredoc) and multi-line shell
  commands. Their text is already in the chat's "How this answer was made".

Only code text is returned: a notebook's cell outputs (which can hold data)
never leave here, and nor does anything a command printed.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from datalab.sessions.checkpoints import Checkpoint, Checkpoints, Entry

Status = Literal["new", "modified", "deleted", "unchanged"]

# A version larger than this is listed, but its text isn't shown.
MAX_TEXT_BYTES = 1024**2
MAX_FILES = 500  # code files one listing returns
MAX_INLINE = 200  # inline snippets one listing returns (the latest)
MAX_INLINE_CHARS = 20_000  # of one snippet's text
MAX_DIFF_LINES = 20_000  # of one diff's output

_LANGUAGES = {
    ".r": "r",
    ".py": "python",
    ".sql": "sql",
    ".ipynb": "notebook",
    ".qmd": "markdown",
    ".rmd": "markdown",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".jl": "text",
    ".do": "text",
    ".sas": "text",
}
CODE_SUFFIXES = frozenset(_LANGUAGES)
# Tool folders, not the agent's code: git's own files, caches, installed packages.
_SKIPPED_DIRS = frozenset(
    {".git", ".ipynb_checkpoints", "__pycache__", "node_modules", ".venv", "venv", "renv",
     ".Rproj.user", ".cache", ".local", ".config"}
)  # fmt: skip


def language_of(path: str) -> str | None:
    """The language of a code file, by its extension; None if it isn't code."""
    return _LANGUAGES.get(os.path.splitext(path)[1].lower())


def is_code(path: str) -> bool:
    if language_of(path) is None:
        return False
    folders = path.split("/")[:-1]
    return not any(part in _SKIPPED_DIRS or part.startswith(".") for part in folders)


@dataclass(frozen=True)
class Version:
    """One saved version of a file: the checkpoint where its content became this."""

    checkpoint: int  # 0: as DataLab copied it in, before the first turn
    turn: int | None
    created_at: str
    label: str
    sha256: str
    size: int


@dataclass(frozen=True)
class CodeFile:
    path: str
    language: str
    status: Status
    versions: list[Version]  # oldest first
    # In the latest checkpoint (the file as the workspace has it now).
    current: bool

    @property
    def last(self) -> Version:
        return self.versions[-1]


@dataclass(frozen=True)
class Listing:
    files: list[CodeFile]
    unchanged: int  # baseline code files still as copied (left out unless asked for)
    latest: int | None  # the latest checkpoint's number
    more: int = 0  # files left out past MAX_FILES


def code_files(checkpoints: Checkpoints, baseline_folders: Iterable[str] = ()) -> Listing:
    """Every code file the checkpoints saw, with its versions, newest change first.

    `baseline_folders` are /work folders DataLab copied in before the first
    turn; they're used only when the checkpoints don't record what was copied
    (conversations from before they did): then a file there as the first
    checkpoint has it counts as copied in.
    """
    listed = checkpoints.list()
    recorded = {rel: v for rel, v in checkpoints.baseline().items() if is_code(rel)}
    history: dict[str, list[Version]] = {
        rel: [Version(0, None, "", "As copied into the workspace", sha, size)]
        for rel, (sha, size) in recorded.items()
    }
    baseline = set(recorded)
    if not recorded and listed:
        folders = tuple(f"{folder.rstrip('/')}/" for folder in baseline_folders)
        if folders:
            first = checkpoints.entries(listed[0].number)
            baseline = {rel for rel in first if rel.startswith(folders) and is_code(rel)}
    latest_entries: dict[str, Entry] = {}
    for checkpoint in listed:
        entries = checkpoints.entries(checkpoint.number)
        for rel, entry in entries.items():
            if not is_code(rel):
                continue
            versions = history.setdefault(rel, [])
            if not versions or versions[-1].sha256 != entry.sha256:
                versions.append(_version(checkpoint, entry))
        latest_entries = entries
    files = []
    for rel, versions in history.items():
        in_baseline = rel in baseline
        current = rel in latest_entries
        if not current:
            status: Status = "deleted"
        elif not in_baseline:
            status = "new"
        elif len(versions) == 1:
            status = "unchanged"
        else:
            status = "modified"
        language = language_of(rel) or "text"
        files.append(CodeFile(rel, language, status, versions, current))
    # Newest change first; then by path, so the order is stable.
    files.sort(key=lambda f: (-f.last.checkpoint, f.path))
    return Listing(
        files, sum(f.status == "unchanged" for f in files), listed[-1].number if listed else None
    )


def _version(checkpoint: Checkpoint, entry: Entry) -> Version:
    return Version(
        checkpoint.number,
        checkpoint.turn,
        checkpoint.created_at,
        checkpoint.label,
        entry.sha256,
        entry.size,
    )


def find_version(listing: Listing, path: str, checkpoint: int | None) -> tuple[CodeFile, Version]:
    """The version of `path` as of `checkpoint` (its latest if None): the one
    saved at that checkpoint or last before it. KeyError if there's none."""
    file = next((f for f in listing.files if f.path == path), None)
    if file is None:
        raise KeyError(path)
    if checkpoint is None:
        return file, file.last
    earlier = [v for v in file.versions if v.checkpoint <= checkpoint]
    if not earlier:
        raise KeyError(checkpoint)
    return file, earlier[-1]


def read_version(checkpoints: Checkpoints, version: Version) -> bytes | None:
    """A version's bytes, or None if it's larger than MAX_TEXT_BYTES."""
    if version.size > MAX_TEXT_BYTES:
        return None
    with os.fdopen(checkpoints.open_object_by_hash(version.sha256), "rb") as source:
        data = source.read(MAX_TEXT_BYTES + 1)
    return None if len(data) > MAX_TEXT_BYTES else data


# --- Notebooks: their cells, never their outputs -------------------------------


@dataclass(frozen=True)
class Cell:
    kind: Literal["code", "markdown", "raw"]
    source: str
    outputs: int  # how many outputs it had (not shown)


@dataclass(frozen=True)
class Notebook:
    language: str
    cells: list[Cell]

    @property
    def outputs(self) -> int:
        return sum(c.outputs for c in self.cells)


def read_notebook(data: bytes) -> Notebook | None:
    """A Jupyter notebook's cells, without outputs. None if it isn't one."""
    try:
        raw = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("cells"), list):
        return None
    metadata = _dict(raw.get("metadata"))
    kernel, info = _dict(metadata.get("kernelspec")), _dict(metadata.get("language_info"))
    named = str(kernel.get("language") or info.get("name") or "python").lower()
    language = "r" if named == "r" else "python" if named.startswith("python") else "text"
    cells = []
    for cell in raw["cells"]:
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type")
        source = cell.get("source", "")
        text = "".join(str(s) for s in source) if isinstance(source, list) else str(source)
        outputs = cell.get("outputs")
        cells.append(
            Cell(
                kind if kind in ("code", "markdown") else "raw",
                text,
                len(outputs) if isinstance(outputs, list) else 0,
            )
        )
    return Notebook(language, cells)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def notebook_text(notebook: Notebook) -> str:
    """A notebook as text for comparing versions: each cell's source, marked by kind."""
    parts = []
    for i, cell in enumerate(notebook.cells, start=1):
        parts.append(f"# ── cell {i} ({cell.kind}) ──\n{cell.source.rstrip()}\n")
    return "\n".join(parts)


# --- Diffs --------------------------------------------------------------------


@dataclass(frozen=True)
class DiffLine:
    op: Literal[" ", "+", "-", "@"]  # "@": a hunk's header
    old: int | None  # 1-based line in the old text
    new: int | None
    text: str


@dataclass
class Diff:
    lines: list[DiffLine] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    truncated: bool = False


def diff_texts(old: str, new: str, context: int = 3) -> Diff:
    """A unified diff, line by line, with the line numbers on each side."""
    a, b = old.splitlines(), new.splitlines()
    diff = Diff()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in matcher.get_grouped_opcodes(context):
        first, last = group[0], group[-1]
        header = f"@@ -{first[1] + 1},{last[2] - first[1]} +{first[3] + 1},{last[4] - first[3]} @@"
        diff.lines.append(DiffLine("@", None, None, header))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    diff.lines.append(DiffLine(" ", i1 + k + 1, j1 + k + 1, a[i1 + k]))
                continue
            for k in range(i1, i2):
                diff.lines.append(DiffLine("-", k + 1, None, a[k]))
                diff.removed += 1
            for k in range(j1, j2):
                diff.lines.append(DiffLine("+", None, k + 1, b[k]))
                diff.added += 1
        if len(diff.lines) > MAX_DIFF_LINES:
            diff.lines = diff.lines[:MAX_DIFF_LINES]
            diff.truncated = True
            break
    return diff


# --- Inline code, from the event log -------------------------------------------


@dataclass(frozen=True)
class InlineCode:
    id: str  # the command's id: its step in the chat is "cmd-<id>"
    turn: int
    language: str
    code: str
    truncated: bool
    exit_code: int | None


_INTERPRETERS = {"python": "python", "python3": "python", "Rscript": "r", "R": "r"}
_HEREDOC = re.compile(
    r"(?:^|[\s;&|(])(python3?|Rscript|R|bash|sh)\b[^\n]*?<<-?\s*(['\"]?)(\w+)\2[^\n]*\n"
    r"(.*?)\n[ \t]*\3[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_FLAG = re.compile(r"(?:^|[\s;&|(])(python3?|Rscript|R)\s+(?:--?[\w-]+\s+)*?(-c|-e)\s+")
_DUCKDB = re.compile(r"(?:^|[\s;&|(])(duckdb|sqlite3)\b[^\n]*?\s-c\s+")
_WRITES_FILE = re.compile(r"\bcat\b[^\n]*>[^\n]*<<|\btee\b[^\n]*<<|<<[^\n]*>\s*\S")


def unwrap_shell(command: str) -> str:
    """The command inside a `bash -lc '…'` wrapper, as the agent wrote it."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if (
        len(argv) == 3
        and os.path.basename(argv[0]) in ("bash", "sh", "zsh")
        and argv[1] in ("-c", "-lc")
    ):
        return argv[2]
    return command


def inline_code(command: str) -> tuple[str, str] | None:
    """The code a command ran without a saved file, with its language, or None.

    Python or R passed with `-c`/`-e` or on standard input (a heredoc), SQL
    passed to duckdb or sqlite3 with `-c`, and shell commands of three lines
    or more. A heredoc that writes a file isn't: that file is a saved script.
    """
    inner = unwrap_shell(command).strip()
    heredoc = _HEREDOC.search(inner)
    if heredoc and not _WRITES_FILE.search(inner[: heredoc.end(3)]):
        program = heredoc.group(1)
        language = _INTERPRETERS.get(program, "shell")
        return language, heredoc.group(4)
    for pattern, language in ((_FLAG, None), (_DUCKDB, "sql")):
        match = pattern.search(inner)
        if match:
            rest = inner[match.end() :]
            try:
                code = shlex.split(rest)[0] if rest else ""
            except (ValueError, IndexError):
                code = rest
            if code.strip():
                return language or _INTERPRETERS[match.group(1)], code
    lines = [line for line in inner.splitlines() if line.strip()]
    if len(lines) >= 3 and not _WRITES_FILE.search(inner):
        return "shell", inner
    return None


def inline_snippets(events: Iterable[Any]) -> list[InlineCode]:
    """The inline code in a conversation's event log, oldest first, the latest MAX_INLINE.

    `events`: its user_message, command_started, command_finished,
    review_started and review_finished events, in order. A rigor review's own
    commands aren't the agent's work, so they're left out.
    """
    turn = 0
    reviewing = False
    found: dict[str, InlineCode] = {}
    for event in events:
        if event.type == "user_message":
            turn += 1
        elif event.type == "review_started":
            reviewing = True
        elif event.type == "review_finished":
            reviewing = False
        elif event.type == "command_started" and not reviewing:
            command = event.data.get("command")
            ident = str(event.data.get("id") or "")
            if not isinstance(command, str) or not ident:
                continue
            what = inline_code(command)
            if what is None:
                continue
            language, code = what
            found[ident] = InlineCode(
                ident,
                turn,
                language,
                code[:MAX_INLINE_CHARS],
                len(code) > MAX_INLINE_CHARS,
                None,
            )
        elif event.type == "command_finished":
            ident = str(event.data.get("id") or "")
            if ident in found:
                exit_code = event.data.get("exit_code")
                snippet = found[ident]
                found[ident] = InlineCode(
                    snippet.id,
                    snippet.turn,
                    snippet.language,
                    snippet.code,
                    snippet.truncated,
                    exit_code if isinstance(exit_code, int) else None,
                )
    return list(found.values())[-MAX_INLINE:]
