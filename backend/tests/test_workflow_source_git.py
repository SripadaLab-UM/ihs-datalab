"""A workflow folder that's a git repo: each file's blob and commit, with git run once per file."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from datalab.workflows import source
from datalab.workflows.source import WorkflowFolder, git_blob_id


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=x@example.com", "-c", "user.name=x", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for name in ("a.yaml", "b.yaml", "c.yaml"):
        (tmp_path / name).write_text(f"name: {name[0]}\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "first")
    return tmp_path


def test_committed_files_have_their_blob_and_commit(repo: Path) -> None:
    folder = WorkflowFolder(repo)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    file = folder.read("a.yaml")
    assert (file.source, file.blob, file.commit) == (
        "git",
        git_blob_id((repo / "a.yaml").read_bytes()),
        head,
    )
    # Edited since: not the committed file any more.
    (repo / "b.yaml").write_text("name: changed\n")
    assert folder.read("b.yaml").source == "file"


def test_a_new_commit_is_seen_and_git_runs_once_per_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    real = source._git

    def counting(root: Path, *args: str) -> str:
        calls.append(args)
        return real(root, *args)

    monkeypatch.setattr(source, "_git", counting)
    folder = WorkflowFolder(repo)
    for path in folder.paths():
        assert folder.read(path).source == "git"
    # Three rev-parse calls (one per file) and one listing of the commit.
    assert sum(args[0] == "rev-parse" for args in calls) == 3
    assert sum(args[0] == "ls-tree" for args in calls) == 1

    (repo / "c.yaml").write_text("name: c2\n")
    assert folder.read("c.yaml").source == "file"
    _git(repo, "commit", "-qam", "second")
    file = folder.read("c.yaml")
    assert file.source == "git"
    assert file.blob == git_blob_id(b"name: c2\n")
