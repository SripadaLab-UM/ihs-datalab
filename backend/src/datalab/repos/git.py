"""Git, run by DataLab on the host, and the lab repos' clones in `<data_dir>/repos/`.

Git reads no configuration but the clone's own (`.git/config`, which only
DataLab writes): the person's global and the system config are switched off
(`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM`), so no filter, merge or diff
driver, URL rewrite, rerere, or submodule setting from them can apply. And
`.git/info/attributes`, which outranks any `.gitattributes` in the repo,
turns every filter, diff and merge driver and every text conversion off, so
a `.gitattributes` in the knowledge base can't select one either.

On top of that, every command gets the same fixed configuration on its
command line (`-c`):

- no hooks, no fsmonitor, no signing, no line-ending conversion, links
  checked out as plain files, names that Mac or Windows would read as `.git`
  refused, and only the https transport (the local one only in tests);
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

import contextlib
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BRANCH = "main"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
_NETWORK_SECONDS = 120
_LOCAL_SECONDS = 60
# A clone staging folder older than this is one a clone left when it stopped.
_STAGING_SECONDS = 60 * 60
_ZERO = "0" * 40

# The configuration every command runs with (see the module docstring).
_CONFIG = (
    f"core.hooksPath={os.devnull}",
    "core.fsmonitor=false",
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.symlinks=false",
    "core.quotePath=false",
    "core.protectHFS=true",
    "core.protectNTFS=true",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "push.gpgSign=false",
    "rerere.enabled=false",
    "rebase.updateRefs=false",
    "submodule.recurse=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "credential.interactive=false",
    "rebase.autoStash=false",
    "merge.conflictStyle=merge",
    "advice.detachedHead=false",
    "init.defaultBranch=main",
    # Without the system config, Git for Windows would otherwise look for a
    # CA bundle; use Windows' own certificate store instead.
    *(("http.sslBackend=schannel",) if sys.platform == "win32" else ()),
)
# Outranks every .gitattributes in the repo: no filters (smudge/clean, LFS),
# no external diff or merge drivers (`merge` set means git's own text
# merge), no end-of-line or encoding conversion, no $Id$ expansion.
ATTRIBUTES = "* -filter -diff merge -text -eol -ident -working-tree-encoding\n"
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
        f"credential.helper=!{quoted} -X utf8 -m datalab.repos.credential_helper",
    ]


def git_executable() -> str:
    """The git to run: the one on PATH, else Git for Windows where DataLab's
    installer puts it (a DataLab opened before that hasn't got it on PATH).
    Plain "git" if neither is found."""
    found = shutil.which("git")
    if found:
        return found
    if sys.platform == "win32":
        for variable, sub in (("LOCALAPPDATA", "Programs"), ("ProgramFiles", "")):
            base = os.environ.get(variable)
            if base:
                candidate = Path(base) / sub / "Git" / "cmd" / "git.exe"
                if candidate.is_file():
                    return str(candidate)
    return "git"


# Git for Windows, pinned like the installer's copy (installer/windows/install.ps1,
# $GitUrl and friends; tests check they match).
GIT_FOR_WINDOWS_URL = "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.1/Git-2.56.0-64-bit.exe"
GIT_FOR_WINDOWS_SHA256 = "bfe94e7b419b16eee9fecbd1253a98e3d4f49ba8f029630549052278ffe286a6"
GIT_FOR_WINDOWS_PUBLISHER = "Johannes Schindelin"
_install_lock = threading.Lock()


def install_git_for_windows() -> bool:
    """For a DataLab installed before its installer added Git: downloads the
    pinned Git for Windows, checks its SHA-256 and publisher's signature, and
    installs it for this account only (no administrator). True once git is
    there. Windows only; False (nothing done) elsewhere or on any failure."""
    if sys.platform != "win32":
        return False
    import hashlib
    import tempfile
    import urllib.request

    with _install_lock:
        if Path(git_executable()).is_absolute():
            return True
        try:
            with tempfile.TemporaryDirectory(prefix="datalab-git-") as folder:
                exe = Path(folder) / "Git-installer.exe"
                with urllib.request.urlopen(GIT_FOR_WINDOWS_URL, timeout=300) as response:
                    exe.write_bytes(response.read())
                if hashlib.sha256(exe.read_bytes()).hexdigest() != GIT_FOR_WINDOWS_SHA256:
                    return False
                check = (
                    "$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
                    "if ($s.Status -ne 'Valid') { exit 1 }; "
                    "if ($s.SignerCertificate.GetNameInfo('SimpleName', $false) -cne $args[1]) "
                    "{ exit 1 }"
                )
                signed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        check,
                        str(exe),
                        GIT_FOR_WINDOWS_PUBLISHER,
                    ],
                    capture_output=True,
                    timeout=120,
                )
                if signed.returncode != 0:
                    return False
                done = subprocess.run(
                    [
                        str(exe),
                        "/VERYSILENT",
                        "/NORESTART",
                        "/SUPPRESSMSGBOXES",
                        "/NOCANCEL",
                        "/SP-",
                        "/CURRENTUSER",
                    ],
                    capture_output=True,
                    timeout=600,
                )
                if done.returncode != 0:
                    return False
        except (OSError, subprocess.SubprocessError):
            return False
        return Path(git_executable()).is_absolute()


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith("GIT_") and k.upper() not in _DROPPED_ENV
    }
    env.update(
        # Only the clone's own config: never the person's or the system's.
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
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
    # failed: git stopped for another reason; nothing changed.
    state: Literal["done", "empty", "conflict", "failed"]
    commit: str | None = None
    conflicts: list[str] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class PushResult:
    # rejected: GitHub's main moved on meanwhile (fetch and try again).
    state: Literal["pushed", "rejected", "failed"]
    message: str = ""


_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10)),
}  # fmt: skip
_WINDOWS_CHARACTERS = set('<>:"|?*\\')
# Git's own files, which a repo may hold but DataLab never writes: they'd
# change how every clone of the lab repo behaves.
_TOP_LEVEL_GIT_NAMES = (".gitignore", ".github")


# Windows' 260-character limit applies to the whole path on disk, the clone's
# folder included: a lab repo's own paths stay well under it.
MAX_PATH_BYTES = 180
MAX_NAME_BYTES = 100


def name_problem(path: str) -> str | None:
    """Why a path can't go into a lab repo, or None if it can: it must be a
    plain relative path every disk and git take as it is."""
    if not path or path.startswith("/"):
        return "it isn't a relative path"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "its name has control characters"
    if any(ord(c) > 126 for c in path):
        # Also what hides `.git` from a check (a zero-width joiner, say).
        return "its name has characters other than plain ASCII"
    if any(c in _WINDOWS_CHARACTERS for c in path):
        return "its name has a character Windows can't use (< > : \" | ? * \\)"
    if len(path.encode()) > MAX_PATH_BYTES:
        return f"it's longer than {MAX_PATH_BYTES} characters, too long for some computers"
    parts = path.split("/")
    for number, part in enumerate(parts):
        if len(part.encode()) > MAX_NAME_BYTES:
            return f"a name in it is longer than {MAX_NAME_BYTES} characters"
        if part in ("", ".", ".."):
            return "it has an empty, . or .. part"
        if part.endswith((".", " ")) or part.startswith(" "):
            return "a name in it starts with a space or ends with a dot or a space"
        if re.search(r"~\d", part):
            return "a name in it looks like a Windows short name (such as GIT~1)"
        if part.split(".")[0].lower() in _WINDOWS_RESERVED:
            return "a name in it is one Windows reserves (such as CON or NUL)"
        top_level = number == 0 and part in _TOP_LEVEL_GIT_NAMES
        if part.lower().startswith(".git") and not top_level:
            return "names starting with .git are git's own control files"
    return None


def clone_path(data_dir: Path, repo: str) -> Path:
    """Where a lab repo (`owner/name`) is cloned: `<data_dir>/repos/<name>`."""
    return data_dir / "repos" / repo.split("/")[1]


def remove_tree(folder: Path) -> None:
    """Delete a folder that may hold a clone, as far as it can be deleted.

    On Windows git makes its object files read-only, and a read-only file
    can't be deleted there: plain `shutil.rmtree` skips them and leaves the
    clone's `.git` behind. Each such file is made writable and tried once
    more. Whatever still can't go (a file something has open) is left, so
    callers check whether `folder` still exists. Links aren't followed.
    """
    shutil.rmtree(folder, onexc=_retry_read_only)


_WINDOWS = os.name == "nt"


def _retry_read_only(function: Callable[..., object], path: str, error: BaseException) -> None:
    """remove_tree's second try at a read-only file (Windows only)."""
    # Access denied (5) only: a file in use (32) won't go on a second try.
    if not _WINDOWS or getattr(error, "winerror", None) != 5:
        return
    if function not in (os.unlink, os.rmdir):
        return
    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(path, stat.S_IWRITE, follow_symlinks=False)
        function(path)


def _modified(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def _parents(path: str) -> list[str]:
    """Each folder `path` is in, without a trailing slash: a/b/c -> a, a/b."""
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def safe_path(path: str) -> bool:
    """A relative path git and every disk can take as it is (see name_problem)."""
    return name_problem(path) is None


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
        # Tests only: a remote that's a folder on this computer.
        allow_local: bool = False,
    ) -> None:
        self.path = path
        self.remote = remote
        self._allow_local = allow_local
        self._before_network = before_network
        self._helper = helper_args(python)
        # Held for anything that reads and then changes the clone. One per
        # folder, shared by every Clone of it (the Pipelines tab's, and the
        # workflow runner's that copies a run's files from it).
        self.lock = _lock_for(path)

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
        config = (*_CONFIG, *(("protocol.file.allow=always",) if self._allow_local else ()))
        options = [part for setting in config for part in ("-c", setting)]
        command = [git_executable(), *options, *(self._helper if network else []), *args]
        try:
            try:
                done = self._run(command, cwd, input, env, network, timeout)
            except FileNotFoundError:
                if not install_git_for_windows():
                    raise
                command[0] = git_executable()
                done = self._run(command, cwd, input, env, network, timeout)
        except FileNotFoundError:
            raise GitError(
                "Git isn't installed on this computer. Run the DataLab installer again, or "
                "install Git from git-scm.com, then restart DataLab."
            ) from None
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} took too long.") from None
        if check and done.returncode != 0:
            raise GitError(_message(args[0], done.stderr))
        return done

    def _run(self, command, cwd, input, env, network, timeout):
        return subprocess.run(
            command,
            cwd=cwd or self.path,
            input=input,
            capture_output=True,
            env=git_env(env),
            timeout=timeout or (_NETWORK_SECONDS if network else _LOCAL_SECONDS),
        )

    def text(self, *args: str, **kwargs) -> str:
        return self.git(*args, **kwargs).stdout.decode("utf-8", "replace").strip()

    # Clone and sync ---------------------------------------------------------

    def exists(self) -> bool:
        # HEAD, not only .git: a folder left half deleted (found on Windows,
        # where an uninstall could delete all but .git/objects' read-only
        # files) isn't a clone, and every git command in it failed. A .git
        # file is a worktree's pointer to its repository.
        git_dir = self.path / ".git"
        return (git_dir / "HEAD").is_file() or git_dir.is_file()

    def set_aside(self) -> Path | None:
        """An unusable copy _clone moved out of the way, if there is one."""
        found = sorted(self.path.parent.glob(f".{self.path.name}.broken-*"), key=_modified)
        return found[-1] if found else None

    def set_aside_note(self, repo: str) -> str | None:
        """For the repo's status, whatever its state: where that copy is."""
        aside = self.set_aside()
        if aside is None:
            return None
        return (
            f"An unusable copy of {repo} was set aside at {aside}; any unshared changes "
            "are there. Delete that folder once you don't need it."
        )

    def sync(self, *, timeout: float | None = None) -> str:
        """Clone if there's no clone yet, else fetch and fast-forward `main`.
        GitHub's `main` afterwards."""
        with self.lock:
            if not self.exists():
                self._clone()
            else:
                self.protect()
                self.fetch(timeout=timeout)
                self.git("merge", "--ff-only", "-q", REMOTE_REF)
            head = self.remote_head()
            if head is None:
                raise GitError(f"The repo has no {BRANCH} branch.")
            return head

    def protect(self, git_dir: Path | None = None) -> None:
        """Write `.git/info/attributes` (see ATTRIBUTES), shared by worktrees."""
        info = (git_dir or self.path / ".git") / "info"
        info.mkdir(parents=True, exist_ok=True)
        target = info / "attributes"
        if (
            not target.is_symlink()
            and target.exists()
            and target.read_text(encoding="utf-8") == ATTRIBUTES
        ):
            return
        fresh = info / "attributes.tmp"
        fresh.unlink(missing_ok=True)
        fresh.write_text(ATTRIBUTES, encoding="utf-8")
        fresh.replace(target)

    def fetch(self, *, timeout: float | None = None) -> None:
        with self.lock:
            self.git(
                "fetch", "--prune", "--no-tags", "-q", "origin",
                f"+refs/heads/{BRANCH}:{REMOTE_REF}", network=True, timeout=timeout,
            )  # fmt: skip

    def fast_forward(self) -> None:
        """Bring the checkout's `main` up to the last fetched GitHub `main`."""
        with self.lock:
            self.protect()
            self.git("merge", "--ff-only", "-q", REMOTE_REF)

    def _clone(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A clone that stopped part way (DataLab quit, say) left its staging
        # folder. Only old ones: another DataLab process may be cloning now.
        for earlier in self.path.parent.glob(f".{self.path.name}.cloning-*"):
            if time.time() - _modified(earlier) > _STAGING_SECONDS:
                remove_tree(earlier)
        staging = self.path.parent / f".{self.path.name}.cloning-{secrets.token_hex(4)}"
        try:
            # No checkout until the attributes are in place.
            self.git(
                "clone", "--no-checkout", "--branch", BRANCH, "--single-branch", "--no-tags",
                "-q", "--", self.remote, str(staging), cwd=self.path.parent, network=True,
            )  # fmt: skip
            self.protect(staging / ".git")
            self.git("reset", "-q", "--hard", "HEAD", cwd=staging)
            if self.path.exists() or self.path.is_symlink() or self.path.is_junction():
                # Something that isn't a clone (see exists) is in the way: set
                # aside, not deleted, as the person's unshared changes may be
                # in it; the status says where (see set_aside). Only the
                # newest is kept.
                for earlier in self.path.parent.glob(f".{self.path.name}.broken-*"):
                    remove_tree(earlier)
                self.path.rename(
                    self.path.parent / f".{self.path.name}.broken-{secrets.token_hex(4)}"
                )
            staging.rename(self.path)
        finally:
            remove_tree(staging)

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

    def ls_tree(self, commit: str, *paths: str) -> dict[str, TreeEntry]:
        """The commit's entries, all of them or only those under `paths`."""
        out = self.git("ls-tree", "-r", "-z", "-l", "--full-tree", commit, "--", *paths).stdout
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
        out = self.git("diff", "--no-ext-diff", "--name-only", "-z", "--no-renames", a, b).stdout
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
                # What stays of the base: a file can't also be a folder, or git
                # would silently drop one of them.
                kept = {p for p in existing if files.get(p, b"") is not None}
                kept |= {p for p, c in files.items() if c is not None}
                folders = {f for p in kept for f in _parents(p)}
                for path, content in files.items():
                    if not safe_path(path):
                        raise GitError(f"{path!r} can't be committed.")
                    if content is not None:
                        clash = next((f for f in _parents(path) if f in kept), None)
                        if clash is not None:
                            raise GitError(f"{path!r} can't be committed: {clash} is a file.")
                        if path in folders:
                            raise GitError(f"{path!r} can't be committed: it's a folder.")
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
            self.protect()
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
                # Nothing staged means the resolution made the change a no-op.
                staged = self.git(
                    "diff", "--no-ext-diff", "--cached", "--quiet", cwd=tree, check=False
                )
                self.git("rebase", "--abort", cwd=tree, check=False)
                if left:
                    return RebaseResult("conflict", conflicts=left)
                if staged.returncode == 0:
                    return RebaseResult("empty", commit=onto)
                return RebaseResult("failed", message=_message("rebase", done.stderr))
        head = self.text("rev-parse", "HEAD", cwd=tree)
        onto_commit = self.text("rev-parse", f"{onto}^{{commit}}")
        return RebaseResult("empty" if head == onto_commit else "done", commit=head)

    def _conflicts(self, tree: Path) -> list[str]:
        out = self.git(
            "diff", "--no-ext-diff", "--name-only", "-z", "--diff-filter=U", cwd=tree, check=False
        )
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


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(key, threading.RLock())


def _message(command: str, stderr: bytes) -> str:
    text = stderr.decode("utf-8", "replace").strip()
    lines = [line for line in text.splitlines() if line.strip()][-6:]
    detail = " ".join(lines)[:600]
    return f"git {command} failed: {detail}" if detail else f"git {command} failed."
