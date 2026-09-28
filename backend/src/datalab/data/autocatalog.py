"""Build DataLab's own catalog the first time it can reach the database.

Every column a query names is checked against the catalog, so with an empty
one no query runs, and the agent's catalog search finds nothing. When the
lab's settings name no catalog folder (the practice profile never does),
DataLab keeps its own in the data folder and fills it from the database's
catalog views the first time it connects: the same metadata-only read as
`datalab catalog --from-database` (tables, columns, comments, primary keys;
never a row). A catalog folder the lab's settings name is never written.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from datalab.data.catalog import Catalog

log = logging.getLogger(__name__)

# How long to wait before trying again after the database couldn't be
# reached (not started yet, no password saved).
RETRY_SECONDS = 30.0


class CatalogAutoBuild:
    def __init__(
        self,
        catalog: Catalog,
        folder: Path,
        build: Callable[[], Catalog],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._catalog = catalog
        self._folder = folder
        self._build = build
        self._clock = clock
        self._lock = threading.Lock()
        self._next_try = 0.0
        # Why the last try didn't build it, safe to show (never a password).
        self.problem: str | None = None

    def ensure(self) -> bool:
        """Build the catalog if it's empty and it's time to try. Whether it has tables now.

        Never raises: a database that can't be reached yet is tried again later.
        """
        if len(self._catalog):
            return True
        with self._lock:
            if len(self._catalog):
                return True
            if self._clock() < self._next_try:
                return False
            try:
                built = self._build()
            except Exception as error:
                self._next_try = self._clock() + RETRY_SECONDS
                self.problem = _safe_message(error)
                log.warning("Couldn't build the catalog yet: %s", self.problem)
                return False
            if not len(built):
                self._next_try = self._clock() + RETRY_SECONDS
                self.problem = "The database showed no tables or views in the cohort schemas."
                log.warning("Couldn't build the catalog yet: %s", self.problem)
                return False
            try:
                self._save(built)
            except OSError as error:
                # Still used now; built again at the next start.
                log.warning("Couldn't save the catalog: %s", error)
            self._catalog.replace(built)
            self.problem = None
            log.info("Built the catalog: %d tables and views.", len(built))
            return True

    def try_again_soon(self) -> None:
        """Don't wait out the retry delay (a password was just saved)."""
        self._next_try = 0.0

    def _save(self, built: Catalog) -> None:
        # Written beside the folder, then moved into place: a half-written
        # catalog is never loaded at the next start.
        staging = self._folder.with_name(f".{self._folder.name}.building")
        shutil.rmtree(staging, ignore_errors=True)
        built.save(staging)
        if self._folder.exists():
            shutil.rmtree(self._folder)
        staging.rename(self._folder)


def _safe_message(error: Exception) -> str:
    """The first line of the error: Oracle's and DataLab's messages never
    contain the password."""
    text = str(error).strip().splitlines()
    return text[0][:300] if text else type(error).__name__
