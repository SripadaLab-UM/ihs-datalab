"""Build DataLab's own catalog the first time it can reach the database.

Every column a query names is checked against the catalog, so with an empty
one no query runs, and the agent's catalog search finds nothing. Practice
DataLab keeps its own catalog in the data folder and fills it from the
synthetic database's catalog views the first time it connects: the same
metadata-only read as `datalab catalog --from-database` (tables, columns,
comments, primary keys; never a row). A catalog folder the lab's settings
name is never written. (For now practice only: app.catalog_auto_build.)

It's tried at startup, then at the next query or catalog lookup, at most
every RETRY_SECONDS. A wrong password, a locked or expired account, or a
database that isn't the synthetic one stops the tries until a password is
saved (try_again_soon) or DataLab starts again: trying again would only
lock the account, or keep knocking on the wrong database.

To rebuild it, delete `<data_dir>/catalog` and start DataLab again.
"""

from __future__ import annotations

import logging
import math
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import oracledb

from datalab.data.catalog import Catalog
from datalab.data.oracle import NotSyntheticDatabase

log = logging.getLogger(__name__)

# The least time between two tries after the database couldn't be reached
# (not started yet, no password saved).
RETRY_SECONDS = 30.0
# Oracle errors that trying again can't fix, and could make worse: a wrong
# password (repeated, it locks the account), a locked account, an expired password.
STOP_CODES = frozenset({"ORA-01017", "ORA-28000", "ORA-28001"})

# Coarse enough for /api/health, which anyone on this computer can read.
CatalogState = Literal["ready", "building", "waiting-for-database", "stopped", "empty"]


class CatalogAutoBuild:
    def __init__(
        self,
        catalog: Catalog,
        folder: Path,
        build: Callable[[], Catalog],
        *,
        clock: Callable[[], float] = time.monotonic,
        on_built: Callable[[Catalog], None] | None = None,
    ) -> None:
        self._catalog = catalog
        self._folder = folder
        self._build = build
        self._clock = clock
        self._on_built = on_built
        self._lock = threading.Lock()
        self._next_try = 0.0
        # Why the last try didn't build it (for signed-in Settings only; never
        # a password, but it may name the database user or the account's state).
        self.problem: str | None = None
        # Whether the tries stopped until a password is saved or DataLab restarts.
        self.stopped = False

    @property
    def state(self) -> CatalogState:
        if len(self._catalog):
            return "ready"
        if self.stopped:
            return "stopped"
        return "waiting-for-database" if self.problem else "building"

    def ensure(self) -> bool:
        """Build the catalog if it's empty and it's time to try. Whether it has tables now.

        Never raises: a database that can't be reached yet is tried again later.
        """
        if len(self._catalog):
            return True
        # One build at a time; other callers don't wait for it (they'd hold
        # worker threads for up to the whole build).
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if len(self._catalog):
                return True
            if self._clock() < self._next_try:
                return False
            try:
                built = self._build()
            except Exception as error:
                return self._failed(_safe_message(error), stop=_stops(error))
            if not len(built):
                return self._failed(
                    "The database showed no tables or views in the cohort schemas.", stop=False
                )
            try:
                self._save(built)
            except (OSError, ValueError) as error:
                # Still used now; built again at the next start.
                log.warning("Couldn't save the catalog: %s", error)
            self._catalog.replace(built)
            self.problem = None
            log.info("Built the catalog: %d tables and views.", len(built))
            if self._on_built is not None:
                try:
                    self._on_built(built)
                except Exception as error:
                    log.warning("Couldn't record the catalog build: %s", type(error).__name__)
            return True
        finally:
            self._lock.release()

    def try_again_soon(self) -> None:
        """Try again at the next chance (a password was just saved)."""
        with self._lock:
            self._next_try = 0.0
            self.stopped = False

    def _failed(self, problem: str, *, stop: bool) -> bool:
        self.problem = problem
        self.stopped = stop
        self._next_try = math.inf if stop else self._clock() + RETRY_SECONDS
        if stop:
            log.warning(
                "Stopped building the catalog until a password is saved or DataLab restarts: %s",
                problem,
            )
        else:
            log.warning("Couldn't build the catalog yet: %s", problem)
        return False

    def _save(self, built: Catalog) -> None:
        # Written beside the folder, then moved into place: a half-written
        # catalog is never loaded at the next start.
        staging = self._folder.with_name(f".{self._folder.name}.building")
        shutil.rmtree(staging, ignore_errors=True)
        built.save(staging)
        if self._folder.exists():
            shutil.rmtree(self._folder)
        staging.rename(self._folder)


def _stops(error: Exception) -> bool:
    """Whether trying again can't help (and could lock the account)."""
    if isinstance(error, NotSyntheticDatabase):
        return True
    if isinstance(error, oracledb.Error) and error.args:
        code = getattr(error.args[0], "full_code", None)
        return code in STOP_CODES
    return False


def _safe_message(error: Exception) -> str:
    """The first line of the error: Oracle's and DataLab's messages never
    contain the password."""
    text = str(error).strip().splitlines()
    return text[0][:300] if text else type(error).__name__
