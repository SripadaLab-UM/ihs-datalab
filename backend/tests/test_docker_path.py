"""DataLab finds Docker Desktop's `docker` when it isn't on PATH (a Mac
whose /usr/local/bin link is missing, or an app opened from Finder)."""

from __future__ import annotations

import stat
from pathlib import Path

from datalab import docker_path


def docker_in(folder: Path) -> Path:
    folder.mkdir(parents=True)
    docker = folder / "docker"
    docker.write_text("#!/bin/sh\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    return docker


def test_docker_in_your_applications_goes_last_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        docker_path,
        "candidates",
        lambda home=None: [
            tmp_path / "Applications/Docker.app/Contents/Resources/bin/docker",
            tmp_path / "home/Applications/Docker.app/Contents/Resources/bin/docker",
        ],
    )
    found = docker_in(tmp_path / "home/Applications/Docker.app/Contents/Resources/bin")
    # Only a folder of this test's own: a real docker elsewhere on the machine
    # (a CI runner's /usr/bin/docker) must not be found.
    nothing = tmp_path / "empty-bin"
    nothing.mkdir()
    env = {"PATH": str(nothing)}
    assert docker_path.ensure_docker_on_path(env, platform="darwin") == found.parent
    assert env["PATH"] == f"{nothing}:{found.parent}"


def test_nothing_changes_when_docker_is_on_path(tmp_path):
    on_path = docker_in(tmp_path / "bin")
    env = {"PATH": str(on_path.parent)}
    assert docker_path.ensure_docker_on_path(env, platform="darwin", home=tmp_path) is None
    assert env == {"PATH": str(on_path.parent)}


def test_nothing_changes_off_a_mac_or_without_docker(tmp_path):
    docker_in(tmp_path / ".docker/bin")
    env = {"PATH": "/nowhere"}
    assert docker_path.ensure_docker_on_path(env, platform="linux", home=tmp_path) is None
    assert env == {"PATH": "/nowhere"}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert docker_path.candidates(empty)[1:] == [
        empty / "Applications/Docker.app/Contents/Resources/bin/docker",
        empty / ".docker/bin/docker",
    ]


def test_the_docker_folder_in_your_home_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(
        docker_path,
        "candidates",
        lambda home=None: [tmp_path / "none/docker", tmp_path / ".docker/bin/docker"],
    )
    found = docker_in(tmp_path / ".docker/bin")
    env = {"PATH": str(tmp_path / "nowhere")}
    assert docker_path.ensure_docker_on_path(env, platform="darwin") == found.parent
    assert env["PATH"] == f"{tmp_path / 'nowhere'}:{found.parent}"
