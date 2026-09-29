"""api/docker.py: how Docker stands on Windows, and the fixes the page offers."""

from __future__ import annotations

import dataclasses
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.docker import build_docker_router
from datalab.app import create_app
from datalab.config import Settings, WindowsSettings, load_settings
from datalab.web import BrowserSession
from tests.conftest import FakeDatabase
from tests.test_web import signed_in_client

from .test_windows_vm import FakeWindows, doctor_for

JSON = {"content-type": "application/json"}


@pytest.fixture(autouse=True)
def installed(docker_app):
    """Docker Desktop is installed where its installer puts it."""
    return docker_app


def client_for(settings: Settings, fake: FakeWindows, platform: str = "win32") -> TestClient:
    app = FastAPI()
    app.include_router(build_docker_router(settings, doctor_for(fake, platform=platform)))
    return TestClient(app)


def test_the_page_learns_the_vm_is_refused_and_where_to_get_admin_access(settings):
    lab = dataclasses.replace(
        settings, windows=WindowsSettings(admin_access_url="https://admin.example.org/jit")
    )
    shown = client_for(lab, FakeWindows()).get("/api/docker").json()
    assert shown == {
        "state": "vm-refused",
        "fixing": False,
        "phase": None,
        "admin_access_url": "https://admin.example.org/jit",
    }


def test_off_windows_nothing_is_offered(settings):
    fake = FakeWindows()
    client = client_for(settings, fake, platform="darwin")
    assert client.get("/api/docker").json()["state"] == "unsupported"
    assert client.post("/api/docker/fix").json()["outcome"] == "unsupported"
    assert fake.calls == []


def test_fix_answers_once_docker_is_back_with_how_it_stands_then(settings):
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    fixed = client_for(settings, fake).post("/api/docker/fix").json()
    assert fixed == {"outcome": "fixed", "state": "ready", "failed_step": None}
    fake = FakeWindows(grant=1)
    assert client_for(settings, fake).post("/api/docker/fix").json() == {
        "outcome": "declined",
        "state": "vm-refused",
        "failed_step": None,
    }


def test_fix_says_which_step_of_the_restart_didnt_work(settings):
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=False)
    assert client_for(settings, fake).post("/api/docker/fix").json() == {
        "outcome": "restart-failed",
        "state": "starting",
        "failed_step": "ready",
    }


def test_fix_never_asks_windows_while_docker_works(settings):
    fake = FakeWindows(docker=True)
    assert client_for(settings, fake).post("/api/docker/fix").json() == {
        "outcome": "not-needed",
        "state": "ready",
        "failed_step": None,
    }
    assert fake.ran("powershell.exe") == []


def test_fix_waits_while_something_in_datalab_is_working(settings):
    """The restart stops everything in Docker, a conversation's sandbox among
    them: so no administrator prompt while a turn, query or run is going."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    app = FastAPI()
    app.include_router(build_docker_router(settings, doctor_for(fake), working=lambda: True))
    assert TestClient(app).post("/api/docker/fix").json()["outcome"] == "working"
    assert fake.ran("powershell.exe") == [] and fake.killed() == []


def test_start_opens_a_closed_docker_desktop(settings, installed):
    fake = FakeWindows(wsl=(0, b""), desktop_open=False)
    client = client_for(settings, fake)
    assert client.get("/api/docker").json()["state"] == "stopped"
    assert fake.ran("wsl.exe") == []  # a closed Docker Desktop is never probed
    assert client.post("/api/docker/start").json()["state"] == "starting"
    assert fake.opened == [[str(installed)]]


def test_check_again_is_a_post_and_at_most_every_few_seconds(settings):
    fake = FakeWindows()
    client = client_for(settings, fake)
    client.get("/api/docker")
    assert client.get("/api/docker?fresh=true").json()["state"] == "vm-refused"
    assert len(fake.ran("wsl.exe")) == 1  # a GET answers from the last check
    client.post("/api/docker/check")
    client.post("/api/docker/check")
    assert len(fake.ran("wsl.exe")) == 1  # within MIN_CHECK_SECONDS of the last one


def test_another_lab_can_name_its_own_admin_page_or_none(settings, tmp_path, monkeypatch, caplog):
    other = dataclasses.replace(settings, windows=WindowsSettings(admin_access_url=None))
    assert client_for(other, FakeWindows()).get("/api/docker").json()["admin_access_url"] is None
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text(
        '[windows]\nadmin_access_url = "https://admin.example.org/jit"\n', encoding="utf-8"
    )
    assert load_settings("practice").windows.admin_access_url == "https://admin.example.org/jit"
    # Only a link: a bad one is left out, with a warning, and DataLab still starts.
    (tmp_path / "settings.toml").write_text(
        '[windows]\nadmin_access_url = "javascript:alert(1)"\n', encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        assert load_settings("practice").windows.admin_access_url is None
    assert "isn't an https:// address" in caplog.text


def test_only_datalabs_own_signed_in_page_can_check_start_or_fix(settings, catalog, tmp_path):
    """The routes sit behind the sign-in cookie and ApiProtection like every
    other: no other page on this computer can show a Windows administrator
    prompt, open Docker Desktop, or make DataLab start WSL's VM."""
    fake = FakeWindows(after_grant=(0, b""), ready_after_open=True)
    browser = BrowserSession(settings.port)
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        browser=browser,
        docker_doctor=doctor_for(fake),
    )
    routes = ("/api/docker/check", "/api/docker/start", "/api/docker/fix")
    signed_out = TestClient(app, base_url="http://127.0.0.1:8766")
    assert signed_out.get("/api/docker").status_code == 401
    for route in routes:
        assert signed_out.post(route, json={}).status_code == 401
    with signed_in_client(app, browser) as client:
        for route in routes:
            for headers in (
                {"content-type": "text/plain"},  # a form post from another page
                {**JSON, "sec-fetch-site": "same-site"},  # another port of 127.0.0.1
                {**JSON, "origin": "http://127.0.0.1:9999"},
            ):
                refused = client.post(route, content="{}", headers=headers)
                assert refused.status_code == 403, (route, headers)
        assert fake.calls == []  # nothing ran: not docker, not wsl, not PowerShell
        # DataLab's own page, as its request() sends it.
        assert client.post("/api/docker/fix", content="{}", headers=JSON).json() == {
            "outcome": "fixed",
            "state": "ready",
            "failed_step": None,
        }
        assert len(fake.ran("powershell.exe")) == 1
