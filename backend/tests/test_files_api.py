"""Outputs, previews, and checkpoints over the API."""

import asyncio

from fastapi.testclient import TestClient

from datalab.app import create_app
from datalab.htmlclean import clean_html
from datalab.sessions.containers import DockerError
from tests.conftest import FakeDatabase
from tests.test_conversations import use_fake_runtime, wait_for

FRAME = {"sec-fetch-dest": "iframe"}


def make_app(settings, catalog):
    return create_app(
        settings,
        database=FakeDatabase(),
        catalog=catalog,
        manage_containers=False,
        protect_api=False,
    )


def new_conversation(client):
    return client.post("/api/conversations", json={}).json()["id"]


def workspace(app, cid):
    work = app.state.services.sessions.paths(cid).work
    (work / "outputs").mkdir(parents=True, exist_ok=True)
    return work


def checkpoint(app, cid):
    """What happens after every turn: the browser sees files as of this."""
    app.state.services.sessions.checkpoints(cid).take("After turn 1", turn=1)


class FakeContainers:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.stopped = 0

    async def stop_and_confirm(self):
        self.stopped += 1
        if self.fail:
            raise DockerError("DataLab couldn't stop the agent's container.")


def test_outputs_come_from_the_latest_checkpoint(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = new_conversation(client)
        outputs = workspace(app, cid) / "outputs"
        (outputs / "figures").mkdir()
        (outputs / "summary.csv").write_text("a,b\n1,2\n")
        (outputs / "figures" / "plot.png").write_bytes(b"\x89PNG fake")
        (outputs / "report.html").write_text("<p>hi</p>")
        assert client.get(f"/api/conversations/{cid}/files").json() == []  # not yet saved
        checkpoint(app, cid)
        (outputs / "summary.csv").write_text("changed mid-turn")  # live changes aren't shown

        listed = client.get(f"/api/conversations/{cid}/files").json()
        assert [(f["path"], f["kind"]) for f in listed] == [
            ("figures/plot.png", "image"),
            ("report.html", "html"),
            ("summary.csv", "csv"),
        ]
        csv = client.get(f"/api/conversations/{cid}/files/outputs/summary.csv")
        assert csv.text == "a,b\n1,2\n"
        assert csv.headers["content-type"].startswith("text/plain")
        assert csv.headers["content-security-policy"].startswith("sandbox")
        # Only one policy: the stricter one, not the app's as well.
        assert "connect-src 'self'" not in csv.headers["content-security-policy"]
        html = client.get(f"/api/conversations/{cid}/files/outputs/report.html")
        assert html.headers["content-type"].startswith("text/plain")
        head = client.get(f"/api/conversations/{cid}/files/outputs/summary.csv", params={"head": 3})
        assert head.text == "a,b" and head.headers["x-datalab-truncated"] == "1"
        work = client.get(f"/api/conversations/{cid}/files", params={"root": "work"}).json()
        assert "outputs/summary.csv" in [f["path"] for f in work]


def test_a_linked_outputs_folder_shows_nothing_from_the_host(settings, catalog, tmp_path):
    app = make_app(settings, catalog)
    host = tmp_path / "host"
    host.mkdir()
    (host / "secret.txt").write_text("host file")
    with TestClient(app) as client:
        cid = new_conversation(client)
        work = workspace(app, cid)
        (work / "outputs").rmdir()
        (work / "outputs").symlink_to(host)
        checkpoint(app, cid)
        assert client.get(f"/api/conversations/{cid}/files").json() == []
        response = client.get(f"/api/conversations/{cid}/files/outputs/secret.txt")
        assert response.status_code == 404
        for bad in ("../secret.txt", "..%2Fsecret.txt"):
            refused = client.get(f"/api/conversations/{cid}/files/outputs/{bad}")
            assert refused.status_code in (404, 422)


def test_query_results_are_served_directly(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = new_conversation(client)
        results = app.state.services.sessions.paths(cid).oracle_results
        results.mkdir(parents=True)
        (results / "q_1.csv").write_text("x\n1\n")
        listed = client.get(f"/api/conversations/{cid}/files", params={"root": "results"})
        assert [f["path"] for f in listed.json()] == ["q_1.csv"]
        assert client.get(f"/api/conversations/{cid}/files/results/q_1.csv").text == "x\n1\n"


def test_html_preview_is_cleaned_sandboxed_and_frame_only(settings, catalog):
    app = make_app(settings, catalog)
    with TestClient(app) as client:
        cid = new_conversation(client)
        outputs = workspace(app, cid) / "outputs"
        (outputs / "my report.html").write_text(
            "<link rel=dns-prefetch href='//leak.example'><h1>Report</h1>"
            "<img src='plot.png'><script>fetch('/api')</script>"
        )
        (outputs / "plot.png").write_bytes(b"\x89PNG")
        (outputs / "notes.txt").write_text("not for the preview")
        checkpoint(app, cid)

        url = client.post(
            f"/api/conversations/{cid}/previews",
            json={"root": "outputs", "path": "my report.html"},
        ).json()["url"]
        assert "my%20report.html" in url
        page = client.get(url, headers=FRAME)
        policy = page.headers["content-security-policy"]
        assert "<h1>Report</h1>" in page.text
        assert "leak.example" not in page.text and "<script" not in page.text
        assert policy.split(";")[0].strip() == "sandbox"  # no allow-scripts
        assert "script-src" not in policy and "connect-src" not in policy
        assert "default-src 'none'" in policy and "'self'" not in policy
        assert page.headers["x-dns-prefetch-control"] == "off"
        # Opened as a page of its own, it isn't served at all.
        assert client.get(url, headers={"sec-fetch-dest": "document"}).status_code == 404
        base = url.rsplit("/", 1)[0]
        image = client.get(f"{base}/plot.png", headers={"sec-fetch-dest": "image"})
        assert image.status_code == 200
        assert client.get(f"{base}/notes.txt").status_code == 404
        assert client.get("/preview/guess/report.html").status_code == 404


def test_cleaning_keeps_a_report_and_drops_what_could_leak():
    cleaned = clean_html(
        "<!-- note --><html><head><meta http-equiv=refresh content='0;url=https://x'>"
        "<link rel=stylesheet href=style.css><link rel=PreConnect href=https://x>"
        "<base href=https://x><style>p{color:red}</style></head>"
        "<body onload=go()><p class=a>1 &lt; 2</p><svg viewBox='0 0 1 1'><style>"
        "<img src=x></style></svg><iframe src=https://x></iframe>"
        "<a href='https://x' ping='https://y'>link</a></body></html>"
    )
    assert '<link rel="stylesheet" href="style.css">' in cleaned
    assert "preconnect" not in cleaned.lower() and "<base" not in cleaned
    assert "refresh" not in cleaned and "onload" not in cleaned and "ping" not in cleaned
    assert "<iframe" not in cleaned and "note" not in cleaned
    assert '<p class="a">1 &lt; 2</p>' in cleaned
    assert "<img" not in cleaned  # the svg style's text can't become a tag
    assert "<span>link</span>" in cleaned and "https://x" not in cleaned  # links are text


def test_cleaning_keeps_the_first_of_duplicate_attributes_like_a_browser():
    cleaned = clean_html(
        '<link rel=preconnect rel=stylesheet href="https://data.leak.example/">'
        '<link rel="dns-prefetch" rel="stylesheet" href="//leak.example">'
        "<img src=a.png src=https://x/y.png alt=x>"
    )
    assert "leak.example" not in cleaned and "preconnect" not in cleaned
    assert '<img src="a.png" alt="x">' in cleaned


def test_cleaning_drops_svg_animation_and_outside_links_in_svg():
    cleaned = clean_html(
        "<svg><a href='#top'><text>ok</text></a>"
        "<a xlink:href='https://x/?d=1'><set attributename=href to='https://x'/>"
        "<animate attributeName=href values='https://x'/></a>"
        "<image href='plot.png'/><use href='https://x/s.svg#a'/></svg>"
    )
    assert "https://x" not in cleaned
    assert "<set" not in cleaned and "<animate" not in cleaned
    assert '<image href="plot.png"/>' in cleaned  # self-closing stays self-closing


def test_cleaning_keeps_svg_siblings_apart():
    # Written as <rect> (unclosed), everything after it would sit inside the rect
    # and never be drawn.
    cleaned = clean_html('<svg><rect fill="white"/><g><path d="M0,0"/></g></svg>')
    assert '<rect fill="white"/><g><path d="M0,0"/></g>' in cleaned


def test_turns_are_checkpointed_and_restorable(settings, catalog):
    app = make_app(settings, catalog)
    made = use_fake_runtime(app)
    manager = app.state.services.sessions
    containers = FakeContainers()
    manager._containers = lambda conversation: containers
    with TestClient(app) as client:
        cid = new_conversation(client)
        work = workspace(app, cid)
        (work / "notes.md").write_text("first version")
        client.post(f"/api/conversations/{cid}/messages", json={"text": "one"})
        wait_for(client, cid, "checkpoint")
        (work / "notes.md").write_text("second version")

        [first] = client.get(f"/api/conversations/{cid}/checkpoints").json()
        assert (first["label"], first["turn"], first["files"]) == ("After turn 1", 1, 1)
        restored = client.post(f"/api/conversations/{cid}/checkpoints/1/restore")
        assert restored.status_code == 200
        assert (work / "notes.md").read_text() == "first version"
        assert containers.stopped == 1  # stopped (and confirmed gone) first

        # The current files were saved first, so the restore can be undone;
        # and the browser now sees the restored files.
        labels = [c["label"] for c in client.get(f"/api/conversations/{cid}/checkpoints").json()]
        assert labels == [
            "Restored to after turn 1",
            "Before restoring to after turn 1",
            "After turn 1",
        ]
        shown = client.get(f"/api/conversations/{cid}/files", params={"root": "work"}).json()
        assert [f["path"] for f in shown] == ["notes.md"]

        # The agent hears about it on its next turn.
        client.post(f"/api/conversations/{cid}/messages", json={"text": "two"})
        wait_for(client, cid, "turn_finished")
    assert made[-1].sent[-1].startswith("[DataLab: the user restored the files")
    assert made[-1].sent[-1].endswith("two")


def test_restore_refuses_if_the_container_wont_stop(settings, catalog):
    app = make_app(settings, catalog)
    manager = app.state.services.sessions
    manager._containers = lambda conversation: FakeContainers(fail=True)
    with TestClient(app) as client:
        cid = new_conversation(client)
        work = workspace(app, cid)
        (work / "notes.md").write_text("first")
        checkpoint(app, cid)
        (work / "notes.md").write_text("second")
        assert client.post(f"/api/conversations/{cid}/checkpoints/1/restore").status_code == 503
        assert (work / "notes.md").read_text() == "second"


def test_restore_waits_for_the_agent(settings, catalog):
    app = make_app(settings, catalog)
    hold = asyncio.Event()
    use_fake_runtime(app, hold=hold)
    with TestClient(app) as client:
        cid = new_conversation(client)
        checkpoint(app, cid)
        client.post(f"/api/conversations/{cid}/messages", json={"text": "busy"})
        wait_for(client, cid, "answer_delta")
        assert client.post(f"/api/conversations/{cid}/checkpoints/1/restore").status_code == 409
        client.post(f"/api/conversations/{cid}/stop")
        wait_for(client, cid, "checkpoint")
        assert client.post(f"/api/conversations/{cid}/checkpoints/99/restore").status_code == 404


async def test_a_turn_being_set_up_counts_as_busy(settings, catalog):
    app = make_app(settings, catalog)
    use_fake_runtime(app)
    manager = app.state.services.sessions
    store = app.state.services.conversations
    conversation = store.create(kind="data", mode="analysis", title="t", model="m")
    room = asyncio.Event()

    async def slow_make_room(keep):  # e.g. stopping another conversation's container
        await room.wait()

    manager._make_room = slow_make_room
    sending = asyncio.create_task(manager.send(conversation, "hi", None))
    await asyncio.sleep(0.01)
    assert manager.is_busy(conversation.id)  # a restore arriving now is refused
    room.set()
    await sending
