"""The clones and git itself, against local bare repos (never GitHub)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from datalab.repos import credential_helper
from datalab.repos.git import (
    ATTRIBUTES,
    Clone,
    GitError,
    Identity,
    git_env,
    helper_args,
    safe_path,
)
from tests.kb_fixtures import Remote, sample_kb

ME = Identity("Yu Fang", "42+yfang@users.noreply.github.com")
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path)


@pytest.fixture
def clone(tmp_path, remote):
    return Clone(tmp_path / "data" / "repos" / "ihs-knowledge", remote.url, allow_local=True)


def test_clone_then_sync_follows_githubs_main(clone, remote):
    assert not clone.exists() and clone.remote_head() is None
    assert clone.sync() == remote.head()
    assert clone.exists()
    assert clone.ahead_behind() == (0, 0)
    # No token and no helper is ever written to the clone's config.
    config = (clone.path / ".git" / "config").read_text()
    assert "helper" not in config and "@" not in config
    remote.write({"qc/new.md": b"x"}, "Someone else's page")
    clone.fetch()
    assert clone.ahead_behind() == (0, 1)
    assert clone.sync() == remote.head()
    assert clone.ahead_behind() == (0, 0)
    assert (clone.path / "qc" / "new.md").read_bytes() == b"x"


def test_a_half_deleted_clone_is_set_aside_and_cloned_again(clone, remote):
    """Found with 0.3.0b5 on Windows: the 0.3.0b3 uninstaller left only
    .git/objects (git's read-only files), and the next install took that for
    a clone: every sync said "fatal: not a git repository"."""
    pack = clone.path / ".git" / "objects" / "pack"
    pack.mkdir(parents=True)
    (pack / "pack-1.pack").write_bytes(b"x")
    (clone.path / ".git" / "info").mkdir()  # what DataLab's protect() then added
    assert not clone.exists()

    assert clone.sync() == remote.head()
    assert clone.exists() and clone.ahead_behind() == (0, 0)
    aside = [p for p in clone.path.parent.iterdir() if p.name.startswith(".ihs-knowledge.broken-")]
    assert len(aside) == 1 and (aside[0] / ".git" / "objects" / "pack" / "pack-1.pack").exists()
    # Said whatever the state (in sync here), with where the copy is.
    assert clone.set_aside() == aside[0]
    note = clone.set_aside_note("SripadaLab-UM/ihs-knowledge")
    assert note is not None and str(aside[0]) in note and "unshared changes" in note

    # Broken again: only the newest copy set aside is kept.
    (clone.path / ".git" / "HEAD").unlink()
    assert clone.sync() == remote.head()
    now = [p for p in clone.path.parent.iterdir() if p.name.startswith(".ihs-knowledge.broken-")]
    assert len(now) == 1 and now[0] != aside[0] and clone.set_aside() == now[0]


def test_a_clone_with_no_copy_set_aside_has_no_note(clone, remote):
    clone.sync()
    assert clone.set_aside() is None and clone.set_aside_note("o/r") is None


def test_a_worktree_clone_counts_as_a_clone(tmp_path):
    folder = tmp_path / "repos" / "kb"
    folder.mkdir(parents=True)
    (folder / ".git").write_text("gitdir: ../elsewhere/.git/worktrees/kb\n")
    assert Clone(folder, "https://github.com/o/kb.git").exists()


def test_only_old_staging_folders_are_swept_before_a_clone(clone, remote):
    """A clone that stopped part way leaves .<name>.cloning-*: swept once
    it's an hour old. A newer one may be another DataLab process's."""
    import time

    parent = clone.path.parent
    parent.mkdir(parents=True)
    old, fresh = parent / ".ihs-knowledge.cloning-old", parent / ".ihs-knowledge.cloning-new"
    for folder in (old, fresh):
        (folder / ".git").mkdir(parents=True)
    hours_ago = time.time() - 2 * 60 * 60
    os.utime(old, (hours_ago, hours_ago))
    clone.sync()
    assert not old.exists() and fresh.exists()


@pytest.mark.parametrize(("winerror", "deleted"), [(5, True), (32, False)])
def test_the_read_only_retry_is_for_access_denied_only(tmp_path, monkeypatch, winerror, deleted):
    """remove_tree's second try, as it runs on Windows (where rmtree fails on
    a read-only file with WinError 5); not for a file in use (32)."""
    import stat

    from datalab.repos import git

    monkeypatch.setattr(git, "_WINDOWS", True)
    target = tmp_path / "pack-1.idx"
    target.write_bytes(b"x")
    os.chmod(target, stat.S_IREAD)
    error = PermissionError(13, "Access is denied")
    error.winerror = winerror  # type: ignore[attr-defined]
    git._retry_read_only(os.unlink, str(target), error)
    assert target.exists() is not deleted
    monkeypatch.setattr(git, "_WINDOWS", False)
    if target.exists():
        git._retry_read_only(os.unlink, str(target), PermissionError(13, "x"))
        assert target.exists()  # never off Windows


def test_a_failed_clone_leaves_nothing_behind(tmp_path):
    clone = Clone(tmp_path / "repos" / "kb", str(tmp_path / "missing.git"), allow_local=True)
    with pytest.raises(GitError):
        clone.sync()
    assert list((tmp_path / "repos").iterdir()) == []


def test_the_copy_has_only_plain_files(clone, remote, tmp_path):
    # A link committed to the repo (pointing at the person's ssh keys).
    link = remote.other / "sources" / "keys.md"
    link.symlink_to(Path.home() / ".ssh" / "id_rsa")
    remote.write({}, "A link")
    head = clone.sync()
    copy = tmp_path / "copy"
    copy.mkdir()
    written = clone.copy_tree(head, copy, skip=lambda p: p.startswith(".github/"))
    assert written == len(sample_kb()) - 1
    assert not (copy / ".git").exists() and not (copy / ".github").exists()
    assert not (copy / "sources" / "keys.md").exists()
    assert (copy / "sources" / "fitbit.md").read_bytes() == sample_kb()["sources/fitbit.md"]
    # The clone's checkout has it as a plain file, never a link.
    assert not (clone.path / "sources" / "keys.md").is_symlink()


def test_commits_are_built_without_touching_the_checkout(clone, remote):
    head = clone.sync()
    commit = clone.commit_files(
        head, {"qc/new.md": b"new\n", "qc/midnight-sleep.md": None}, "Change\n", ME
    )
    assert clone.show(commit, "qc/new.md") == b"new\n"
    assert clone.show(commit, "qc/midnight-sleep.md") is None
    assert clone.changed_paths(head, commit) == ["qc/midnight-sleep.md", "qc/new.md"]
    author = clone.text("log", "-1", "--format=%an <%ae>|%P", commit)
    assert author == f"Yu Fang <{ME.email}>|{head}"
    assert clone.text("status", "--porcelain") == ""
    with pytest.raises(GitError):
        clone.commit_files(head, {"../escape.md": b"x"}, "m", ME)


def test_rebase_replays_only_our_change_and_reports_conflicts(clone, remote):
    base = clone.sync()
    ours = clone.commit_files(base, {"qc/midnight-sleep.md": b"ours\n"}, "Ours\n", ME)
    remote.write({"sources/fitbit.md": b"elsewhere\n"}, "Unrelated")
    clone.fetch()
    done = clone.rebase(ours, onto=clone.remote_head() or "", old_base=base, committer=ME)
    assert done.state == "done" and done.commit
    assert clone.show(done.commit, "sources/fitbit.md") == b"elsewhere\n"
    assert clone.show(done.commit, "qc/midnight-sleep.md") == b"ours\n"

    remote.write({"qc/midnight-sleep.md": b"theirs\n"}, "Same file")
    clone.fetch()
    upstream = clone.remote_head() or ""
    stopped = clone.rebase(ours, onto=upstream, old_base=base, committer=ME)
    assert stopped.state == "conflict" and stopped.conflicts == ["qc/midnight-sleep.md"]
    resolved = clone.rebase(
        ours,
        onto=upstream,
        old_base=base,
        committer=ME,
        resolutions={"qc/midnight-sleep.md": b"both\n"},
    )
    assert resolved.state == "done" and resolved.commit
    assert clone.show(resolved.commit, "qc/midnight-sleep.md") == b"both\n"
    # No worktree is left behind.
    assert not (clone.path.parent / ".ihs-knowledge-worktrees").exists()
    assert clone.text("worktree", "list").count("\n") == 0


def test_push_never_forces(clone, remote):
    base = clone.sync()
    ours = clone.commit_files(base, {"qc/new.md": b"ours\n"}, "Ours\n", ME)
    theirs = remote.write({"qc/other.md": b"theirs\n"}, "Theirs")
    pushed = clone.push(ours)
    assert pushed.state == "rejected"
    assert remote.head() == theirs  # their commit is still there
    clone.fetch()
    rebased = clone.rebase(ours, onto=theirs, old_base=base, committer=ME)
    assert rebased.commit and clone.push(rebased.commit).state == "pushed"
    assert remote.head() == rebased.commit
    assert remote.log("%P")[0] == theirs


def test_paths_that_arent_plain_are_refused():
    for bad in ("", "/etc/passwd", "../x", "a/../b", "a//b", ".git/config", "a\\b", "C:x", "a\nb"):
        assert not safe_path(bad), bad
    assert safe_path("tables/IHS_2025.VFITBITDAILYDATA.md")


def test_git_runs_with_no_inherited_git_settings_or_secrets(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath'='/tmp/evil'")
    monkeypatch.setenv("DATALAB_MODEL_API_KEY", "sk-secret")
    env = git_env()
    assert "GIT_DIR" not in env and "GIT_CONFIG_PARAMETERS" not in env
    assert "DATALAB_MODEL_API_KEY" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


# The credential helper ------------------------------------------------------


def test_the_helper_answers_only_for_github_over_https():
    def token() -> str:
        return "ghu_secret"

    request = credential_helper.parse("protocol=https\nhost=github.com\n\nignored=1\n")
    assert request == {"protocol": "https", "host": "github.com"}
    answer = credential_helper.answer("get", request, token)
    assert answer == "username=x-access-token\npassword=ghu_secret\n"
    for other in (
        {"protocol": "http", "host": "github.com"},
        {"protocol": "https", "host": "evil.example"},
        {"protocol": "https", "host": "github.com.evil.example"},
    ):
        assert credential_helper.answer("get", other, token) == ""
    assert credential_helper.answer("store", request, token) == ""
    assert credential_helper.answer("get", request, lambda: None) == ""
    assert credential_helper.answer("get", request, lambda: "a\nb") == ""


FAKE_KEYRING = """
import json, os
from keyring.backend import KeyringBackend

class FileKeyring(KeyringBackend):
    priority = 1
    def _all(self):
        try:
            with open(os.environ["FAKE_KEYRING_FILE"]) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    def get_password(self, service, username):
        return self._all().get(f"{service}/{username}")
    def set_password(self, service, username, password):
        raise AssertionError("the helper never writes to the keychain")
    def delete_password(self, service, username):
        raise AssertionError("the helper never deletes from the keychain")
"""


def test_git_gets_the_token_from_datalabs_helper_and_no_other(tmp_path):
    """Through real git: our helper answers, and the person's own helpers
    (here `store`, from their git config) are switched off, so the token is
    never offered to one to keep."""
    (tmp_path / "fake_keyring.py").write_text(FAKE_KEYRING)
    keys = tmp_path / "keys.json"
    tokens = '{"access_token": "ghu_from_keychain", "access_expires_at": null, '
    tokens += '"refresh_token": null, "refresh_expires_at": null}'
    keys.write_text(json.dumps({"datalab-github/user-token": tokens}))
    home = tmp_path / "home"
    home.mkdir()
    stored = tmp_path / "stored-credentials"
    (home / ".gitconfig").write_text(f"[credential]\n\thelper = store --file={stored}\n")
    env = {
        **git_env(),
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(BACKEND / "src")]),
        "PYTHON_KEYRING_BACKEND": "fake_keyring.FileKeyring",
        "FAKE_KEYRING_FILE": str(keys),
    }
    # DataLab's git reads neither the person's nor the system's config.
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
    command = ["git", *helper_args(sys.executable), "credential"]
    asked = b"protocol=https\nhost=github.com\n\n"
    filled = subprocess.run(
        [*command, "fill"], input=asked, env=env, capture_output=True, check=True
    ).stdout.decode()
    assert "username=x-access-token" in filled and "password=ghu_from_keychain" in filled
    # And even with a config git does read that names another helper (here
    # the person's own, forced in), the empty value first switches it off.
    forced = {**env, "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    subprocess.run([*command, "approve"], input=filled.encode(), env=forced, check=True)
    assert not stored.exists()
    # Without our options, that helper would have kept it.
    subprocess.run(["git", "credential", "approve"], input=filled.encode(), env=forced, check=True)
    assert "ghu_from_keychain" in stored.read_text()


def test_no_driver_from_any_config_or_gitattributes_ever_runs(tmp_path, monkeypatch):
    """A `.gitattributes` in the repo selects filter, merge and diff drivers,
    and LFS; the person's config defines them (and rewrites URLs, and turns
    on rerere). None of it runs, and sync still works."""
    ran = tmp_path / "ran"
    home = tmp_path / "home"
    home.mkdir()
    driver = f"sh -c 'echo $0 >> {ran}; cat'"
    remote = Remote(tmp_path / "gh")
    (home / ".gitconfig").write_text(
        f'[filter "evil"]\n\tsmudge = {driver} smudge\n\tclean = {driver} clean\n'
        "\trequired = true\n"
        '[filter "lfs"]\n\tsmudge = git-lfs-not-installed smudge %f\n\trequired = true\n'
        f'[merge "evil"]\n\tdriver = {driver} merge\n'
        f'[diff "evil"]\n\ttextconv = {driver} textconv\n'
        f'[url "{tmp_path}/nowhere/"]\n\tinsteadOf = {remote.url}\n'
        "[rerere]\n\tenabled = true\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    remote.write(
        {".gitattributes": b"* filter=evil merge=evil diff=evil\n*.md filter=lfs\n"},
        "Hostile attributes",
    )
    clone = Clone(tmp_path / "repos" / "ihs-knowledge", remote.url, allow_local=True)
    base = clone.sync()
    assert (clone.path / ".git" / "info" / "attributes").read_text() == ATTRIBUTES
    # Even a driver in the clone's own config (which only DataLab writes) is off.
    with (clone.path / ".git" / "config").open("a") as config:
        config.write(f'[filter "evil"]\n\tsmudge = {driver} smudge\n')
    ours = clone.commit_files(base, {"qc/midnight-sleep.md": b"ours\n"}, "Ours\n", ME)
    remote.write({"qc/midnight-sleep.md": b"theirs\n", "qc/new.md": b"x\n"}, "Theirs")
    assert clone.sync() == remote.head()
    upstream = remote.head() or ""
    assert clone.rebase(ours, onto=upstream, old_base=base, committer=ME).state == "conflict"
    resolved = clone.rebase(
        ours,
        onto=upstream,
        old_base=base,
        committer=ME,
        resolutions={"qc/midnight-sleep.md": b"both\n"},
    )
    assert resolved.state == "done" and resolved.commit
    clone.changed_paths(base, resolved.commit)
    copy = tmp_path / "copy"
    copy.mkdir()
    clone.copy_tree(resolved.commit, copy, skip=lambda p: False)
    assert (copy / "qc" / "midnight-sleep.md").read_bytes() == b"both\n"
    assert not ran.exists()
    assert not (clone.path / ".git" / "rr-cache").exists()


def test_the_local_transport_is_only_for_tests(tmp_path, remote):
    clone = Clone(tmp_path / "repos" / "kb", remote.url)
    with pytest.raises(GitError, match="transport 'file' not allowed"):
        clone.sync()


def test_a_rebase_that_stops_with_changes_staged_is_a_failure_not_empty(clone, remote, monkeypatch):
    base = clone.sync()
    ours = clone.commit_files(base, {"qc/midnight-sleep.md": b"ours\n"}, "Ours\n", ME)
    upstream = remote.write({"qc/midnight-sleep.md": b"theirs\n"}, "Theirs")
    clone.fetch()
    real = clone.git

    def git(*args, **kwargs):
        if args[:2] == ("rebase", "--continue"):
            return subprocess.CompletedProcess(args, 1, b"", b"error: could not commit")
        return real(*args, **kwargs)

    monkeypatch.setattr(clone, "git", git)
    done = clone.rebase(
        ours, onto=upstream, old_base=base, committer=ME,
        resolutions={"qc/midnight-sleep.md": b"both\n"},
    )  # fmt: skip
    assert done.state == "failed" and "could not commit" in done.message
    # And when the resolution leaves nothing staged, it's empty.
    same = clone.rebase(
        ours, onto=upstream, old_base=base, committer=ME,
        resolutions={"qc/midnight-sleep.md": b"theirs\n"},
    )  # fmt: skip
    assert same.state == "empty"
