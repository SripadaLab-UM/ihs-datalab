import json

import httpx
import pytest
from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.relay.policy import Refused, check_responses_request
from datalab.sessions.tokens import SessionAccess
from tests.conftest import FakeDatabase

REAL_KEY = "sk-real-umgpt-key"


def codex_request(**extra) -> dict:
    """The shape of a request Codex 0.157 sends (trimmed)."""
    body = {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ],
        "tools": [
            {"type": "function", "name": "shell", "parameters": {}},
            {"type": "tool_search", "execution": "client"},
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "reasoning": {"effort": "high", "summary": "auto"},
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": "abc",
    }
    body.update(extra)
    return body


class TestPolicy:
    def test_codex_requests_are_allowed(self):
        check_responses_request(codex_request(), "data")

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"tools": [{"type": "web_search"}]}, "tool type"),
            ({"tools": [{"type": "mcp", "server_url": "https://example.org"}]}, "tool type"),
            ({"tools": [{"type": "code_interpreter"}]}, "tool type"),
            ({"tools": [{"type": "tool_search"}]}, "tool type"),  # server-side search
            ({"store": True}, "store"),
            ({"background": True}, "background"),
            ({"include": ["file_search_call.results"]}, "include"),
            ({"tool_choice": {"type": "web_search"}}, "tool_choice"),
            ({"input": [{"type": "item_reference", "id": "x"}]}, "stored items"),
        ],
    )
    def test_refused_in_data_sessions(self, change, reason):
        with pytest.raises(Refused, match=reason):
            check_responses_request(codex_request(**change), "data")

    def test_images_must_be_inline(self):
        link = {"type": "input_image", "image_url": "https://example.org/a.png"}
        inline = {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}
        message = {"type": "message", "role": "user"}
        check_responses_request(codex_request(input=[{**message, "content": [inline]}]), "data")
        with pytest.raises(Refused, match="inline"):
            check_responses_request(codex_request(input=[{**message, "content": [link]}]), "data")

    def test_tool_output_images_are_checked_too(self):
        link = {"type": "input_image", "image_url": "https://example.org/a.png"}
        item = {"type": "function_call_output", "call_id": "c", "output": [link]}
        with pytest.raises(Refused):
            check_responses_request(codex_request(input=[item]), "data")

    def test_research_sessions_may_use_hosted_web_search(self):
        check_responses_request(codex_request(tools=[{"type": "web_search"}]), "research")
        with pytest.raises(Refused):
            check_responses_request(codex_request(tools=[{"type": "mcp"}]), "research")


@pytest.fixture
def relay(settings, catalog, tmp_path):
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}]})
        return httpx.Response(
            200, content=b"event: done\ndata: {}\n\n", headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        model_client=client,
        model_key=lambda: REAL_KEY,
        manage_containers=False,
    )
    token = app.state.services.tokens.issue(
        SessionAccess(session_id="s1", kind="data", results_dir=tmp_path)
    )
    with TestClient(app) as test_client:
        yield test_client, token, seen


def test_models_are_forwarded_with_the_real_key(relay):
    client, token, seen = relay
    response = client.get("/relay/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert seen[0].headers["authorization"] == f"Bearer {REAL_KEY}"


def test_allowed_request_streams_back_and_uses_the_real_key(relay):
    client, token, seen = relay
    response = client.post(
        "/relay/v1/responses",
        headers={"Authorization": f"Bearer {token}"},
        content=json.dumps(codex_request()),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"event: done" in response.content
    assert seen[0].headers["authorization"] == f"Bearer {REAL_KEY}"
    assert token not in seen[0].headers["authorization"]


def test_refused_request_never_reaches_u_m(relay):
    client, token, seen = relay
    response = client.post(
        "/relay/v1/responses",
        headers={"Authorization": f"Bearer {token}"},
        content=json.dumps(codex_request(tools=[{"type": "web_search"}])),
    )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "datalab_refused"
    assert seen == []


def test_unknown_token_is_refused(relay):
    client, _, seen = relay
    for headers in ({}, {"Authorization": "Bearer nope"}):
        assert (
            client.post("/relay/v1/responses", headers=headers, json=codex_request()).status_code
            == 401
        )
    assert seen == []


@pytest.mark.parametrize(
    "path", ["/relay/v1/files", "/relay/v1/chat/completions", "/relay/v1/vector_stores"]
)
def test_other_endpoints_are_refused(relay, path):
    client, token, seen = relay
    response = client.post(path, headers={"Authorization": f"Bearer {token}"}, json={})
    assert response.status_code == 403
    assert seen == []
