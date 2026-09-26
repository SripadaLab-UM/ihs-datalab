import json

import pytest

from datalab.exports import (
    MANIFEST,
    REPORT_POLICY,
    ExportError,
    ExportSource,
    export,
    report_document,
    safe_name,
)
from datalab.sessions.checkpoints import UnsafePath, open_workspace_file


@pytest.fixture
def outputs(tmp_path):
    folder = tmp_path / "work" / "outputs"
    (folder / "figures").mkdir(parents=True)
    (folder / "table.csv").write_text("a,b\n1,2\n")
    (folder / "figures" / "plot.png").write_bytes(b"png")
    return folder


def source(folder, path):
    return ExportSource(lambda: open_workspace_file(folder, path), path, f"/work/outputs/{path}")


def test_export_makes_a_dated_folder_with_a_manifest(tmp_path, outputs):
    destination = tmp_path / "Dropbox"
    destination.mkdir()
    result = export(
        destination,
        title="Sleep: interns/2025?",
        sources=[source(outputs, "table.csv"), source(outputs, "figures/plot.png")],
        about={"conversation": {"id": "c1", "kind": "data"}},
    )
    assert result.folder.parent == destination
    assert result.folder.name.endswith("Sleep interns 2025")
    assert (result.folder / "files" / "figures" / "plot.png").read_bytes() == b"png"
    manifest = json.loads((result.folder / MANIFEST).read_text())
    assert manifest["conversation"] == {"id": "c1", "kind": "data"}
    assert [f["path"] for f in manifest["files"]] == ["files/table.csv", "files/figures/plot.png"]
    assert manifest["files"][0]["from"] == "/work/outputs/table.csv"
    assert len(manifest["files"][0]["sha256"]) == 64


def test_exports_never_overwrite(tmp_path, outputs):
    destination = tmp_path / "out"
    destination.mkdir()
    first = export(destination, title="same", sources=[source(outputs, "table.csv")], about={})
    second = export(destination, title="same", sources=[source(outputs, "table.csv")], about={})
    assert first.folder != second.folder
    assert second.folder.name.endswith("(2)")


def test_a_failed_export_leaves_nothing_behind(tmp_path, outputs):
    destination = tmp_path / "out"
    destination.mkdir()
    (outputs / "link.csv").symlink_to(tmp_path / "elsewhere.csv")
    (tmp_path / "elsewhere.csv").write_text("host file")
    with pytest.raises(UnsafePath):
        export(
            destination,
            title="t",
            sources=[source(outputs, "table.csv"), source(outputs, "link.csv")],
            about={},
        )
    assert list(destination.iterdir()) == []


def test_missing_destination_is_an_error(tmp_path, outputs):
    with pytest.raises(ExportError):
        export(tmp_path / "gone", title="t", sources=[], about={})


def test_report_is_bound_by_a_no_network_policy():
    document = report_document(
        "Report <script>alert(1)</script>",
        "<h1>Hi</h1><img src='https://example.org/x.png'>",
        "body{color:red}</style><script>x()</script>",
    ).decode()
    head = document[: document.index("</head>")]
    assert head.index("Content-Security-Policy") < head.index("<title>")
    assert REPORT_POLICY in head and "default-src 'none'" in REPORT_POLICY
    assert "<script>alert" not in head and "</style><script>" not in head


def test_safe_names():
    assert safe_name('a/b\\c:d*e?"f"<g>|h') == "a b c d e f g h"
    assert safe_name("...") == "export"
    assert len(safe_name("x" * 200)) == 60


def test_files_that_would_run_are_made_inert(tmp_path, outputs):
    (outputs / "results.csv.bat").write_text("del *")
    (outputs / "report.html").write_text(
        "<p>hi</p><img src='https://x/?d=1'><script>fetch('https://x')</script>"
    )
    (outputs / "chart.svg").write_text('<svg><rect width="1"/></svg>')
    (outputs / "evil.svg").write_text('<svg><x:script xmlns:x="http://www.w3.org/2000/svg"/></svg>')
    destination = tmp_path / "out"
    destination.mkdir()
    names = ["results.csv.bat", "report.html", "chart.svg", "evil.svg"]
    result = export(destination, title="t", sources=[source(outputs, n) for n in names], about={})
    files = result.folder / "files"
    assert (files / "results.csv.bat.txt").read_text() == "del *"
    assert (files / "chart.svg").exists() and (files / "evil.svg.txt").exists()
    page = (files / "report.html").read_text()
    assert page.index("Content-Security-Policy") < page.index("<p>hi</p>")
    assert "https://x" not in page and "<script" not in page
    manifest = json.loads((result.folder / MANIFEST).read_text())
    renamed = {f.get("renamed_from") for f in manifest["files"]}
    assert {"results.csv.bat", "evil.svg"} <= renamed


def test_raw_html_is_kept_only_when_asked(tmp_path, outputs):
    (outputs / "app.html").write_text("<script>1</script>")
    destination = tmp_path / "out"
    destination.mkdir()
    raw = ExportSource(
        lambda: open_workspace_file(outputs, "app.html"), "app.html", "/work/outputs/app.html", True
    )
    result = export(destination, title="t", sources=[raw], about={})
    assert (result.folder / "files" / "app.html").read_text() == "<script>1</script>"


def test_the_report_and_manifest_cant_be_replaced(tmp_path, outputs):
    (outputs / "conversation.html").write_text("fake report")
    destination = tmp_path / "out"
    destination.mkdir()
    result = export(
        destination,
        title="t",
        sources=[source(outputs, "conversation.html")],
        extra_files={"conversation.html": b"real report"},
        about={},
    )
    assert (result.folder / "conversation.html").read_bytes() == b"real report"
    assert (result.folder / "files" / "conversation.html").exists()


def test_windows_trailing_dots_and_utf16_svgs_dont_slip_through(tmp_path, outputs):
    (outputs / "run.bat.").write_text("del *")
    (outputs / "page.html ").write_text("<script>1</script><p>x</p>")
    utf16 = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    (outputs / "trick.svg").write_bytes(b"\xff\xfe" + utf16.encode("utf-16-le"))
    (outputs / "plot.svg").write_text(
        '<?xml version="1.0" encoding="utf-8"?><svg><defs><path id="g1" d="M0"/></defs>'
        '<use xlink:href="#g1"/><image xlink:href="data:image/png;base64,AA"/></svg>'
    )
    destination = tmp_path / "out"
    destination.mkdir()
    names = ["run.bat.", "page.html ", "trick.svg", "plot.svg"]
    result = export(destination, title="t", sources=[source(outputs, n) for n in names], about={})
    files = result.folder / "files"
    assert (files / "run.bat.txt").exists()
    assert "<script" not in (files / "page.html").read_text()
    assert (files / "trick.svg.txt").exists()
    assert (files / "plot.svg").exists()  # matplotlib-style SVGs stay usable
