from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.web import BrowserSession
from tests.conftest import FakeDatabase

JSON = {"content-type": "application/json"}


def make(settings, catalog, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>DataLab</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "apple-touch-icon-practice.png").write_bytes(b"png")
    (tmp_path / "secret.txt").write_text("not for the browser")
    browser = BrowserSession(settings.port)
    app = create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        browser=browser,
        web_dist=dist,
    )
    return app, browser


def test_api_needs_the_sign_in_cookie(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/conversations").status_code == 401
        assert client.get("/api/plan-schema").status_code == 401
        assert client.get("/api/health").status_code == 200  # the launcher's readiness check
        signed_in = client.get(browser.sign_in_path(), follow_redirects=False)
        assert signed_in.status_code == 303
        cookie = signed_in.cookies[browser.cookie_name]
        assert "httponly" in signed_in.headers["set-cookie"].lower()
        assert "samesite=strict" in signed_in.headers["set-cookie"].lower()
        client.cookies.set(browser.cookie_name, cookie)
        assert client.get("/api/conversations").status_code == 200


def test_each_port_has_its_own_cookie(settings, catalog, tmp_path):
    """Browsers share cookies between ports of one host: two DataLabs on one
    computer mustn't sign each other's windows out, or accept each other's."""
    app, browser = make(settings, catalog, tmp_path)
    assert browser.cookie_name == f"datalab_session_{settings.port}"
    other = BrowserSession(settings.port + 1)
    assert other.cookie_name != browser.cookie_name
    with TestClient(app) as client:
        signed_in = client.get(browser.sign_in_path(), follow_redirects=False)
        assert signed_in.headers["set-cookie"].startswith(f"{browser.cookie_name}=")
        value = signed_in.cookies[browser.cookie_name]
        client.cookies.clear()
        # The right value under another instance's name doesn't sign in here.
        client.cookies.set(other.cookie_name, value)
        assert client.get("/api/conversations").status_code == 401


def test_sign_in_link_works_once(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    link = browser.sign_in_path()
    with TestClient(app) as client:
        client.get(link, follow_redirects=False)
        again = client.get(link, follow_redirects=False)
    assert again.headers["location"] == "/signed-out"


def test_wrong_cookie_is_refused(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        client.cookies.set(browser.cookie_name, "guess")
        assert client.get("/api/conversations").status_code == 401


def test_every_response_has_the_security_policy(settings, catalog, tmp_path):
    app, _ = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        for path in ("/", "/api/health", "/api/conversations"):
            policy = client.get(path).headers["content-security-policy"]
            assert "connect-src 'self'" in policy
            assert "img-src 'self' data: blob:" in policy


def test_web_ui_serves_files_and_falls_back_to_index(settings, catalog, tmp_path):
    app, _ = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        assert client.get("/assets/app.js").text == "console.log(1)"
        page = client.get("/workspace/c_123")
        assert page.text == "<html>DataLab</html>"
        # The page always revalidates, so an update shows on the next load.
        assert page.headers["cache-control"] == "no-cache"
        assert client.get("/index.html").headers["cache-control"] == "no-cache"
        # Outside the built folder: never served.
        assert "not for the browser" not in client.get("/../secret.txt").text
        assert "not for the browser" not in client.get("/assets/%2e%2e/%2e%2e/secret.txt").text


def test_the_tabs_icons_always_revalidate(settings, catalog, tmp_path):
    # They keep their names from version to version (the page adds ?v=<hash>),
    # so a browser checks them again rather than keep an old icon.
    app, _ = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        icon = client.get("/favicon.svg?v=0123abcd")
        assert icon.text == "<svg/>" and icon.headers["cache-control"] == "no-cache"
        touch = client.get("/apple-touch-icon-practice.png")
        assert touch.headers["cache-control"] == "no-cache"
        # The hashed assets can be cached as the browser likes.
        assert "cache-control" not in client.get("/assets/app.js").headers


def test_end_session_signs_every_window_out_until_a_new_sign_in(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    link = browser.sign_in_path()
    with TestClient(app) as client:
        # Only a signed-in window can end it.
        assert client.post("/api/session/end", headers=JSON).status_code == 401
        signed_in = client.get(link, follow_redirects=False)
        cookie = signed_in.cookies[browser.cookie_name]
        client.cookies.set(browser.cookie_name, cookie)
        assert client.get("/api/conversations").status_code == 200
        ended = client.post("/api/session/end", headers=JSON)
        assert ended.status_code == 204
        # The browser is told to forget the cookie...
        forget = ended.headers["set-cookie"].lower()
        assert forget.startswith(f"{browser.cookie_name}=") and "max-age=0" in forget
        # ...and a copy kept anywhere (another window, another tab) no longer works.
        client.cookies.clear()
        client.cookies.set(browser.cookie_name, cookie)
        assert client.get("/api/conversations").status_code == 401
        # The used link doesn't sign in again: it takes DataLab's next one.
        assert client.get(link, follow_redirects=False).headers["location"] == "/signed-out"


def signed_in_client(app, browser) -> TestClient:
    client = TestClient(app, base_url="http://127.0.0.1:8766")
    signed_in = client.get(browser.sign_in_path(), follow_redirects=False)
    client.cookies.set(browser.cookie_name, signed_in.cookies[browser.cookie_name])
    return client


def test_another_local_page_cant_end_the_session(settings, catalog, tmp_path):
    """Every port of 127.0.0.1 is one site to a browser, so a page on another
    port gets the SameSite=Strict cookie sent. Its simple (text/plain) POST is
    refused, and the session carries on."""
    app, browser = make(settings, catalog, tmp_path)
    with signed_in_client(app, browser) as client:
        refused = client.post(
            "/api/session/end",
            content="x",
            headers={
                "content-type": "text/plain",
                "origin": "http://127.0.0.1:9999",
                "sec-fetch-site": "same-site",
            },
        )
        assert refused.status_code == 403
        assert client.get("/api/conversations").status_code == 200
        assert browser.generation == 0


def test_cross_origin_and_non_json_requests_are_refused(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    with signed_in_client(app, browser) as client:
        for headers in (
            # Another origin, whatever it says about the fetch.
            {**JSON, "origin": "http://127.0.0.1:9999"},
            {**JSON, "origin": "http://localhost:8766"},
            {**JSON, "origin": "null"},
            {**JSON, "origin": "http://127.0.0.1:8766", "sec-fetch-site": "cross-site"},
            {**JSON, "sec-fetch-site": "same-site"},
            # This origin, but not JSON: a form post.
            {
                "content-type": "application/x-www-form-urlencoded",
                "origin": "http://127.0.0.1:8766",
            },
            {"content-type": "multipart/form-data; boundary=x", "sec-fetch-site": "same-origin"},
            {},
        ):
            refused = client.post("/api/session/end", headers=headers)
            assert refused.status_code == 403, headers
        # Other state-changing methods are checked the same way.
        assert client.put("/api/settings/connections/model-key", content="{}").status_code == 403
        assert client.delete("/api/export-destinations/x").status_code == 403
        assert client.get("/api/conversations").status_code == 200
        assert browser.generation == 0


def test_the_pages_own_json_requests_pass(settings, catalog, tmp_path):
    """From DataLab's own page (same-origin), and from older browsers or tests
    that send no Sec-Fetch-Site, with this origin or none."""
    app, browser = make(settings, catalog, tmp_path)
    with signed_in_client(app, browser) as client:
        for headers in (
            {**JSON, "origin": "http://127.0.0.1:8766", "sec-fetch-site": "same-origin"},
            {"content-type": "application/json; charset=utf-8", "origin": "http://127.0.0.1:8766"},
            JSON,
        ):
            made = client.post("/api/conversations", json={}, headers=headers)
            assert made.status_code == 201, (headers, made.text)
        assert client.post("/api/session/end", headers=JSON).status_code == 204


def test_streams_learn_the_session_ended(settings):
    """What the live-update streams (conversations, workflow runs) check."""
    from types import SimpleNamespace

    from datalab.web import ended_since

    browser = BrowserSession(settings.port)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(browser=browser)))
    ended = ended_since(request)  # type: ignore[arg-type]
    assert not ended()
    browser.end()
    assert ended()
    # A stream opened after the end follows the new session.
    assert not ended_since(request)()  # type: ignore[arg-type]
    # No browser session (a router tested on its own): never ended.
    assert not ended_since(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))()  # type: ignore[arg-type]


def test_session_activity_says_what_is_still_going(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    with signed_in_client(app, browser) as client:
        assert client.get("/api/session/activity").json() == {
            "agent_turn": False,
            "workflow_run": False,
        }
        app.state.services.sessions._starting.add("c1")
        assert client.get("/api/session/activity").json()["agent_turn"] is True
