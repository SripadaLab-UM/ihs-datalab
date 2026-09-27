from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.web import COOKIE, BrowserSession
from tests.conftest import FakeDatabase


def make(settings, catalog, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>DataLab</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("not for the browser")
    browser = BrowserSession()
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
        assert client.get("/api/health").status_code == 200  # the launcher's readiness check
        signed_in = client.get(browser.sign_in_path(), follow_redirects=False)
        assert signed_in.status_code == 303
        cookie = signed_in.cookies[COOKIE]
        assert "httponly" in signed_in.headers["set-cookie"].lower()
        assert "samesite=strict" in signed_in.headers["set-cookie"].lower()
        client.cookies.set(COOKIE, cookie)
        assert client.get("/api/conversations").status_code == 200


def test_sign_in_link_works_once(settings, catalog, tmp_path):
    app, browser = make(settings, catalog, tmp_path)
    link = browser.sign_in_path()
    with TestClient(app) as client:
        client.get(link, follow_redirects=False)
        again = client.get(link, follow_redirects=False)
    assert again.headers["location"] == "/signed-out"


def test_wrong_cookie_is_refused(settings, catalog, tmp_path):
    app, _ = make(settings, catalog, tmp_path)
    with TestClient(app) as client:
        client.cookies.set(COOKIE, "guess")
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
