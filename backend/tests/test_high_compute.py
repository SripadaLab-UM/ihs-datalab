"""High compute: conversations whose containers run on the lab's larger
machine (sessions/compute.py, the remote Engine in sessions/containers.py)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.config import HighComputeSettings, ServerSettings, _deployment
from datalab.sessions import compute
from datalab.sessions import containers as module
from datalab.sessions.containers import SessionContainers, SessionPaths
from tests.conftest import FakeDatabase
from tests.test_inputs_api import FakeContainers
from tests.test_route_access import JSON, SITE, signed_in

HIGH = HighComputeSettings(
    docker_host="unix:///run/datalab-compute/podman.sock",
    gpu_docker_host="unix:///run/datalab-compute/podman-gpu.sock",
    sessions_dir="/app/datalab-compute/practice/sessions",
    relay_socket="/app/datalab-compute/run/practice-relay.sock",
    agent_memory="64g",
    agent_cpus="16",
)


def server_settings(settings, high=HIGH, **changes):
    server = ServerSettings(public_url=SITE, admins=(1001,), high_compute=high)
    return replace(settings, deployment="server", server=server, **changes)


def test_settings_read_the_high_compute_table():
    raw = {
        "deployment": "server",
        "server": {
            "public_url": SITE,
            "high_compute": {
                "docker_host": "unix:///run/x.sock",
                "sessions_dir": "/srv/sessions",
                "relay_socket": "/srv/run/relay.sock",
                "agent_memory": "128g",
            },
        },
    }
    _, server = _deployment(raw)
    assert server is not None and server.high_compute is not None
    assert (server.high_compute.agent_memory, server.high_compute.gpu_docker_host) == ("128g", "")
    for bad in ({"docker_host": "tcp://x:2375"}, {"sessions_dir": "relative"}, {"open_to": "all"}):
        table = {**raw["server"]["high_compute"], **bad}
        with pytest.raises(ValueError, match="high_compute"):
            _deployment({**raw, "server": {"public_url": SITE, "high_compute": table}})
    with pytest.raises(ValueError, match="needed"):
        _deployment(
            {**raw, "server": {"public_url": SITE, "high_compute": {"docker_host": "unix:///x"}}}
        )


def test_the_gateway_reaches_datalab_through_the_relay_socket():
    local = module.render_gateway_conf("data", 8766)
    assert "proxy_pass http://host.docker.internal:8766/relay/v1/;" in local
    remote = module.render_gateway_conf("data", 8766, relay=True)
    assert "proxy_pass http://unix:/datalab/relay.sock:/relay/v1/;" in remote
    assert "proxy_pass http://unix:/datalab/relay.sock:/mcp;" in remote
    assert "host.docker.internal" not in remote
    # DataLab's listener still sees its own address.
    assert "proxy_set_header Host 127.0.0.1:8766;" in remote


async def test_a_remote_session_runs_on_the_other_machines_engine(tmp_path, monkeypatch, settings):
    calls: list[tuple[tuple[str, ...], dict | None]] = []

    async def docker(*args, check=True, env=None):
        calls.append((args, env))
        if args[0] == "inspect" and "IPAddress" in args[2]:
            return "10.89.0.7\n" if args[-1].endswith("gateway") else "10.89.0.8\n"
        return ""

    async def status(*args, env=None):
        calls.append((args, env))
        return (1, "", "Error: network name already exists")  # made by an earlier start

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module, "docker_status", status)
    on_server = server_settings(settings)
    engine = compute.engine_for(on_server, "high")
    containers = SessionContainers(
        "c_1", "research", SessionPaths(tmp_path), agent_image="img", host_port=8766,
        profile="practice", instance="abc", engine=engine,
        limits=compute.limits_for_compute(on_server, "high"), cpuset="16-31",
    )  # fmt: skip
    monkeypatch.setattr(containers, "_wait_for_proxy", _no_wait)
    await containers.start("token")
    remote = {"DOCKER_HOST": HIGH.docker_host}
    gpu = {"DOCKER_HOST": HIGH.gpu_docker_host}
    # Every command goes to the other machine; only the agent's run to its GPU API.
    runs = [(args, env) for args, env in calls if args[0] == "run"]
    gateway, proxy, agent = (
        next(c for c in runs if c[0][c[0].index("--name") + 1].endswith(name))
        for name in ("-gateway", "-proxy", "-agent")
    )
    assert all(env == remote for args, env in calls if args not in (agent[0],))
    assert agent[1] == gpu
    assert f"{HIGH.relay_socket}:/datalab/relay.sock" in gateway[0]
    assert "host.docker.internal:host-gateway" not in gateway[0]
    agent_args = agent[0]
    assert list(agent_args[agent_args.index("gateway:10.89.0.7") - 1 :][:2]) == [
        "--add-host",
        "gateway:10.89.0.7",
    ]
    assert "proxy:10.89.0.8" in agent_args and "--dns" in agent_args  # still no DNS out
    assert agent_args[agent_args.index("--memory") + 1] == "64g"
    assert agent_args[agent_args.index("--cpus") + 1] == "16"
    assert "--gpus" not in agent_args  # Podman's Docker API drops it: the GPU API adds it
    assert agent_args[agent_args.index("--cpuset-cpus") + 1] == "16-31"
    assert "--cpuset-cpus" not in gateway[0] and "--cpuset-cpus" not in proxy[0]
    assert proxy[1] == remote


async def _no_wait(*_args, **_kwargs):
    return None


def test_a_high_compute_conversation_lives_on_the_other_machine(settings):
    on_server = server_settings(settings)
    assert compute.session_root(on_server, "c_1", "high") == Path(HIGH.sessions_dir) / "c_1"
    assert (
        compute.session_root(on_server, "c_1", "standard") == settings.data_dir / "sessions" / "c_1"
    )
    # Without [server.high_compute] there is nowhere else.
    plain = server_settings(settings, high=None)
    assert compute.session_root(plain, "c_1", "high") == settings.data_dir / "sessions" / "c_1"
    assert compute.engine_for(plain, "high") is module.LOCAL


@pytest.fixture
def app(settings, catalog, tmp_path):
    high = replace(HIGH, sessions_dir=str(tmp_path / "compute-sessions"))
    app = create_app(
        server_settings(settings, high=high, profile="real"),
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
    )
    app.state.services.sessions._containers = lambda conversation: FakeContainers()
    return app


def test_only_those_allowed_may_choose_high_compute(app, tmp_path):
    with TestClient(app) as admin:
        person = TestClient(app)
        signed_in(app, admin, 1001, admin=True)
        signed_in(app, person, 1002, admin=False)
        offered = admin.get("/api/conversations/compute").json()["high"]
        assert offered == {"memory": "64g", "cpus": "16", "gpu": True}
        assert person.get("/api/conversations/compute").json() == {"high": None}
        body = {"mode": "analysis", "compute": "high"}
        refused = person.post("/api/conversations", json=body, headers=JSON)
        assert refused.status_code == 403
        made = admin.post("/api/conversations", json=body, headers=JSON)
        assert made.status_code == 201 and made.json()["compute"] == "high"
        cid = made.json()["id"]
        assert admin.get(f"/api/conversations/{cid}").json()["compute"] == "high"
        paths = app.state.services.sessions.paths(cid)
        assert paths.root == tmp_path / "compute-sessions" / cid
        standard = admin.post("/api/conversations", json={"mode": "analysis"}, headers=JSON)
        assert standard.json()["compute"] == "standard"


def test_a_desktop_offers_no_high_compute(settings, catalog):
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/conversations/compute").json() == {"high": None}
        refused = client.post("/api/conversations", json={"compute": "high"})
        assert refused.status_code == 403


def test_a_sample_goes_to_the_other_machine_with_the_conversation(settings, catalog, tmp_path):
    """Practice's samples are in DataLab's package here: a high-compute
    conversation gets a copy in its own folder, which is all that's mounted."""
    high = replace(HIGH, sessions_dir=str(tmp_path / "compute-sessions"))
    app = create_app(
        server_settings(settings, high=high), database=FakeDatabase(), catalog=catalog,
        manage_containers=False,
    )  # fmt: skip
    app.state.services.sessions._containers = lambda conversation: FakeContainers()
    with TestClient(app) as client:
        signed_in(app, client, 1001, admin=True)
        cid = client.post(
            "/api/conversations", json={"mode": "analysis", "compute": "high"}, headers=JSON
        ).json()["id"]
        sample = client.get("/api/input-samples").json()[0]
        added = client.post(
            f"/api/conversations/{cid}/inputs",
            json={"source": "sample", "sample": sample},
            headers=JSON,
        )
        assert added.status_code == 200, added.text
        [item] = added.json()["added"]
        assert item["host_path"].startswith(str(tmp_path / "compute-sessions" / cid / "uploads"))
        services = app.state.services
        from datalab.sessions.inputs import AttachmentStore
        from datalab.sessions.mounts import input_mounts

        mounts = " ".join(
            input_mounts(
                services.conversations,
                services.settings,
                AttachmentStore(services.conversations._db),
                cid,
            )
        )
        assert item["host_path"] in mounts


async def test_leftovers_on_the_other_machine_are_cleaned_up(monkeypatch, tmp_path, settings):
    """Docker's client can't list Podman's networks: each leftover session's
    is found from its containers, by the name DataLab gives it."""
    mine = module.instance_of(tmp_path)
    calls: list[tuple[tuple[str, ...], dict | None]] = []

    async def docker(*args, check=True, env=None):
        calls.append((args, env))
        if args[0] == "ps":
            return f"aaa|{mine}|c_1\nbbb|{mine}|c_1\nccc|other|c_2\n"
        return ""

    monkeypatch.setattr(module, "docker", docker)
    engine = compute.engine_for(server_settings(settings), "high")
    await module.remove_all_session_containers("practice", tmp_path, engine)
    removed = [args for args, env in calls if args[0] != "ps"]
    assert removed == [
        ("rm", "-f", "aaa", "bbb"),
        ("network", "rm", module.session_name("c_1", mine)),
    ]
    assert all(env == {"DOCKER_HOST": HIGH.docker_host} for _, env in calls)
    assert not any(args[:2] == ("network", "ls") for args, _ in calls)


def test_the_agent_is_told_its_real_limits(settings):
    """`nproc` and `free` show the whole machine (cgroup v1): the agent is told
    its share, and numeric libraries' threads are set to match."""
    on_server = server_settings(settings)
    high = compute.resources_note(on_server, "high")
    assert "high-compute server" in high
    assert "16 CPUs and 64 GB of memory, and has a GPU" in high
    assert "show the whole machine" in high
    standard = compute.resources_note(on_server, "standard")
    assert "1 CPU and 1536 MB of memory." in standard and "GPU" not in standard
    assert compute.resources_note(settings, "standard") == ""  # a person's own computer
    env = compute.limit_env(on_server, "high")
    assert {"DATALAB_CPUS=16", "DATALAB_MEMORY=64g", "OMP_NUM_THREADS=16", "MC_CORES=16"} <= set(
        env
    )
    assert compute.limit_env(settings, "high") == []


def test_cores_are_split_into_slots_of_an_agents_size():
    assert replace(HIGH, cpuset="0-31").cpu_slots == ("0-15", "16-31")
    eight = replace(HIGH, cpuset="0-7,16-23", agent_cpus="4", max_running=2)
    assert eight.cpu_slots == ("0-3", "4-7", "16-19", "20-23")
    assert replace(HIGH, cpuset="").cpu_slots == ()
    with pytest.raises(ValueError, match="room for 1 agents"):
        replace(HIGH, cpuset="0-23", max_running=2)  # one slot of 16, and 8 left over
    with pytest.raises(ValueError, match="whole number"):
        replace(HIGH, cpuset="0-31", agent_cpus="1.5")
    with pytest.raises(ValueError, match="list of cores"):
        replace(HIGH, cpuset="all")


async def test_each_running_agent_gets_cores_of_its_own(settings):
    """`nproc` then says what the agent may use. A slot is given back when its
    conversation stops; with none free, an agent runs unpinned (still limited)."""
    from datalab.db import connect
    from datalab.sessions.manager import SessionManager
    from datalab.sessions.store import ConversationStore
    from datalab.sessions.tokens import SessionTokens

    on_server = server_settings(settings, high=replace(HIGH, cpuset="0-31"))
    store = ConversationStore(connect(settings.data_dir / "test.sqlite"))
    manager = SessionManager(on_server, store, SessionTokens())

    def high_conversation():
        return store.create(kind="data", mode="analysis", title="t", model="m", compute="high")

    one, two, three = high_conversation(), high_conversation(), high_conversation()
    assert manager._containers(one, pin=True).cpuset == "0-15"
    assert manager._containers(one, pin=True).cpuset == "0-15"  # the same while it runs
    assert manager._containers(two, pin=True).cpuset == "16-31"
    assert manager._containers(three, pin=True).cpuset is None  # none free
    assert manager._containers(one).cpuset is None  # only an agent starting takes one
    await manager._shutdown(one.id)
    assert manager._containers(three, pin=True).cpuset == "0-15"
    standard = store.create(kind="data", mode="analysis", title="t", model="m")
    assert manager._containers(standard, pin=True).cpuset is None


@pytest.mark.parametrize("with_high", [True, False])
async def test_the_safety_check_runs_a_data_session_on_the_high_compute_machine(
    settings, tmp_path, monkeypatch, with_high
):
    """The same data-session checks, on the other machine's engine, in its
    shared folder, reported apart ("(high compute)"); none without it."""
    from datalab.safety import CheckResult, SafetyCheck
    from datalab.safety.canary import Canaries
    from datalab.sessions.tokens import SessionTokens

    started: list[tuple[str, SessionPaths]] = []

    class Recorded(SessionContainers):
        async def start(self, token):
            started.append((self.engine.env.get("DOCKER_HOST", "local"), self.paths))

        async def remove(self):
            return None

    high = replace(HIGH, sessions_dir=str(tmp_path / "compute-sessions")) if with_high else None
    check = SafetyCheck(
        server_settings(settings, high=high),
        SessionTokens(),
        Canaries(),
        model_key=lambda: "",
        containers=Recorded,
    )

    async def none(*_args, **_kwargs):
        return []

    async def one(*_args, **_kwargs):
        return CheckResult("x", "p", "One check", "pass", "this computer is unreachable")

    async def data_checks(probe):
        return [CheckResult("internet_blocked", "p", "A data session can't", "pass", "")]

    monkeypatch.setattr(check, "_database_checks", none)
    monkeypatch.setattr(check, "_research_session_checks", none)
    monkeypatch.setattr(check, "_data_session_checks", data_checks)
    for name in ("_image_check", "_browser_policy_check", "_preview_check"):
        monkeypatch.setattr(check, name, one)
    report = await check.run()
    ids = [r.id for r in report.results]
    if not with_high:
        assert "high_compute_internet_blocked" not in ids
        assert all(where == "local" for where, _ in started)
        return
    assert "internet_blocked" in ids and "high_compute_internet_blocked" in ids
    label = next(r.label for r in report.results if r.id == "high_compute_internet_blocked")
    assert label.endswith("(high compute)")
    where, paths = started[-1]
    assert where == HIGH.docker_host
    assert paths.root.parent == tmp_path / "compute-sessions" / "safety"


@pytest.mark.parametrize(
    ("cap_drop", "bounding", "sealed"),
    [
        (["ALL"], "0000000000000000", True),  # Docker
        (["CHOWN", "KILL", "SETUID"], "0000000000000000", True),  # Podman lists each one
        (["CHOWN"], "00000000a80425fb", False),  # capabilities left in
        (["ALL"], "00000000a80425fb", False),  # whatever the setting says
    ],
)
async def test_capabilities_are_checked_from_inside_the_container(
    settings, cap_drop, bounding, sealed
):
    from datalab.safety import SafetyCheck
    from datalab.safety.canary import Canaries
    from datalab.sessions.tokens import SessionTokens

    class Probe:
        containers = type("C", (), {"network": "net"})()

        async def inspect(self):
            return {
                "HostConfig": {"CapDrop": cap_drop, "SecurityOpt": ["no-new-privileges"]},
                "Config": {"User": "1000:1000"},
                "Mounts": [
                    {"Destination": d, "RW": d in ("/work", "/codex-home")}
                    for d in ("/work", "/codex-home", "/codex-home/config.toml")
                ],
                "NetworkSettings": {"Networks": {"net": {}}},
            }

        async def sh(self, command):
            if command.startswith("grep '^CapBnd:'"):
                return 0, f"CapBnd:\t{bounding}\n"
            return 1, ""  # the home folder isn't there; the config can't be changed

    check = SafetyCheck(settings, SessionTokens(), Canaries(), model_key=lambda: "")
    result = await check._locked_down(Probe(), kind="research")
    assert (result.status == "pass") is sealed, result.detail
    assert ("capabilities not dropped" in result.detail) is not sealed
