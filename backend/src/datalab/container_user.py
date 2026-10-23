"""Who DataLab's containers run as, where their image's own user won't do.

Docker Desktop (Mac, Windows) shows a bind-mounted folder to a container as
writable by whoever the container runs as. Native Linux Docker (CI, the
lab's server) shows the host's owner and modes as they are: a folder DataLab
made is writable only by DataLab's own user. So there, containers that write
to such folders run as that user; never as root.
"""

from __future__ import annotations

import os
import sys


def host_user() -> str | None:
    """DataLab's own user as "uid:gid" on native Linux, unless that's root;
    None elsewhere."""
    if sys.platform.startswith("linux") and hasattr(os, "getuid") and os.getuid() != 0:
        return f"{os.getuid()}:{os.getgid()}"
    return None
