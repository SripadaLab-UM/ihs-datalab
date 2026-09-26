"""The Safety check's preview row, without Docker: a hostile page through the real route."""

from __future__ import annotations

import asyncio

import pytest

from datalab.app import create_app
from datalab.safety import SafetyCheck, policy
from tests.conftest import FakeDatabase, live_server


@pytest.fixture
def app(settings, catalog):
    return create_app(settings, database=FakeDatabase(), catalog=catalog, manage_containers=False)


def _check(app):
    services = app.state.services
    with live_server(app) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        settings = services.settings.__class__(**{**services.settings.__dict__, "port": port})
        check = SafetyCheck(
            settings,
            services.tokens,
            app.state.canaries,
            model_key=lambda: "sk-test-not-real",
            previews=app.state.previews,
        )
        return asyncio.run(check._preview_check())


def test_a_hostile_page_is_cleaned_and_sandboxed(app, settings):
    result = _check(app)
    assert result.status == "pass", result.detail
    assert not list((settings.data_dir / "safety").iterdir())  # cleaned up


def test_an_uncleaned_page_is_caught(app, monkeypatch):
    monkeypatch.setattr("datalab.api.files.clean_html", lambda html: html)
    result = _check(app)
    assert result.status == "fail"
    assert "<script" in result.detail and "outside its folder" in result.detail


def test_preview_policies_are_judged():
    folder = "http://127.0.0.1:1/preview/Tok/"
    good = f"sandbox; default-src 'none'; img-src {folder} data:; style-src {folder} 'unsafe-inline'; form-action 'none'; base-uri 'none'; frame-ancestors http://127.0.0.1:1"  # noqa: E501
    assert policy.preview_problems(good, folder) == []
    assert "the page isn't sandboxed" in policy.preview_problems(
        good.replace("sandbox; ", ""), folder
    )
    loose = good.replace("sandbox", "sandbox allow-scripts").replace("data:;", "data: https:;")
    assert policy.preview_problems(loose, folder) == [
        "the sandbox allows allow-scripts",
        "img-src allows https:",
    ]
