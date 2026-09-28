"""Where DataLab's catalog comes from, and reading it again after a sync.

Every column a query names is checked against the catalog, so with an empty
one no query runs. In order, DataLab reads:

1. the folder `catalog_dir` in DataLab's settings names, if it does (and
   nothing else, even if that folder has no tables);
2. outside practice, the lab knowledge base's `generated/schema`, from its
   clone in `<data_dir>/repos/` (`[repos] knowledge`): GitHub's `main` as
   last synced, read from the clone's objects as the Knowledge tab reads it,
   never the working tree, and only regular files (no links);
3. DataLab's own folder, `<data_dir>/catalog` (practice DataLab builds its
   own there: autocatalog.py).

So an install whose settings never named a catalog folder finds the one in
the knowledge base once it's synced: at startup if the clone is there (the
installers sync it before DataLab first starts), and after each sync in the
app (`refresh`), with no restart. The files are the lab repository's, so
they're data: each is checked as the knowledge-base check checks it
(catalog.py's `from_files`), and a file that fails is left out.

While there's no usable catalog, `missing` on the catalog says what's
missing and how to fix it (the SQL check and the agent's tools say it; no
paths in it, since the agent reads it), and `detail` says it for Settings.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Literal

from datalab.config import Settings
from datalab.data.catalog import MAX_TABLE_BYTES, Catalog
from datalab.repos.git import Clone, GitError

log = logging.getLogger(__name__)

SCHEMA_FOLDER = "generated/schema"
# The least time between two reads while there's no catalog (a query, a
# catalog lookup): the clone may have appeared meanwhile.
RETRY_SECONDS = 10.0

Origin = Literal["setting", "knowledge", "data folder"]

_ASK = "if that doesn't help, ask the DataLab maintainer."


def knowledge_clone(settings: Settings) -> Clone | None:
    """The knowledge base's clone, for reading only (never synced from here),
    or None where the catalog doesn't come from it."""
    repo = settings.repos.knowledge
    if settings.profile == "practice" or settings.catalog_dir is not None or not repo:
        return None
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        return None
    # As knowledge/service.py names it.
    return Clone(settings.data_dir / "repos" / name, f"https://github.com/{repo}.git")


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
        # Where the tables came from, and the knowledge base's commit, if from there.
        self.origin: Origin | None = None
        self.head: str | None = None
        # Files left out, each with why (for Settings and the log).
        self.skipped: list[str] = []
        # Why there's no catalog, for Settings (signed in): may name folders.
        self.detail: str | None = None

    def load(self) -> bool:
        """Read the catalog from where it comes from now. Whether it has tables.

        Tables found replace the catalog's, in place: everything holding it
        (the SQL check, the agent's tools, the Playground) sees them at once.
        With none found, the catalog keeps what it had. Never raises.
        """
        with self._lock:
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

    def refresh(self) -> bool:
        """After the knowledge base synced: read it again if its commit
        changed (or there's no catalog yet). Whether it has tables."""
        if self._clone is None:
            return len(self.catalog) > 0
        try:
            head = self._clone.remote_head()
        except GitError:
            head = None
        if len(self.catalog) and head == self.head:
            return True
        return self.load()

    def ensure(self) -> bool:
        """Before a query or a catalog lookup: while there's no catalog, look
        again, at most every RETRY_SECONDS. Whether it has tables."""
        if len(self.catalog):
            return True
        if self._clock() < self._next_try:
            return False
        return self.load()

    # Reading ------------------------------------------------------------------

    def _read(self) -> tuple[Catalog, Origin, str | None] | None:
        settings = self._settings
        self.skipped = []
        if settings.catalog_dir is not None:
            catalog, self.skipped = Catalog.read(settings.catalog_dir)
            if len(catalog):
                return catalog, "setting", None
            return self._missing(
                "The catalog folder named in DataLab's settings (catalog_dir) has no table "
                f"files DataLab can read{self._left_out()}. Ask the DataLab maintainer.",
                f"The catalog folder in DataLab's settings ({settings.catalog_dir}) has no "
                f"tables DataLab can read{self._left_out()}. Build it with: datalab catalog "
                "--from-database --out <folder>",
            )
        try:
            from_knowledge = self._read_knowledge() if self._clone is not None else None
        except GitError as error:
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
        prefix = f"{SCHEMA_FOLDER}/"
        with clone.lock:
            head = clone.remote_head()
            if head is None:
                return None
            entries = {
                path[len(prefix) :]: entry
                for path, entry in clone.ls_tree(head).items()
                if path.startswith(prefix) and path.endswith(".yml")
            }
            wanted = {}
            for name, entry in entries.items():
                if name.count("/") != 1:
                    continue  # not <SCHEMA>/<TABLE>.yml
                if not entry.regular:
                    self.skipped.append(f"{name}: not a plain file")
                elif entry.size > MAX_TABLE_BYTES:
                    self.skipped.append(f"{name}: larger than 1 MB")
                else:
                    wanted[name] = entry
            blobs = clone.read_blobs(e.blob for e in wanted.values())
        files = {name: blobs.get(entry.blob, b"") for name, entry in wanted.items()}
        catalog, unread = Catalog.from_files(files)
        self.skipped += unread
        return catalog, head

    def _left_out(self) -> str:
        count = len(self.skipped)
        if not count:
            return ""
        return f" ({count} file{'s' if count != 1 else ''} left out as unreadable)"

    def _missing(self, message: str, detail: str) -> None:
        # Kept even if an older catalog is still in use, for the log.
        if not len(self.catalog):
            self.catalog.missing = message
            self.detail = detail
        log.warning("No catalog read: %s", detail)
        return None
