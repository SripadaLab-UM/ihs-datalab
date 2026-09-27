"""Git, run by DataLab on the host, and the lab repos' clones in `<data_dir>/repos/`.

Every git command gets the same fixed configuration on its command line
(`-c`), whatever the person's own git config says:

- no hooks, no fsmonitor, no signing, no line-ending conversion, links
  checked out as plain files, and only the https and local transports;
- for commands that talk to GitHub, DataLab's credential helper and no
  other (credential_helper.py). Nothing is ever written to a git config
  file, and the token is never on a command line or in the environment;
- no prompts: a command that would ask for anything fails instead.

The clone is DataLab's alone: nothing else writes to it, and no container
ever sees it. Its `main` only ever fast-forwards to GitHub's. Save & share
builds commits with plumbing (a private index file), and rebases them in a
temporary worktree next to the clone, so the clone's own checkout never
holds anything half-done.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BRANCH = "main"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
_NETWORK_SECONDS = 120
_LOCAL_SECONDS = 60
_ZERO = "0" * 40

# The configuration every command runs with (see the module docstring).
_CONFIG = (
    f"core.hooksPath={os.devnull}",
    "core.fsmonitor=false",
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.symlinks=false",
    "core.quotePath=false",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "protocol.file.allow=always",
    "credential.interactive=false",
    "rebase.autoStash=false",
    "merge.conflictStyle=merge",
    "advice.detachedHead=false",
    "init.defaultBranch=main",
)
# Environment variables git would read that no command of ours should
# inherit, besides every GIT_* one.
_DROPPED_ENV = ("SSH_ASKPASS", "DATALAB_MODEL_API_KEY", "DATALAB_ORACLE_PASSWORD")


class GitError(RuntimeError):
    """A git command failed. The message is git's own (it never holds a token)."""


def helper_args(python: str | None = None) -> list[str]:
    """The `-c` options that give git DataLab's credential helper, and only it."""
    python = python or sys.executable
    if sys.platform == "win32":
        # Git for Windows runs helpers with its own sh, which takes forward slashes.
        quoted = '"' + python.replace("\\", "/") + '"'
    else:
        quoted = shlex.quote(python)
    return [
        "-c",
        "credential.helper=",
        "-c",
        f"credential.helper=!{quoted} -m datalab.repos.credential_helper",
    ]


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith("GIT_") and k.upper() not in _DROPPED_ENV
    }
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GCM_INTERACTIVE="never",
        GIT_EDITOR="true",
        GIT_SEQUENCE_EDITOR="true",
        LC_ALL="C",
        LANG="C",
    )
    if extra:
        env.update(extra)
    return env


@dataclass(frozen=True)
class Identity:
    name: str
    email: str

    def env(self, role: Literal["AUTHOR", "COMMITTER"]) -> dict[str, str]:
        return {f"GIT_{role}_NAME": self.name, f"GIT_{role}_EMAIL": self.email}


@dataclass(frozen=True)
class TreeEntry:
    mode: str  # "100644", "100755", "120000" (a link), "160000" (a submodule)
    blob: str
    size: int

    @property
    def regular(self) -> bool:
        return self.mode in ("100644", "100755")


@dataclass(frozen=True)
class RebaseResult:
    # done: rebased (possibly with the given resolutions); empty: nothing of
    # ours was left once rebased; conflict: stopped, nothing changed.
    state: Literal["done", "empty", "conflict"]
    commit: str | None = None
    conflicts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PushResult:
    # rejected: GitHub's main moved on meanwhile (fetch and try again).
    state: Literal["pushed", "rejected", "failed"]
    message: str = ""


def safe_path(path: str) -> bool:
    """A relative path git and every disk can take as it is: no `..`, no
    absolute or drive paths, no control characters or backslashes."""
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    if any(ord(c) < 32 or c == "\x7f" for c in path):
        return False
    parts = path.split("/")
    return all(p not in ("", ".", "..") and p.lower() != ".git" for p in parts)


class Clone:
    """One lab repo, cloned into DataLab's data folder."""

    def __init__(
        self,
        path: Path,
        remote: str,
        *,
        # Called before every command that talks to the remote: DataLab
        # makes sure the GitHub token is fresh there (it may raise).
        before_network: Callable[[], object] = lambda: None,
        python: str | None = None,
    ) -> None:
        self.path = path
        self.remote = remote
        self._before_network = before_network
        self._helper = helper_args(python)
        # Held for anything that reads and then changes the clone.
        self.lock = threading.RLock()

    # Running git ------------------------------------------------------------

    def git(
        self,
        *args: str,
        cwd: Path | None = None,
        input: bytes | None = None,
        network: bool = False,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if network:
            self._before_network()
        options = [part for setting in _CONFIG for part in ("-c", setting)]
        command = ["git", *options, *(self._helper if network else []), *args]
        try:
            done = subprocess.run(
                command,
                cwd=cwd or self.path,
                input=input,
                capture_output=True,
                env=git_env(env),
                timeout=timeout or (_NETWORK_SECONDS if network else _LOCAL_SECONDS),
            )
        except FileNotFoundError:
            raise GitError("Git isn't installed on this computer.") from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took too long.") from None
        if check and done.returncode != 0:
            raise GitError(_message(args[0], done.stderr))
        return done

    def text(self, *args: str, **kwargs) -> str:
        return self.git(*args, **kwargs).stdout.decode("utf-8", "replace").strip()

    # Clone and sync ---------------------------------------------------------

    def exists(self) -> bool:
        return (self.path / ".git").is_dir()

    def sync(self, *, timeout: float | None = None) -> str:
        """Clone if there's no clone yet, else fetch and fast-forward `main`.
        GitHub's `main` afterwards."""
        with self.lock:
            if not self.exists():
                self._clone()
            else:
                self.fetch(timeout=timeout)
                self.git("merge", "--ff-only", "-q", REMOTE_REF)
            head = self.remote_head()
            if head is None:
                raise GitError(f"The repo has no {BRANCH} branch.")
            return head

    def fetch(self, *, timeout: float | None = None) -> None:
        with self.lock:
            self.git(
                "fetch", "--prune", "--no-tags", "-q", "origin",
                f"+refs/heads/{BRANCH}:{REMOTE_REF}", network=True, timeout=timeout,
            )  # fmt: skip

    def fast_forward(self) -> None:
        """Bring the checkout's `main` up to the last fetched GitHub `main`."""
        with self.lock:
            self.git("merge", "--ff-only", "-q", REMOTE_REF)

    def _clone(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.parent / f".{self.path.name}.cloning-{secrets.token_hex(4)}"
        try:
            self.git(
                "clone", "--branch", BRANCH, "--single-branch", "--no-tags", "-q",
                "--", self.remote, str(staging), cwd=self.path.parent, network=True,
            )  # fmt: skip
            staging.rename(self.path)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def remote_head(self) -> str | None:
        return self.resolve(REMOTE_REF)

    def resolve(self, ref: str) -> str | None:
        if not self.exists():
            return None
        done = self.git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
        if done.returncode != 0:
            return None
        return done.stdout.decode().strip() or None

    def ahead_behind(self) -> tuple[int, int]:
        """How far the local `main` is ahead of, and behind, the last fetched
        GitHub `main`."""
        counts = self.text("rev-list", "--left-right", "--count", f"{BRANCH}...{REMOTE_REF}")
        ahead, behind = (int(n) for n in counts.split())
        return ahead, behind

    # Reading ----------------------------------------------------------------

    def ls_tree(self, commit: str) -> dict[str, TreeEntry]:
        out = self.git("ls-tree", "-r", "-z", "-l", "--full-tree", commit).stdout
        entries: dict[str, TreeEntry] = {}
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            mode, _kind, blob, size = meta.decode().split()
            path = name.decode("utf-8", "surrogateescape")
            entries[path] = TreeEntry(mode, blob, int(size) if size.isdigit() else 0)
        return entries

    def read_blobs(self, blobs: Iterable[str]) -> dict[str, bytes]:
        wanted = list(dict.fromkeys(blobs))
        if not wanted:
            return {}
        out = self.git("cat-file", "--batch", input=("\n".join(wanted) + "\n").encode()).stdout
        found: dict[str, bytes] = {}
        at = 0
        for blob in wanted:
            end = out.index(b"\n", at)
            header = out[at:end].decode().split()
            at = end + 1
            if len(header) < 3 or header[1] == "missing":
                continue
            size = int(header[2])
            found[blob] = out[at : at + size]
            at += size + 1
        return found

    def show(self, commit: str, path: str) -> bytes | None:
        entry = self.ls_tree(commit).get(path)
        if entry is None or not entry.regular:
            return None
        return self.read_blobs([entry.blob]).get(entry.blob)

    def changed_paths(self, a: str, b: str) -> list[str]:
        out = self.git("diff", "--name-only", "-z", "--no-renames", a, b).stdout
        return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]

    def copy_tree(self, commit: str, folder: Path, *, skip: Callable[[str], bool]) -> int:
        """Write the regular files of `commit` into `folder`, a fresh empty
        folder of DataLab's. Links, submodules, and paths that aren't plain
        are left out. The number of files written."""
        entries = {
            path: entry
            for path, entry in self.ls_tree(commit).items()
            if entry.regular and safe_path(path) and not skip(path)
        }
        contents = self.read_blobs(e.blob for e in entries.values())
        for path, entry in sorted(entries.items()):
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_BINARY", 0)
            fd = os.open(target, flags, 0o755 if entry.mode == "100755" else 0o644)
            with os.fdopen(fd, "wb") as out:
                out.write(contents.get(entry.blob, b""))
        return len(entries)

    # Writing ----------------------------------------------------------------

    def commit_files(
        self,
        parent: str,
        files: Mapping[str, bytes | None],
        message: str,
        author: Identity,
        *,
        committer: Identity | None = None,
        tree_of: str | None = None,
        parents: list[str] | None = None,
    ) -> str:
        """A new commit: the tree of `tree_of` (default `parent`) with `files`
        written (None deletes), on `parents` (default `[parent]`). Uses a
        private index, so neither the clone's checkout nor its index change."""
        with self.lock:
            index = self.path / ".git" / f"datalab-index-{secrets.token_hex(4)}"
            env = {"GIT_INDEX_FILE": str(index)}
            try:
                base = tree_of or parent
                self.git("read-tree", base, env=env)
                existing = self.ls_tree(base)
                records = []
                for path, content in files.items():
                    if not safe_path(path):
                        raise GitError(f"{path!r} can't be committed.")
                    if content is None:
                        records.append(f"0 {_ZERO}\t{path}")
                        continue
                    blob = self.text("hash-object", "-w", "--no-filters", "--stdin", input=content)
                    known = existing.get(path)
                    mode = "100755" if known is not None and known.mode == "100755" else "100644"
                    records.append(f"{mode} {blob}\t{path}")
                if records:
                    info = "".join(r + "\0" for r in records).encode("utf-8", "surrogateescape")
                    self.git("update-index", "-z", "--index-info", input=info, env=env)
                tree = self.text("write-tree", env=env)
            finally:
                index.unlink(missing_ok=True)
            who = {**author.env("AUTHOR"), **(committer or author).env("COMMITTER")}
            parent_args = [arg for p in (parents or [parent]) for arg in ("-p", p)]
            return self.text(
                "commit-tree", tree, *parent_args, "-F", "-", input=message.encode(), env=who
            )

    def set_ref(self, ref: str, commit: str) -> None:
        self.git("update-ref", ref, commit)

    def rebase(
        self,
        commit: str,
        *,
        onto: str,
        old_base: str,
        committer: Identity,
        resolutions: Mapping[str, bytes] | None = None,
    ) -> RebaseResult:
        """Replay what `commit` changed since `old_base` onto `onto`, in a
        temporary worktree. Where both changed the same lines, a resolution
        (the person's final text for that file) is used if there is one for
        every conflicting file; otherwise nothing changes and the conflicting
        files are reported."""
        with self.lock:
            place = self.path.parent / f".{self.path.name}-worktrees"
            shutil.rmtree(place, ignore_errors=True)  # left over from a crash
            self.git("worktree", "prune")
            place.mkdir(parents=True)
            tree = place / secrets.token_hex(4)
            self.git("worktree", "add", "--detach", "-q", str(tree), commit)
            try:
                return self._rebase_in(tree, onto, old_base, committer, resolutions or {})
            finally:
                self.git("worktree", "remove", "--force", str(tree), check=False)
                shutil.rmtree(place, ignore_errors=True)
                self.git("worktree", "prune", check=False)

    def _rebase_in(
        self,
        tree: Path,
        onto: str,
        old_base: str,
        committer: Identity,
        resolutions: Mapping[str, bytes],
    ) -> RebaseResult:
        who = committer.env("COMMITTER")
        done = self.git("rebase", "-q", "--onto", onto, old_base, cwd=tree, env=who, check=False)
        if done.returncode != 0:
            conflicts = self._conflicts(tree)
            if not conflicts or not all(p in resolutions for p in conflicts):
                self.git("rebase", "--abort", cwd=tree, check=False)
                return RebaseResult("conflict", conflicts=conflicts)
            for path in conflicts:
                (tree / path).write_bytes(resolutions[path])
            self.git("add", "--", *conflicts, cwd=tree)
            done = self.git("rebase", "--continue", cwd=tree, env=who, check=False)
            if done.returncode != 0:
                left = self._conflicts(tree)
                self.git("rebase", "--abort", cwd=tree, check=False)
                if left:
                    return RebaseResult("conflict", conflicts=left)
                # The resolution made the change a no-op: nothing to save.
                return RebaseResult("empty", commit=onto)
        head = self.text("rev-parse", "HEAD", cwd=tree)
        onto_commit = self.text("rev-parse", f"{onto}^{{commit}}")
        return RebaseResult("empty" if head == onto_commit else "done", commit=head)

    def _conflicts(self, tree: Path) -> list[str]:
        out = self.git("diff", "--name-only", "-z", "--diff-filter=U", cwd=tree, check=False)
        return sorted(p.decode("utf-8", "surrogateescape") for p in out.stdout.split(b"\0") if p)

    def push(self, commit: str) -> PushResult:
        """Push exactly `commit` to GitHub's `main`, never forcing."""
        with self.lock:
            done = self.git(
                "push", "--porcelain", "origin", f"{commit}:refs/heads/{BRANCH}",
                network=True, check=False,
            )  # fmt: skip
            if done.returncode == 0:
                return PushResult("pushed")
            said = (done.stdout + b"\n" + done.stderr).decode("utf-8", "replace")
            if re.search(r"non-fast-forward|fetch first|\[rejected\]", said):
                return PushResult("rejected", _message("push", done.stderr))
            return PushResult("failed", _message("push", done.stderr))


def _message(command: str, stderr: bytes) -> str:
    text = stderr.decode("utf-8", "replace").strip()
    lines = [line for line in text.splitlines() if line.strip()][-6:]
    detail = " ".join(lines)[:600]
    return f"git {command} failed: {detail}" if detail else f"git {command} failed."
