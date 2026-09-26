"""Make sure these scripts only ever touch the local synthetic database.

The generator drops and recreates the IHS cohort schemas, so it must never run
against a real database. Three checks, all required:

1. the address is this computer (localhost);
2. the server says it is Oracle Database Free, in the FREEPDB1 container;
3. the marker table DATALAB_SYNTHETIC.MARKER, created by the generator, is
   what DataLab's practice profile checks for before it runs any query.
"""

from __future__ import annotations

import re
import sys

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MARKER_SCHEMA = "DATALAB_SYNTHETIC"
MARKER_NOTE = "Synthetic IHS database: every row is fake (ihs_datalab/synthetic)."


def require_local_dsn(dsn: str) -> None:
    match = re.match(r"^\[?([^\]/:]+)\]?", dsn.strip())
    host = match.group(1).lower() if match else ""
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing to run: {dsn!r} is not a database on this computer.")


def require_synthetic_server(cur) -> None:
    """Call as SYSTEM, before changing anything."""
    cur.execute("SELECT banner FROM v$version")
    banners = " ".join(row[0] for row in cur)
    cur.execute("SELECT SYS_CONTEXT('USERENV', 'CON_NAME') FROM dual")
    container = cur.fetchone()[0]
    if "Free" not in banners or container != "FREEPDB1":
        sys.exit(
            "Refusing to run: this server doesn't look like the local Oracle Database Free "
            f"container (container {container!r})."
        )


def create_marker(cur, reader: str) -> None:
    """Create the marker DataLab's practice profile requires. Call as SYSTEM."""
    cur.execute(f"CREATE USER {MARKER_SCHEMA} NO AUTHENTICATION")
    cur.execute(f"GRANT UNLIMITED TABLESPACE TO {MARKER_SCHEMA}")
    cur.execute(f"CREATE TABLE {MARKER_SCHEMA}.MARKER (NOTE VARCHAR2(200))")
    cur.execute(f"INSERT INTO {MARKER_SCHEMA}.MARKER VALUES (:1)", [MARKER_NOTE])
    cur.execute(f"GRANT SELECT ON {MARKER_SCHEMA}.MARKER TO {reader}")
