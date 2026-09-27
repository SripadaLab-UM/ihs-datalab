"""The routers for areas still being built: each answers its status."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from tests.conftest import REQUIRE_DATA_FOLDER_LOCK, FakeDatabase


@pytest.fixture
def client(settings, catalog):
    app = create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("area", ["workflows", "pipelines", "settings"])
def test_areas_not_built_yet_say_so(client, area):
    client.get(client.app.state.browser.sign_in_path())  # type: ignore[attr-defined]
    assert client.get(f"/api/{area}/status").json() == {"available": False}


def test_knowledge_says_its_repo_isnt_configured(client):
    client.get(client.app.state.browser.sign_in_path())  # type: ignore[attr-defined]
    assert client.get("/api/knowledge/status").json() == {
        "available": False,
        "repo": "not configured",
    }


def test_the_status_routes_need_the_browser_session(client):
    assert client.get("/api/sql/status").status_code in (401, 403)


def test_the_areas_are_in_the_api_schema(client):
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    for area in ("sql", "knowledge", "workflows", "pipelines", "settings"):
        assert f"/api/{area}/status" in paths


def test_app_needs_the_data_folder_lock(settings, catalog, monkeypatch):
    from datalab import app as app_module
    from datalab import datalock

    # The real check, not the tests' stand-in (see conftest.py).
    monkeypatch.setattr(app_module, "require_data_folder_lock", REQUIRE_DATA_FOLDER_LOCK)
    with pytest.raises(app_module.DataFolderNotLocked):
        create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)
    datalock.refuse_second_instance(settings.data_dir, settings.profile)
    create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)
