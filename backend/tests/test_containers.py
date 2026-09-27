"""Container state checks fail closed: if Docker can't answer, nothing is assumed stopped."""

import os
import secrets

import pytest

from datalab.sessions import containers as module
from datalab.sessions.containers import DockerError, SessionContainers, SessionPaths


def make(tmp_path, instance="abc"):
    return SessionContainers(
        "c_1", "data", SessionPaths(tmp_path), agent_image="img", host_port=1,
        profile="practice", instance=instance,
    )  # fmt: skip


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
    fake_docker(monkeypatch, [(1, "", "Error: No such object: datalab-abc-c-1-agent")])
    await make(tmp_path).stop_and_confirm()


async def test_stopping_removes_the_network_after_the_containers(tmp_path, monkeypatch):
    """Networks left behind used up Docker's address pools after ~30 conversations."""
    calls = []

    async def docker(*args, check=True):
        calls.append(args)
        return ""

    monkeypatch.setattr(module, "docker", docker)
    containers = make(tmp_path)
    await containers.stop()
    assert calls[-1] == ("network", "rm", containers.network)
    assert all(call[:2] == ("rm", "-f") for call in calls[:-1])


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
    assert "datalab.instance=abc" in make(tmp_path)._labels


def test_names_are_unique_per_instance(tmp_path):
    """Two DataLabs whose data folders hold the same conversation (a copied
    folder, say) must not share a network or a container name: `start()`
    reuses a network by name and replaces a container by name."""
    first = make(tmp_path, module.instance_of(tmp_path / "a"))
    second = make(tmp_path, module.instance_of(tmp_path / "b"))
    for attribute in ("network", "gateway", "agent", "proxy"):
        mine, theirs = getattr(first, attribute), getattr(second, attribute)
        assert mine != theirs
        assert mine.startswith("datalab-") and "c-1" in mine
        assert len(mine) <= 63
    assert first.agent == f"{first.network}-agent"


def test_names_need_an_instance(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path, instance="")


@pytest.mark.docker
async def test_cleanup_finds_containers_named_before_instance_names(tmp_path):
    """Containers and networks named `datalab-<session>` (before names carried
    the instance) are still this instance's by label, and are cleaned up."""
    image = os.environ.get("DATALAB_TEST_AGENT_IMAGE", "datalab-probe:ci")
    if (await module.docker_status("image", "inspect", image))[0] != 0:
        pytest.skip(f"Build the probe image first: docker build -t {image} images/probe")
    # A profile of its own, so nothing outside this test can match.
    profile = f"test-{secrets.token_hex(4)}"
    tag = secrets.token_hex(4)
    labelled, unlabelled = f"c_{tag}a", f"c_{tag}b"
    (tmp_path / "sessions" / unlabelled).mkdir(parents=True)
    networks, containers = [], []
    try:
        for session, instance in ((labelled, module.instance_of(tmp_path)), (unlabelled, "")):
            old = f"datalab-{session.replace('_', '-')}"
            labels = [
                "--label", f"datalab.session={session}",
                "--label", f"datalab.profile={profile}",
            ]  # fmt: skip
            if instance:  # before instance labels, too
                labels += ["--label", f"datalab.instance={instance}"]
            await module.docker("network", "create", "--internal", *labels, old)
            networks.append(old)
            await module.docker("create", "--name", f"{old}-agent", *labels, image)
            containers.append(f"{old}-agent")
        await module.remove_all_session_containers(profile, tmp_path)
        owned = f"label=datalab.profile={profile}"
        assert not (await module.docker("ps", "-aq", "--filter", owned)).strip()
        assert not (await module.docker("network", "ls", "-q", "--filter", owned)).strip()
    finally:
        for name in containers:
            await module.docker("rm", "-f", name, check=False)
        for name in networks:
            await module.docker("network", "rm", name, check=False)
