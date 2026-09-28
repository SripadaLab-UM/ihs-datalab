"""Make sure the generator only ever touches the local synthetic database.

The generator drops and recreates the IHS cohort schemas, so it must never run
against a real database. Three checks, all required:

1. the address is this computer (localhost);
2. the server says it is Oracle Database Free, in the FREEPDB1 container;
3. the marker table DATALAB_SYNTHETIC.MARKER, created by the generator, is
   what DataLab's practice profile checks for before it runs any query.
"""

from __future__ import annotations

import re

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Fixed, public, dev-only passwords (synthetic/README.md): SYSTEM's, and the
# app user DATALAB_RO's. They unlock fake data on this computer and nothing
# else. Never reuse them.
ADMIN_PWD = "SynthDev2026"
RO_PWD = "datalab_ro"
MARKER_SCHEMA = "DATALAB_SYNTHETIC"
MARKER_NOTE = "Synthetic IHS database: every row is fake (ihs_datalab/synthetic)."


class NotTheSyntheticDatabase(RuntimeError):
    """Refused: the database isn't the local synthetic one. Nothing was changed."""


def require_local_dsn(dsn: str) -> None:
    match = re.match(r"^\[?([^\]/:]+)\]?", dsn.strip())
    host = match.group(1).lower() if match else ""
    if host not in LOCAL_HOSTS:
        raise NotTheSyntheticDatabase(
            f"Refusing to run: {dsn!r} is not a database on this computer."
        )


def require_synthetic_server(cur) -> None:
    """Call as SYSTEM, before changing anything."""
    cur.execute("SELECT banner FROM v$version")
    banners = " ".join(row[0] for row in cur)
    cur.execute("SELECT SYS_CONTEXT('USERENV', 'CON_NAME') FROM dual")
    container = cur.fetchone()[0]
    if "Free" not in banners or container != "FREEPDB1":
        raise NotTheSyntheticDatabase(
            "Refusing to run: this server doesn't look like the local Oracle Database Free "
            f"container (container {container!r})."
        )


def create_marker(cur, reader: str) -> None:
    """Create the marker DataLab's practice profile requires. Call as SYSTEM.

    The generator creates it last, once every cohort is loaded, so a build
    that stopped part way has no marker and is built again from scratch."""
    cur.execute(f"CREATE USER {MARKER_SCHEMA} NO AUTHENTICATION")
    cur.execute(f"GRANT UNLIMITED TABLESPACE TO {MARKER_SCHEMA}")
    cur.execute(f"CREATE TABLE {MARKER_SCHEMA}.MARKER (NOTE VARCHAR2(200))")
    cur.execute(f"INSERT INTO {MARKER_SCHEMA}.MARKER VALUES (:1)", [MARKER_NOTE])
    cur.execute(f"GRANT SELECT ON {MARKER_SCHEMA}.MARKER TO {reader}")
