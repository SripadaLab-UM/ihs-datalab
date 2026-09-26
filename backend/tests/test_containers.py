"""Container state checks fail closed: if Docker can't answer, nothing is assumed stopped."""

import pytest

from datalab.sessions import containers as module
from datalab.sessions.containers import DockerError, SessionContainers, SessionPaths


def make(tmp_path):
    return SessionContainers(
        "c_1", "data", SessionPaths(tmp_path), agent_image="img", host_port=1, profile="practice"
    )


def fake_docker(monkeypatch, answers):
    """Answer `docker inspect` from a list; record other docker commands."""
    calls = []

    async def status(*args):
        return answers.pop(0)

    async def docker(*args, check=True):
        calls.append(args[0])
        return ""

    monkeypatch.setattr(module, "docker_status", status)
    monkeypatch.setattr(module, "docker", docker)
    return calls


async def test_stop_isnt_confirmed_if_docker_cant_answer(tmp_path, monkeypatch):
    fake_docker(monkeypatch, [(1, "", "Cannot connect to the Docker daemon")])
    with pytest.raises(DockerError):
        await make(tmp_path).stop_and_confirm()


async def test_stop_is_confirmed_when_the_container_is_gone(tmp_path, monkeypatch):
    fake_docker(monkeypatch, [(1, "", "Error: No such object: datalab-c-1-agent")])
    await make(tmp_path).stop_and_confirm()


async def test_pause_must_be_confirmed(tmp_path, monkeypatch):
    calls = fake_docker(monkeypatch, [(0, "true false", ""), (0, "true false", "")])
    with pytest.raises(DockerError):
        await make(tmp_path).pause()  # still running after "pause"
    assert calls == ["pause"]
    fake_docker(monkeypatch, [(0, "true false", ""), (0, "true true", "")])
    await make(tmp_path).pause()
    fake_docker(monkeypatch, [(1, "", "permission denied")])
    with pytest.raises(DockerError):
        await make(tmp_path).pause()
