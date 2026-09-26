"""The Safety check must catch a broken sandbox, not just pass a good one.

Needs Docker and the probe image (`docker build -t datalab-probe:ci images/probe`).
Run with `uv run pytest -m docker`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from datalab.app import create_app
from datalab.safety import SafetyCheck
from datalab.sessions.containers import SessionContainers, docker
from tests.conftest import FakeDatabase, live_server

pytestmark = pytest.mark.docker

IMAGE = os.environ.get("DATALAB_TEST_AGENT_IMAGE", "datalab-probe:ci")


class LeakyContainers(SessionContainers):
    """What a misconfigured DataLab might do: give the agent a way out."""

    async def start(self, token: str) -> None:
        await super().start(token)
        await docker("network", "connect", "bridge", self.agent)


class LeakyGateway(SessionContainers):
    """A gateway that forwards everything to DataLab, not just /v1 and /mcp."""

    async def start(self, token: str) -> None:
        await super().start(token)
        conf = self.paths.gateway_conf.read_text().replace(
            "return 404;", f"proxy_pass http://host.docker.internal:{self.host_port};"
        )
        self.paths.gateway_conf.write_text(conf)
        await docker("exec", self.gateway, "nginx", "-s", "reload")
        await asyncio.sleep(0.5)


@pytest.fixture
def app(settings, catalog):
    have_image = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    if have_image.returncode != 0:
        pytest.skip(f"Build the probe image first: docker build -t {IMAGE} images/probe")
    settings = settings.__class__(**{**settings.__dict__, "agent_image": IMAGE, "oracle": None})
    return create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)


def _run(app, containers) -> dict[str, str]:
    services = app.state.services
    with live_server(app) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        settings = services.settings.__class__(**{**services.settings.__dict__, "port": port})
        check = SafetyCheck(
            settings,
            services.tokens,
            app.state.canaries,
            model_key=lambda: "sk-test-not-real",
            containers=containers,
        )
        report = asyncio.run(check.run())
    return {r.id: r.status for r in report.results}


def test_a_sealed_session_passes(app):
    results = _run(app, SessionContainers)
    failures = {k: v for k, v in results.items() if v == "fail" and k != "model_reachable"}
    assert failures == {}


def test_a_leaky_session_is_caught(app):
    results = _run(app, LeakyContainers)
    # A route out is detected: raw addresses become reachable, and the
    # container is on a network it shouldn't be on.
    assert results["internet_blocked"] == "fail"
    assert results["data_container_locked_down"] == "fail"
    # The separate DNS layer (--dns to an unroutable address) still holds, so
    # names don't resolve even with the route: the two layers are independent.
    assert results["outside_dns_blocked"] == "pass"


def test_a_leaky_gateway_is_caught_by_canaries(app):
    # Through the leaky gateway, requests reach DataLab and get a 401 or 404
    # back. Only the canaries can tell that they arrived.
    results = _run(app, LeakyGateway)
    assert results["gateway_routes_only"] == "fail"
