"""Finding Docker Desktop's `docker` command when it isn't on PATH.

On a Mac, Docker Desktop keeps its command line tools inside the app
(`Docker.app/Contents/Resources/bin`) and links them from /usr/local/bin, or
from ~/.docker/bin when it's set up for one user. When those links are
missing, or the folder isn't on PATH (an app opened from Finder, a shell
without it), `docker` isn't found although Docker Desktop is installed and
running. Every part of DataLab runs `docker` by name, so at start-up the
folder that holds it goes first on PATH, for DataLab and everything it
starts. Its credential helpers (`docker-credential-desktop`) are in the same
folder, which `docker pull` needs too. The installer does the same.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def candidates(home: Path | None = None) -> list[Path]:
    """Where Docker Desktop's `docker` command is on a Mac, most usual first."""
    home = home or Path.home()
    return [
        Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
        home / "Applications" / "Docker.app" / "Contents" / "Resources" / "bin" / "docker",
        home / ".docker" / "bin" / "docker",
    ]


def ensure_docker_on_path(
    environ: os._Environ[str] | dict[str, str] | None = None,
    *,
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path | None:
    """Put Docker Desktop's command folder on PATH if `docker` isn't found.
    Returns the folder added, or None (already found, not a Mac, not installed)."""
    env = os.environ if environ is None else environ
    if platform != "darwin" or shutil.which("docker", path=env.get("PATH", os.defpath)):
        return None
    for docker in candidates(home):
        if docker.is_file() and os.access(docker, os.X_OK):
            folder = docker.parent
            env["PATH"] = os.pathsep.join(p for p in (str(folder), env.get("PATH", "")) if p)
            return folder
    return None
