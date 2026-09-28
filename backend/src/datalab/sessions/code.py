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

import json
import os
import re
import shlex
import threading
from collections import OrderedDict
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
    ".yaml": "yaml",
    ".yml": "yaml",
}  # Only types the file viewer shows as text too (api/files.py _TEXT).
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


# Checkpoints never change once written, so what each holds (its code files)
# is read once; a listing is kept until a new checkpoint or baseline.
_ENTRIES: OrderedDict[tuple[str, int], tuple[Checkpoint, dict[str, Entry]]] = OrderedDict()
_LISTINGS: OrderedDict[tuple[Any, ...], Listing] = OrderedDict()
_MAX_ENTRIES = 5000  # checkpoints' code entries kept, across conversations
_MAX_LISTINGS = 64
_cache_lock = threading.Lock()


def _code_entries(
    checkpoints: Checkpoints, number: int
) -> tuple[Checkpoint, dict[str, Entry]] | None:
    key = (str(checkpoints.store), number)
    with _cache_lock:
        if key in _ENTRIES:
            _ENTRIES.move_to_end(key)
            return _ENTRIES[key]
    summary = checkpoints.get(number)
    if summary is None:
        return None
    entries = {rel: e for rel, e in checkpoints.entries(number).items() if is_code(rel)}
    with _cache_lock:
        _ENTRIES[key] = (summary, entries)
        while len(_ENTRIES) > _MAX_ENTRIES:
            _ENTRIES.popitem(last=False)
    return summary, entries


def code_files(checkpoints: Checkpoints, baseline_folders: Iterable[str] = ()) -> Listing:
    """Every code file the checkpoints saw, with its versions, newest change first.

    `baseline_folders` are /work folders DataLab copied in before the first
    turn; they're used only when the checkpoints don't record what was copied
    (conversations from before they did): then a file there as the first
    checkpoint has it counts as copied in.
    """
    numbers = checkpoints.numbers()
    folders = tuple(sorted(f"{folder.rstrip('/')}/" for folder in baseline_folders))
    key = (
        str(checkpoints.store),
        tuple(numbers[-1:]),
        len(numbers),
        checkpoints.baseline_stamp(),
        folders,
    )
    with _cache_lock:
        if key in _LISTINGS:
            _LISTINGS.move_to_end(key)
            return _LISTINGS[key]
    listing = _code_files(checkpoints, numbers, folders)
    with _cache_lock:
        _LISTINGS[key] = listing
        while len(_LISTINGS) > _MAX_LISTINGS:
            _LISTINGS.popitem(last=False)
    return listing


def _code_files(checkpoints: Checkpoints, numbers: list[int], folders: tuple[str, ...]) -> Listing:
    listed = [c for c in (_code_entries(checkpoints, n) for n in numbers) if c is not None]
    recorded = {rel: v for rel, v in checkpoints.baseline().items() if is_code(rel)}
    history: dict[str, list[Version]] = {
        rel: [Version(0, None, "", "As copied into the workspace", sha, size)]
        for rel, (sha, size) in recorded.items()
    }
    baseline = set(recorded)
    if not recorded and listed and folders:
        baseline = {rel for rel in listed[0][1] if rel.startswith(folders)}
    latest_entries: dict[str, Entry] = {}
    for checkpoint, entries in listed:
        for rel, entry in entries.items():
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
        files,
        sum(f.status == "unchanged" for f in files),
        listed[-1][0].number if listed else None,
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


_KERNELSPEC_KEYS = ("name", "display_name", "language")
_LANGUAGE_INFO_KEYS = ("name", "version", "file_extension", "mimetype")


def _load_notebook(data: bytes) -> tuple[dict[str, Any], list[int]] | None:
    """A notebook rebuilt from an allowlist, and how many outputs each cell had.

    Only what DataLab shows is kept: `nbformat` and `nbformat_minor`; of the
    metadata, the kernel's and language's names (strings only); and for each
    cell its type (code, markdown or raw), source, id, and empty metadata. A
    code cell gets no outputs and no execution count. Anything else (outputs,
    attachments, unknown keys, nested metadata, papermill parameters) is left
    behind, since any of it can hold data. None if it isn't a notebook DataLab
    can read: not JSON, a pre-v4 notebook, or one with `worksheets`.
    """
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict) or "worksheets" in raw or not isinstance(raw.get("cells"), list):
        return None
    nbformat = raw.get("nbformat")
    if not isinstance(nbformat, int) or isinstance(nbformat, bool) or nbformat < 4:
        return None
    minor = raw.get("nbformat_minor")
    metadata = _dict(raw.get("metadata"))
    kept_metadata: dict[str, Any] = {}
    for key, fields in (("kernelspec", _KERNELSPEC_KEYS), ("language_info", _LANGUAGE_INFO_KEYS)):
        section = {
            f: v for f, v in _dict(metadata.get(key)).items() if f in fields and isinstance(v, str)
        }
        if section:
            kept_metadata[key] = section
    cells: list[dict[str, Any]] = []
    outputs: list[int] = []
    for cell in raw["cells"]:
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type")
        kind = kind if kind in ("code", "markdown", "raw") else "raw"
        source = cell.get("source", "")
        if isinstance(source, list):
            source = [part for part in source if isinstance(part, str)]
        elif not isinstance(source, str):
            source = ""
        clean: dict[str, Any] = {"cell_type": kind}
        if isinstance(cell.get("id"), str) and len(cell["id"]) <= 64:
            clean["id"] = cell["id"]
        clean["metadata"] = {}
        clean["source"] = source
        if kind == "code":
            clean["outputs"] = []
            clean["execution_count"] = None
        cells.append(clean)
        had = cell.get("outputs")
        outputs.append(len(had) if isinstance(had, list) else 0)
    notebook: dict[str, Any] = {"nbformat": nbformat}
    if isinstance(minor, int) and not isinstance(minor, bool):
        notebook["nbformat_minor"] = minor
    notebook["metadata"] = kept_metadata
    notebook["cells"] = cells
    return notebook, outputs


def read_notebook(data: bytes) -> Notebook | None:
    """A notebook's cells as DataLab shows them: its allowlisted form (the same
    one an export carries), with a count of the outputs left out. None if it
    isn't one DataLab can read."""
    loaded = _load_notebook(data)
    if loaded is None:
        return None
    notebook, outputs = loaded
    metadata = notebook["metadata"]
    kernel, info = _dict(metadata.get("kernelspec")), _dict(metadata.get("language_info"))
    named = str(kernel.get("language") or info.get("name") or "python").lower()
    language = "r" if named == "r" else "python" if named.startswith("python") else "text"
    cells = []
    for cell, count in zip(notebook["cells"], outputs, strict=True):
        source = cell["source"]
        text = "".join(source) if isinstance(source, list) else source
        cells.append(Cell(cell["cell_type"], text, count))
    return Notebook(language, cells)


def strip_notebook(data: bytes) -> bytes | None:
    """The notebook as it may leave DataLab or be shown: its allowlisted form
    (see _load_notebook). None if it isn't one DataLab can read."""
    loaded = _load_notebook(data)
    if loaded is None:
        return None
    return (json.dumps(loaded[0], indent=1, ensure_ascii=False) + "\n").encode()


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
    # Too long or too different to compare (MAX_DIFF_INPUT_LINES, DIFF_BUDGET).
    too_large: bool = False


MAX_DIFF_INPUT_LINES = 20_000  # of either side: longer ones aren't compared
DIFF_BUDGET = 1_000_000  # steps of work one comparison may take


def diff_texts(old: str, new: str, context: int = 3) -> Diff:
    """A unified diff, line by line, with the line numbers on each side.

    Myers' algorithm, with its work capped: versions over MAX_DIFF_INPUT_LINES
    lines, or too different to compare within DIFF_BUDGET, come back
    `too_large` (checked before, and while, comparing), so a large or
    repetitive file can't hold the server up.
    """
    a, b = old.splitlines(), new.splitlines()
    diff = Diff()
    if len(a) > MAX_DIFF_INPUT_LINES or len(b) > MAX_DIFF_INPUT_LINES:
        diff.too_large = True
        return diff
    # The lines both share at the start and the end aren't compared, so a
    # large change on one side doesn't use up the budget on them.
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < len(a) - head and tail < len(b) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    middle = _opcodes(a[head : len(a) - tail], b[head : len(b) - tail], DIFF_BUDGET)
    if middle is None:
        diff.too_large = True
        return diff
    ops: list[Opcode] = [("equal", 0, head, 0, head)] if head else []
    ops += [(t, i1 + head, i2 + head, j1 + head, j2 + head) for t, i1, i2, j1, j2 in middle]
    if tail:
        ops.append(("equal", len(a) - tail, len(a), len(b) - tail, len(b)))
    for group in _grouped(ops, len(a), len(b), context):
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


Opcode = tuple[str, int, int, int, int]


def _opcodes(a: list[str], b: list[str], budget: int) -> list[Opcode] | None:
    """difflib-style opcodes from Myers' shortest edit script, or None past `budget`."""
    n, m = len(a), len(b)
    # Only added, or only removed: nothing to search.
    if not n or not m:
        return [("insert", 0, 0, 0, m)] if m else ([("delete", 0, n, 0, 0)] if n else [])
    offset = n + m + 1
    v = [0] * (2 * offset + 1)
    snapshots: list[list[int]] = []
    work = 0
    found = False
    for d in range(n + m + 1):
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                x = v[offset + k + 1]
            else:
                x = v[offset + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
                work += 1
            v[offset + k] = x
            work += 1
            if x >= n and y >= m:
                found = True
                break
        snapshots.append(v[offset - d : offset + d + 1])
        work += 2 * d + 1
        if found:
            break
        if work > budget:
            return None
    # Back from the end: each step is one line removed or added, after a run of equal ones.
    steps: list[tuple[str, int, int]] = []  # ("=", i, j), ("-", i, j), ("+", i, j)
    x, y = n, m
    for d in range(len(snapshots) - 1, 0, -1):
        before = snapshots[d - 1]
        k = x - y
        down = k == -d or (k != d and before[k - 1 + d - 1] < before[k + 1 + d - 1])
        previous_k = k + 1 if down else k - 1
        px = before[previous_k + d - 1]
        py = px - previous_k
        sx, sy = (px, py + 1) if down else (px + 1, py)
        while x > sx and y > sy:
            x -= 1
            y -= 1
            steps.append(("=", x, y))
        steps.append(("+", px, py) if down else ("-", px, py))
        x, y = px, py
    while x > 0 and y > 0:
        x -= 1
        y -= 1
        steps.append(("=", x, y))
    steps.reverse()
    ops: list[list[Any]] = []
    i = j = 0
    for kind, _, _ in steps:
        tag = {"=": "equal", "-": "delete", "+": "insert"}[kind]
        if ops and (ops[-1][0] == tag or (ops[-1][0] != "equal" and tag != "equal")):
            if ops[-1][0] != tag:
                ops[-1][0] = "replace"
        else:
            ops.append([tag, i, i, j, j])
        if kind != "+":
            i += 1
        if kind != "-":
            j += 1
        ops[-1][2], ops[-1][4] = i, j
    return [(str(t), int(i1), int(i2), int(j1), int(j2)) for t, i1, i2, j1, j2 in ops]


def _grouped(ops: list[Opcode], n: int, m: int, context: int) -> list[list[Opcode]]:
    """The changes, each with `context` equal lines around it (difflib's grouping)."""
    codes = list(ops) or [("equal", 0, 1, 0, 1)]
    if codes[0][0] == "equal":
        tag, i1, i2, j1, j2 = codes[0]
        codes[0] = (tag, max(i1, i2 - context), i2, max(j1, j2 - context), j2)
    if codes[-1][0] == "equal":
        tag, i1, i2, j1, j2 = codes[-1]
        codes[-1] = (tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context))
    span = context + context
    groups: list[list[Opcode]] = []
    group: list[Opcode] = []
    for tag, i1, i2, j1, j2 in codes:
        if tag == "equal" and i2 - i1 > span:
            group.append((tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context)))
            groups.append(group)
            group = []
            i1, j1 = max(i1, i2 - context), max(j1, j2 - context)
        group.append((tag, i1, i2, j1, j2))
    if group and not (len(group) == 1 and group[0][0] == "equal"):
        groups.append(group)
    return groups


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


_WRAPPER = re.compile(r"^\s*(?:\S*/)?(?:ba|z)?sh\s+-l?c\s+\"")
# Inside double quotes, bash's backslash escapes only these (and a newline).
_DQ_ESCAPES = '$`"\\'


def _double_quoted(text: str) -> str | None:
    """A double-quoted shell word's text, as bash reads it: `text` starts just
    after the opening quote and must end at the closing one. None if it doesn't."""
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            following = text[i + 1]
            if following in _DQ_ESCAPES:
                out.append(following)
            elif following != "\n":  # a backslash-newline joins the lines
                out.append(char + following)
            i += 2
            continue
        if char == '"':
            return "".join(out) if not text[i + 1 :].strip() else None
        out.append(char)
        i += 1
    return None


def unwrap_shell(command: str) -> str:
    """The command inside a `bash -lc '…'` wrapper, as bash would run it."""
    wrapped = _WRAPPER.match(command)
    if wrapped:
        # shlex doesn't know bash's double-quote escapes (\$, \`), so read those itself.
        inner = _double_quoted(command[wrapped.end() :])
        if inner is not None:
            return inner
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
