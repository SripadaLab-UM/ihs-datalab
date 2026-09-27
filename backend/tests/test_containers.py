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


async def test_cleanup_removes_only_this_instances_containers(monkeypatch, tmp_path):
    """A second DataLab with the same profile (an evaluation server) must not
    remove the first one's running sessions. Containers from before instance
    labels count as this instance's only if their session folder is here."""
    mine = module.instance_of(tmp_path)
    (tmp_path / "sessions" / "c_old").mkdir(parents=True)
    removed: list[tuple[str, ...]] = []

    async def docker(*args, check=True):
        if args[0] == "ps":
            return f"aaa|{mine}|c_1\nbbb|other|c_2\nccc||c_old\nddd||c_elsewhere\n"
        if args[:2] == ("network", "ls"):
            return f"n1|{mine}|c_1\nn2|other|c_2\nn3||c_elsewhere"
        removed.append(args)
        return ""

    monkeypatch.setattr(module, "docker", docker)
    await module.remove_all_session_containers("practice", tmp_path)
    assert removed == [("rm", "-f", "aaa", "ccc"), ("network", "rm", "n1")]


def test_instance_names_are_stable_per_data_folder(tmp_path):
    assert module.instance_of(tmp_path) == module.instance_of(tmp_path / ".")
    assert module.instance_of(tmp_path / "a") != module.instance_of(tmp_path / "b")


def test_containers_carry_their_instance(tmp_path):
    containers = SessionContainers(
        "c_1", "data", SessionPaths(tmp_path), agent_image="img", host_port=1,
        profile="practice", instance="abc",
    )  # fmt: skip
    assert "datalab.instance=abc" in containers._labels
