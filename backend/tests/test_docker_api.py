"""api/docker.py: how Docker stands on Windows, and the fixes the page offers."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.docker import build_docker_router
from datalab.config import Settings, WindowsSettings, load_settings

from .test_windows_vm import FakeWindows, doctor_for


def client_for(settings: Settings, fake: FakeWindows, platform: str = "win32") -> TestClient:
    app = FastAPI()
    app.include_router(build_docker_router(settings, doctor_for(fake, platform=platform)))
    return TestClient(app)


def test_the_page_learns_the_vm_is_refused_and_where_to_get_admin_access(settings):
    shown = client_for(settings, FakeWindows()).get("/api/docker").json()
    assert shown == {
        "state": "vm-refused",
        "fixing": False,
        "admin_access_url": "https://profile.med.umich.edu",
    }


def test_off_windows_nothing_is_offered(settings):
    fake = FakeWindows()
    client = client_for(settings, fake, platform="darwin")
    assert client.get("/api/docker").json()["state"] == "unsupported"
    assert client.post("/api/docker/fix").json()["outcome"] == "unsupported"
    assert fake.calls == []


def test_fix_answers_once_the_prompt_is_answered_with_how_docker_stands_then(settings):
    fake = FakeWindows(after_grant=(0, b""))
    fixed = client_for(settings, fake).post("/api/docker/fix").json()
    assert fixed == {"outcome": "fixed", "state": "starting"}
    fake = FakeWindows(grant=1)
    assert client_for(settings, fake).post("/api/docker/fix").json() == {
        "outcome": "declined",
        "state": "vm-refused",
    }


def test_start_opens_a_closed_docker_desktop(settings, monkeypatch, tmp_path):
    from datalab import windows_vm

    app = tmp_path / "Docker Desktop.exe"
    app.write_bytes(b"")
    monkeypatch.setattr(windows_vm, "docker_desktop", lambda: app)
    fake = FakeWindows(wsl=(0, b""), desktop_open=False)
    client = client_for(settings, fake)
    assert client.get("/api/docker").json()["state"] == "stopped"
    assert client.post("/api/docker/start").json()["state"] == "starting"
    assert fake.opened == [[str(app)]]


def test_another_lab_can_name_its_own_admin_page_or_none(settings, tmp_path, monkeypatch):
    other = dataclasses.replace(settings, windows=WindowsSettings(admin_access_url=None))
    assert client_for(other, FakeWindows()).get("/api/docker").json()["admin_access_url"] is None
    monkeypatch.setenv("DATALAB_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text(
        '[windows]\nadmin_access_url = "https://admin.example.org/jit"\n', encoding="utf-8"
    )
    assert load_settings("practice").windows.admin_access_url == "https://admin.example.org/jit"
    (tmp_path / "settings.toml").write_text(
        '[windows]\nadmin_access_url = "javascript:alert(1)"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="https://"):
        load_settings("practice")
