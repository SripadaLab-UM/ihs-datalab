import os

import pytest

from datalab.sessions.checkpoints import Checkpoints, UnsafePath, open_workspace_file


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    (folder / "outputs").mkdir(parents=True)
    return folder


@pytest.fixture
def checkpoints(tmp_path, work):
    return Checkpoints(tmp_path / "checkpoints", work, max_file_bytes=1000)


def test_restore_puts_files_back_exactly(work, checkpoints):
    (work / "analysis.R").write_text("x <- 1")
    (work / "outputs" / "table.csv").write_text("a,b\n1,2\n")
    (work / "empty").mkdir()
    first = checkpoints.take("After turn 1", turn=1)
    assert (first.number, first.files, first.turn) == (1, 2, 1)

    (work / "analysis.R").write_text("x <- 2  # changed")
    (work / "outputs" / "table.csv").unlink()
    (work / "new.txt").write_text("made later")
    (work / "later" / "deep").mkdir(parents=True)
    (work / "later" / "deep" / "f.txt").write_text("x")
    (work / "empty").rmdir()

    result = checkpoints.restore(1)
    assert (work / "analysis.R").read_text() == "x <- 1"
    assert (work / "outputs" / "table.csv").read_text() == "a,b\n1,2\n"
    assert not (work / "new.txt").exists()
    assert not (work / "later").exists()
    assert (work / "empty").is_dir()
    assert (result.written, result.removed) == (2, 2)


def test_unchanged_files_are_stored_once(tmp_path, work, checkpoints):
    (work / "big.txt").write_text("same content")
    checkpoints.take("one")
    checkpoints.take("two")
    objects = [p for p in (tmp_path / "checkpoints" / "objects").rglob("*") if p.is_file()]
    assert len(objects) == 1
    assert [c.label for c in checkpoints.list()] == ["one", "two"]


def test_large_files_are_listed_and_left_alone(work, checkpoints):
    (work / "huge.bin").write_bytes(b"x" * 5000)
    checkpoint = checkpoints.take("t")
    assert [(s.path, s.reason) for s in checkpoint.skipped] == [("huge.bin", "too large")]
    (work / "huge.bin").write_bytes(b"y" * 6000)
    result = checkpoints.restore(checkpoint.number)
    assert (work / "huge.bin").read_bytes() == b"y" * 6000  # not in the checkpoint: kept
    assert result.left_alone == ["huge.bin"]


def test_links_are_recorded_but_never_followed(tmp_path, work, checkpoints):
    secret = tmp_path / "secret.txt"
    secret.write_text("host file")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("host folder")
    (work / "link.txt").symlink_to(secret)
    (work / "folder-link").symlink_to(outside)
    checkpoint = checkpoints.take("t")
    assert (checkpoint.files, checkpoint.skipped) == (0, [])
    stored = b"".join(
        p.read_bytes() for p in (tmp_path / "checkpoints" / "objects").rglob("*") if p.is_file()
    )
    assert b"host" not in stored
    # Restoring puts the links back as links; their targets are untouched.
    (work / "link.txt").unlink()
    checkpoints.restore(checkpoint.number)
    assert os.readlink(work / "link.txt") == str(secret)
    assert secret.read_text() == "host file"


def test_restore_keeps_what_the_undo_checkpoint_couldnt_save(work, checkpoints):
    (work / "a.txt").write_text("a")
    target = checkpoints.take("After turn 1")
    # Later: a large extract, and a pipe, neither of which a checkpoint can hold.
    (work / "outputs" / "extract.csv").write_bytes(b"x" * 5000)
    os.mkfifo(work / "pipe")
    before = checkpoints.take("Before restoring")
    keep = frozenset(s.path for s in before.skipped)
    assert keep == {"outputs/extract.csv", "pipe"}
    result = checkpoints.restore(target.number, keep=keep)
    assert (work / "outputs" / "extract.csv").stat().st_size == 5000
    assert set(result.left_alone) == {"outputs/extract.csv", "pipe"}


def test_a_planted_pipe_doesnt_hang_a_checkpoint(work, checkpoints):
    os.mkfifo(work / "outputs" / "chart.png")
    checkpoint = checkpoints.take("t")  # would block forever if opened
    assert [s.reason for s in checkpoint.skipped] == ["not a regular file"]


def test_listing_reads_only_summaries(tmp_path, work, checkpoints):
    (work / "f.txt").write_text("x")
    first = checkpoints.take("one")
    assert [c.number for c in checkpoints.list()] == [1]
    assert checkpoints.latest() == first
    assert set(checkpoints.entries(1)) == {"f.txt"}
    with os.fdopen(checkpoints.open_object(checkpoints.entries(1)["f.txt"]), "rb") as saved:
        assert saved.read() == b"x"


def test_restore_replaces_planted_links_without_touching_their_targets(tmp_path, work, checkpoints):
    (work / "outputs" / "report.txt").write_text("mine")
    checkpoint = checkpoints.take("t")
    # The agent swaps the folder for a link to somewhere on the host.
    target = tmp_path / "host-folder"
    target.mkdir()
    (target / "report.txt").write_text("host content")
    (work / "outputs" / "report.txt").unlink()
    (work / "outputs").rmdir()
    (work / "outputs").symlink_to(target)

    checkpoints.restore(checkpoint.number)
    assert not (work / "outputs").is_symlink()
    assert (work / "outputs" / "report.txt").read_text() == "mine"
    assert (target / "report.txt").read_text() == "host content"


def test_executable_bit_is_restored(work, checkpoints):
    script = work / "run.sh"
    script.write_text("echo hi")
    script.chmod(0o755)
    checkpoint = checkpoints.take("t")
    script.chmod(0o644)
    checkpoints.restore(checkpoint.number)
    assert os.access(script, os.X_OK)


def test_serving_refuses_links_and_escapes(tmp_path, work):
    (tmp_path / "secret.txt").write_text("host file")
    (work / "outputs" / "ok.txt").write_text("fine")
    (work / "outputs" / "link.txt").symlink_to(tmp_path / "secret.txt")
    (work / "outputs" / "dir-link").symlink_to(tmp_path)
    root = work / "outputs"
    os.close(open_workspace_file(root, "ok.txt"))
    for bad in ("link.txt", "dir-link/secret.txt", "../../secret.txt", "", "/etc/passwd", "a\\b"):
        with pytest.raises(UnsafePath):
            open_workspace_file(root, bad)


def test_restore_never_replaces_what_it_leaves_alone(work, checkpoints):
    # The checkpoint being restored has these as small files, a file, and a link...
    (work / "big.csv").write_text("small then")
    (work / "a").write_text("a was a file then")
    (work / "slot").write_text("x")
    (work / "l").symlink_to("somewhere")
    target = checkpoints.take("After turn 1")
    # ...now they're things the pre-restore checkpoint couldn't save.
    (work / "big.csv").write_bytes(b"x" * 5000)  # too large now
    (work / "a").unlink()
    (work / "a").mkdir()
    (work / "a" / "huge.bin").write_bytes(b"y" * 5000)
    (work / "slot").unlink()
    os.mkfifo(work / "slot")
    (work / "l").unlink()
    (work / "l").write_bytes(b"z" * 5000)
    before = checkpoints.take("Before")
    keep = frozenset(s.path for s in before.skipped)
    assert keep == {"big.csv", "a/huge.bin", "slot", "l"}

    result = checkpoints.restore(target.number, keep=keep)
    assert (work / "big.csv").stat().st_size == 5000
    assert (work / "a" / "huge.bin").stat().st_size == 5000
    assert (work / "l").stat().st_size == 5000 and not (work / "l").is_symlink()
    assert os.path.exists(work / "slot")
    assert set(result.not_restored) == {"big.csv", "a", "slot", "l"}


def test_restore_repairs_folders_the_agent_locked(work, checkpoints):
    (work / "keep.txt").write_text("keep")
    target = checkpoints.take("t")
    locked = work / "locked"
    locked.mkdir()
    (locked / "z.txt").write_text("z")
    locked.chmod(0o000)
    try:
        before = checkpoints.take("Before")
        assert ("locked", "unreadable") in [(s.path, s.reason) for s in before.skipped]
        checkpoints.restore(target.number, keep=frozenset(s.path for s in before.skipped))
        # Left alone (it couldn't be saved), but DataLab can read it again.
        assert (locked / "z.txt").read_text() == "z"
    finally:
        locked.chmod(0o700)
