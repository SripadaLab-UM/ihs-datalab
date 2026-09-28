"""The Code tab: the scripts, SQL and notebooks a conversation wrote, each saved version, and diffs.

Served from the checkpoints and the event log (sessions/code.py), never from
the live folder. Paths are checked like every workspace path, and only a
path the checkpoints recorded as a code file can be asked for. A notebook's
cell outputs are never sent: they can hold data.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalab.sessions import code
from datalab.sessions.checkpoints import Checkpoints, UnsafePath, check_relative
from datalab.sessions.manager import SessionManager
from datalab.sessions.store import ConversationStore

_EVENTS = (
    "user_message",
    "command_started",
    "command_finished",
    "review_started",
    "review_finished",
)


class CodeVersionOut(BaseModel):
    checkpoint: int  # 0: as DataLab copied it in, before the first turn
    turn: int | None
    created_at: str
    label: str
    size: int
    too_large: bool


class CodeFileOut(BaseModel):
    path: str  # in /work
    language: str
    status: Literal["new", "modified", "deleted", "unchanged"]
    size: int  # of its latest version
    versions: list[CodeVersionOut]  # oldest first
    # The latest version is the file as the workspace has it (as of the latest checkpoint).
    current: bool


class InlineCodeOut(BaseModel):
    id: str
    step: str  # the step in the chat's "How this answer was made"
    turn: int
    language: str
    code: str
    truncated: bool
    exit_code: int | None


class CodeListingOut(BaseModel):
    files: list[CodeFileOut]
    inline: list[InlineCodeOut]
    unchanged: int  # code files copied in and never changed, left out unless `all`
    more: int  # files left out past the cap
    latest_checkpoint: int | None


class CellOut(BaseModel):
    kind: Literal["code", "markdown", "raw"]
    source: str
    outputs: int  # how many outputs it had; they aren't sent


class NotebookOut(BaseModel):
    language: str
    cells: list[CellOut]
    outputs: int


class CodeTextOut(BaseModel):
    path: str
    language: str
    version: CodeVersionOut
    # This version is the file as the workspace has it now (the latest checkpoint).
    current: bool
    too_large: bool
    text: str | None  # None for a notebook (see `notebook`) or when too large
    notebook: NotebookOut | None
    unreadable: bool = False  # a notebook DataLab couldn't read


class DiffLineOut(BaseModel):
    op: Literal[" ", "+", "-", "@"]
    old: int | None
    new: int | None
    text: str


class CodeDiffOut(BaseModel):
    path: str
    language: str  # of the texts compared (a notebook's are its cells, as text)
    base: CodeVersionOut
    head: CodeVersionOut
    head_current: bool
    too_large: bool
    base_text: str | None
    head_text: str | None
    lines: list[DiffLineOut]
    added: int
    removed: int
    truncated: bool


def _version_out(version: code.Version) -> CodeVersionOut:
    return CodeVersionOut(
        checkpoint=version.checkpoint,
        turn=version.turn,
        created_at=version.created_at,
        label=version.label,
        size=version.size,
        too_large=version.size > code.MAX_TEXT_BYTES,
    )


def build_code_router(store: ConversationStore, sessions: SessionManager) -> APIRouter:
    router = APIRouter(prefix="/api/conversations/{conversation_id}/code", tags=["code"])

    def conversation_or_404(conversation_id: str) -> None:
        if store.get(conversation_id) is None:
            raise HTTPException(404, "No such conversation.")

    def listing(conversation_id: str) -> tuple[Checkpoints, code.Listing]:
        checkpoints = sessions.checkpoints(conversation_id)
        return checkpoints, code.code_files(checkpoints, sessions.seeded_folders(conversation_id))

    def version_of(
        conversation_id: str, path: str, checkpoint: int | None
    ) -> tuple[Checkpoints, code.Listing, code.CodeFile, code.Version]:
        try:
            check_relative(path)
        except UnsafePath as error:
            raise HTTPException(404, "No such code file.") from error
        checkpoints, listed = listing(conversation_id)
        try:
            file, version = code.find_version(listed, path, checkpoint)
        except KeyError as error:
            raise HTTPException(404, "No such code file (files appear after each turn).") from error
        return checkpoints, listed, file, version

    def is_current(file: code.CodeFile, version: code.Version) -> bool:
        return file.current and version is file.last

    def text_of(checkpoints: Checkpoints, file: code.CodeFile, version: code.Version):
        """(text to show or compare, notebook, too large, unreadable)."""
        data = code.read_version(checkpoints, version)
        if data is None:
            return None, None, True, False
        if file.language == "notebook":
            notebook = code.read_notebook(data)
            return None, notebook, False, notebook is None
        return data.decode("utf-8", "replace"), None, False, False

    @router.get("")
    def list_code(conversation_id: str, all: bool = False) -> CodeListingOut:
        """Code files this conversation made or changed (every one with `all`), and inline code."""
        conversation_or_404(conversation_id)
        _, listed = listing(conversation_id)
        files = [f for f in listed.files if all or f.status != "unchanged"]
        shown = files[: code.MAX_FILES]
        snippets = code.inline_snippets(store.events_of_types_after(conversation_id, 0, _EVENTS))
        return CodeListingOut(
            files=[
                CodeFileOut(
                    path=f.path,
                    language=f.language,
                    status=f.status,
                    size=f.last.size,
                    versions=[_version_out(v) for v in f.versions],
                    current=f.current,
                )
                for f in shown
            ],
            inline=[
                InlineCodeOut(
                    id=s.id,
                    step=f"cmd-{s.id}",
                    turn=s.turn,
                    language=s.language,
                    code=s.code,
                    truncated=s.truncated,
                    exit_code=s.exit_code,
                )
                for s in reversed(snippets)  # newest first, like the files
            ],
            unchanged=0 if all else listed.unchanged,
            more=len(files) - len(shown),
            latest_checkpoint=listed.latest,
        )

    @router.get("/version")
    def code_version(conversation_id: str, path: str, checkpoint: int | None = None) -> CodeTextOut:
        """One version of a code file: as saved at `checkpoint` (its latest if not given)."""
        conversation_or_404(conversation_id)
        checkpoints, _, file, version = version_of(conversation_id, path, checkpoint)
        text, notebook, too_large, unreadable = text_of(checkpoints, file, version)
        return CodeTextOut(
            path=file.path,
            language=notebook.language if notebook else file.language,
            version=_version_out(version),
            current=is_current(file, version),
            too_large=too_large,
            text=text,
            notebook=NotebookOut(
                language=notebook.language,
                cells=[
                    CellOut(kind=c.kind, source=c.source, outputs=c.outputs) for c in notebook.cells
                ],
                outputs=notebook.outputs,
            )
            if notebook
            else None,
            unreadable=unreadable,
        )

    @router.get("/diff")
    def code_diff(
        conversation_id: str, path: str, base: int, head: int | None = None
    ) -> CodeDiffOut:
        """What changed in a code file from the version at checkpoint `base` to the one
        at `head` (the file as it is now, if not given)."""
        conversation_or_404(conversation_id)
        checkpoints, listed, file, new = version_of(conversation_id, path, head)
        try:
            _, old = code.find_version(listed, path, base)
        except KeyError as error:
            raise HTTPException(404, "No such version of this file.") from error
        texts = []
        language = file.language
        for version in (old, new):
            text, notebook, too_large, unreadable = text_of(checkpoints, file, version)
            if notebook is not None:
                language = notebook.language
                text = code.notebook_text(notebook)
            texts.append(None if too_large or unreadable else text)
        too_large = any(t is None for t in texts)
        diff = code.Diff() if too_large else code.diff_texts(texts[0] or "", texts[1] or "")
        return CodeDiffOut(
            path=file.path,
            language=language,
            base=_version_out(old),
            head=_version_out(new),
            head_current=is_current(file, new),
            too_large=too_large,
            base_text=texts[0],
            head_text=texts[1],
            lines=[DiffLineOut(op=d.op, old=d.old, new=d.new, text=d.text) for d in diff.lines],
            added=diff.added,
            removed=diff.removed,
            truncated=diff.truncated,
        )

    return router
