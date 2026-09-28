"""Where DataLab's catalog comes from, and reading it again after a sync.

Every column a query names is checked against the catalog, so with an empty
one no query runs. In order, DataLab reads:

1. the folder `catalog_dir` in DataLab's settings names, if it does (and
   nothing else, even if that folder has no tables);
2. outside practice, the lab knowledge base's `generated/schema`, from its
   clone in `<data_dir>/repos/` (`[repos] knowledge`): GitHub's `main` as
   last synced, read from the clone's objects as the Knowledge tab reads it,
   never the working tree, and only regular files (no links or submodules);
3. DataLab's own folder, `<data_dir>/catalog` (practice DataLab builds its
   own there: autocatalog.py).

So an install whose settings never named a catalog folder finds the one in
the knowledge base once it's synced: at startup if the clone is there (the
installers sync it before DataLab first starts), and after each sync in the
app (`refresh`), with no restart. The files are the lab repository's, so
they're data: each is checked as the knowledge-base check checks it
(catalog.py's `from_files`), a file that fails is left out, and a
`generated/schema` over MAX_FILES files or MAX_BYTES isn't read at all (the
catalog read before is kept). Still, the column names on GitHub's `main`
decide what the SQL check lets through: see docs/SAFETY.md.

While there's no usable catalog, `missing` on the catalog says what's
missing and how to fix it (the SQL check and the agent's tools say it; no
paths in it, since the agent reads it), and `detail` says it for Settings.

Locks: the clone's (shared with every sync of it) is always taken before
this source's own, never the other way round. `ensure`, on the way to a
query, doesn't wait for either: if a sync or a read is going on, it says
there's no catalog yet, and the sync reads it again once it's done.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from typing import Literal

from datalab.config import Settings
from datalab.data.catalog import MAX_TABLE_BYTES, Catalog, shown_name
from datalab.repos.git import Clone, GitError, clone_path

log = logging.getLogger(__name__)

SCHEMA_FOLDER = "generated/schema"
# The least time between two reads while there's no catalog (a query, a
# catalog lookup): the clone may have appeared meanwhile.
RETRY_SECONDS = 10.0
# The most of generated/schema DataLab reads (909 files, a few MB, in 2026).
MAX_FILES = 20_000
MAX_BYTES = 64 * 1024 * 1024

Origin = Literal["setting", "knowledge", "data folder"]

_ASK = "if that doesn't help, ask the DataLab maintainer."
_NO_GIT = (
    "Git isn't installed on this computer, so DataLab can't read the lab knowledge base. "
    "Ask the DataLab maintainer."
)


class _TooLarge(Exception):
    pass


def knowledge_clone(settings: Settings) -> Clone | None:
    """The knowledge base's clone, for reading only (never synced from here),
    or None where the catalog doesn't come from it."""
    repo = settings.repos.knowledge
    if settings.profile == "practice" or settings.catalog_dir is not None or not repo:
        return None
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        return None
    return Clone(clone_path(settings.data_dir, repo), f"https://github.com/{repo}.git")


def expected_origin(settings: Settings) -> Origin:
    """Where the catalog comes from here, whether or not it's there yet."""
    if settings.catalog_dir is not None:
        return "setting"
    return "knowledge" if knowledge_clone(settings) is not None else "data folder"


class CatalogSource:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.catalog = catalog
        self._settings = settings
        self._clone = knowledge_clone(settings)
        self._clock = clock
        self._lock = threading.Lock()
        self._next_try = 0.0
        # Where it's meant to come from, where the tables came from, and the
        # knowledge base's commit, if from there.
        self.expected: Origin = expected_origin(settings)
        self.origin: Origin | None = None
        self.head: str | None = None
        # Files left out, each with why (for Settings and the log).
        self.skipped: list[str] = []
        # Why there's no catalog, for Settings (signed in): may name folders.
        self.detail: str | None = None

    @contextlib.contextmanager
    def _held(self, *, wait: bool) -> Iterator[bool]:
        """The clone's lock, then this one's (always in that order). Without
        `wait`, False at once if either is taken."""
        with contextlib.ExitStack() as stack:
            for lock in (self._clone.lock if self._clone else None, self._lock):
                if lock is None:
                    continue
                if not lock.acquire(blocking=wait):
                    yield False
                    return
                stack.callback(lock.release)
            yield True

    def load(self) -> bool:
        """Read the catalog from where it comes from now. Whether it has tables.

        Tables found replace the catalog's, in place: everything holding it
        (the SQL check, the agent's tools, the Playground) sees them at once.
        With none found, the catalog keeps what it had. Never raises.
        """
        with self._held(wait=True):
            return self._load()

    def refresh(self) -> bool:
        """After the knowledge base synced: read it again if its commit
        changed (or there's no catalog yet). Whether it has tables."""
        if self._clone is None:
            return len(self.catalog) > 0
        with self._held(wait=True):
            try:
                head = self._clone.remote_head()
            except GitError:
                head = None
            if len(self.catalog) and head == self.head:
                return True
            return self._load()

    def ensure(self) -> bool:
        """Before a query or a catalog lookup: while there's no catalog, look
        again, at most every RETRY_SECONDS, never waiting for a sync or
        another read. Whether it has tables."""
        if len(self.catalog):
            return True
        if self._clock() < self._next_try:
            return False
        with self._held(wait=False) as held:
            if not held:
                return False
            if len(self.catalog):
                return True
            if self._clock() < self._next_try:
                return False
            return self._load()

    # Reading (with both locks held) -----------------------------------------------

    def _load(self) -> bool:
        try:
            found = self._read()
        except Exception as error:  # a bug in reading: DataLab still starts
            log.exception("Couldn't read the catalog")
            found = None
            self._missing(
                "DataLab couldn't read its catalog. Sync the knowledge base again in "
                f"Settings → Connections; {_ASK}",
                f"Reading it failed ({type(error).__name__}); DataLab's log has more.",
            )
        self._next_try = self._clock() + RETRY_SECONDS
        if found is not None:
            catalog, origin, head = found
            self.catalog.replace(catalog)
            self.catalog.missing = None
            self.origin, self.head, self.detail = origin, head, None
            log.info("Read the catalog (%s): %d tables and views.", origin, len(catalog))
        return len(self.catalog) > 0

    def _read(self) -> tuple[Catalog, Origin, str | None] | None:
        settings = self._settings
        self.skipped = []
        if settings.catalog_dir is not None:
            folder = settings.catalog_dir
            # The maintainer's setting: a link to the folder is followed, once.
            if folder.is_symlink():
                folder = folder.resolve()
            catalog, self.skipped = Catalog.read(folder)
            if len(catalog):
                return catalog, "setting", None
            return self._missing(
                "The catalog folder named in DataLab's settings (catalog_dir) has no table "
                f"files DataLab can read{self._left_out()}. Ask the DataLab maintainer.",
                f"The catalog folder in DataLab's settings ({settings.catalog_dir}) has no "
                f"tables DataLab can read{self._left_out()}. Build it with: datalab catalog "
                "--from-database --out <folder>",
            )
        from_knowledge = None
        if self._clone is not None:
            try:
                from_knowledge = self._read_knowledge()
            except _TooLarge as error:
                return self._missing(
                    "The lab knowledge base's generated/schema is larger than DataLab reads, "
                    "so it wasn't read. Ask the DataLab maintainer.",
                    f"The knowledge base's generated/schema is too large to read: {error} "
                    "The catalog read before, if any, is still used.",
                )
            except GitError as error:
                if shutil.which("git") is None:
                    return self._missing(_NO_GIT, _NO_GIT)
                return self._missing(
                    "DataLab couldn't read the lab knowledge base's clone. Sync it again in "
                    f"Settings → Connections; {_ASK}",
                    f"Reading the knowledge base's clone failed: {error}",
                )
        if from_knowledge is not None and len(from_knowledge[0]):
            return from_knowledge[0], "knowledge", from_knowledge[1]
        knowledge_skipped = self.skipped
        own = settings.data_dir / "catalog"
        catalog, own_skipped = Catalog.read(own)
        if len(catalog):
            self.skipped = own_skipped
            return catalog, "data folder", None
        self.skipped = knowledge_skipped + own_skipped
        if settings.profile == "practice":
            # Practice DataLab builds its own (autocatalog.py; app.catalog_problem says so).
            return None
        if self._clone is None:
            return self._missing(
                "This DataLab has no lab knowledge base set up ([repos] knowledge in its "
                "settings) and no catalog folder. Ask the DataLab maintainer.",
                "There's no knowledge base set in settings.toml ([repos] knowledge) to read "
                f"it from, and {own} has no tables. Build it with: datalab catalog "
                "--from-database --out <folder>, then name that folder as catalog_dir in "
                "DataLab's settings.toml.",
            )
        if from_knowledge is None:
            if shutil.which("git") is None:
                return self._missing(_NO_GIT, _NO_GIT)
            return self._missing(
                "It comes from the lab knowledge base (generated/schema), which DataLab "
                "hasn't downloaded yet. To fix it: sign in to GitHub in Settings → "
                "Connections and sync the knowledge base there; if you can't sign in or "
                "have no access, ask the DataLab maintainer.",
                "It comes from the lab knowledge base's generated/schema, which isn't "
                "downloaded yet: sign in to GitHub (Settings → Connections) and sync it.",
            )
        return self._missing(
            "It comes from the lab knowledge base, but its generated/schema has no table "
            f"files DataLab can read{self._left_out()}. Sync the knowledge base again in "
            f"Settings → Connections; {_ASK}",
            "The lab knowledge base, as last synced, has no tables DataLab can read in "
            f"generated/schema{self._left_out()}. Sync it again (Settings → Connections), "
            "or ask the DataLab maintainer to check the knowledge base.",
        )

    def _read_knowledge(self) -> tuple[Catalog, str] | None:
        """generated/schema at GitHub's main as last synced, or None if not synced yet."""
        clone = self._clone
        assert clone is not None
        if not clone.exists():
            return None
        prefix = f"{SCHEMA_FOLDER}/"
        head = clone.remote_head()
        if head is None:
            return None
        entries = {
            path[len(prefix) :]: entry
            for path, entry in clone.ls_tree(head, prefix).items()
            if path.startswith(prefix) and path.endswith(".yml")
        }
        wanted = {}
        for name, entry in entries.items():
            if name.count("/") != 1:
                continue  # not <SCHEMA>/<TABLE>.yml
            if not entry.regular:
                self.skipped.append(f"{shown_name(name)}: not a plain file")
            elif entry.size > MAX_TABLE_BYTES:
                self.skipped.append(f"{shown_name(name)}: larger than 1 MB")
            else:
                wanted[name] = entry
        total = sum(e.size for e in wanted.values())
        if len(wanted) > MAX_FILES or total > MAX_BYTES:
            raise _TooLarge(
                f"{len(wanted):,} files, {total // (1024 * 1024):,} MB "
                f"(DataLab reads at most {MAX_FILES:,} files, {MAX_BYTES // (1024 * 1024)} MB)."
            )
        blobs = clone.read_blobs(e.blob for e in wanted.values())
        files = {name: blobs.get(entry.blob, b"") for name, entry in wanted.items()}
        catalog, unread = Catalog.from_files(files)
        self.skipped += unread
        return catalog, head

    def _left_out(self) -> str:
        count = len(self.skipped)
        if not count:
            return ""
        return f" ({count} left out as unreadable or unsafe; DataLab's log names them)"

    def _missing(self, message: str, detail: str) -> None:
        # Settings' detail is only shown while there are no tables (app.catalog_problem);
        # an older catalog, if any, stays in use.
        self.detail = detail
        if not len(self.catalog):
            self.catalog.missing = message
        log.warning("No catalog read: %s", detail)
        return None
