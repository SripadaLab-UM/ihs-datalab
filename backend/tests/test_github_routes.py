"""The GitHub sign-in routes: one sign-in for both lab repos, whichever is set up."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datalab.api.github import GitHubServices, build_github_router
from datalab.config import RepoSettings, Settings
from datalab.repos.github import Account, GitHubAuth, Tokens, TokenStore

ME = Account("yfang", 42, "Yu Fang")


def answer(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login/device/code":
        return httpx.Response(
            200,
            json={"device_code": "d", "user_code": "WXYZ-0000", "expires_in": 900,
                  "interval": 5, "verification_uri": "https://github.com/login/device"},
        )  # fmt: skip
    return httpx.Response(404)


def client_for(tmp_path, profile: str = "real", **repos: str) -> TestClient:
    settings = Settings(
        profile=profile,  # type: ignore[arg-type]
        data_dir=tmp_path / "data",
        oracle=None,
        repos=RepoSettings(client_id="Iv23liTESTCLIENT", **repos),
    )
    auth = GitHubAuth("Iv23liTESTCLIENT", http=httpx.Client(transport=httpx.MockTransport(answer)))
    app = FastAPI()
    app.include_router(build_github_router(GitHubServices(settings, auth)))
    return TestClient(app)


@pytest.fixture
def signed_in(github_keychain):
    TokenStore().save(Tokens("ghu_x", time.time() + 3600, "ghr_x", time.time() + 1e7, ME))


def test_only_the_pipelines_repo_set_up_can_still_sign_in(tmp_path, github_keychain):
    with client_for(tmp_path, pipelines="SripadaLab-UM/ihs-pipelines") as client:
        status = client.get("/api/github/status").json()
        assert (status["available"], status["signed_in"]) == (True, False)
        assert status["repos"] == [{"area": "pipelines", "name": "SripadaLab-UM/ihs-pipelines"}]
        started = client.post("/api/github/sign-in").json()
        assert (started["state"], started["user_code"]) == ("waiting", "WXYZ-0000")
        assert client.post("/api/github/sign-in/poll").json()["state"] == "waiting"
        assert client.post("/api/github/sign-in/cancel").json()["state"] == "signed out"


def test_status_says_who_is_signed_in_and_for_which_repos(tmp_path, signed_in):
    with client_for(
        tmp_path, knowledge="SripadaLab-UM/ihs-knowledge", pipelines="SripadaLab-UM/ihs-pipelines"
    ) as client:
        status = client.get("/api/github/status").json()
        assert status["signed_in"] and status["account"] == {"login": "yfang", "name": "Yu Fang"}
        assert [r["area"] for r in status["repos"]] == ["knowledge", "pipelines"]
        assert client.post("/api/github/sign-out").json()["state"] == "signed out"
        assert client.get("/api/github/status").json()["signed_in"] is False


@pytest.mark.parametrize(
    ("profile", "repos", "why"),
    [
        ("practice", {"pipelines": "SripadaLab-UM/ihs-pipelines"}, "Practice"),
        ("real", {}, "Neither of the lab's repositories"),
    ],
)
def test_no_sign_in_without_a_repo_or_in_practice(tmp_path, github_keychain, profile, repos, why):
    with client_for(tmp_path, profile, **repos) as client:
        status = client.get("/api/github/status").json()
        assert status["available"] is False and why in status["message"]
        assert client.post("/api/github/sign-in").status_code == 409
