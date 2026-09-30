"""safety._files: which files the secret scans read (no Docker needed)."""

from __future__ import annotations


def test_the_secret_scans_skip_links_but_read_onedrive_placeholders(tmp_path, monkeypatch):
    """Windows reparse points: a name surrogate (bit 0x20000000: WSL's Linux
    symlink 0xA000001D, which Codex makes in codex-home, or a junction) is a
    link and isn't read; a OneDrive cloud placeholder (0x9000601A) is a regular
    file and is."""
    import pathlib
    import stat as stat_module

    from datalab import safety as safety_module

    tags = {"lx.txt": 0xA000001D, "junction.txt": 0xA0000003, "onedrive.txt": 0x9000601A}
    for name in [*tags, "plain.txt"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    real_lstat = pathlib.Path.lstat

    class Info:
        def __init__(self, tag):
            self.st_mode = stat_module.S_IFREG | 0o644
            self.st_reparse_tag = tag

    def lstat(self):
        return Info(tags[self.name]) if self.name in tags else real_lstat(self)

    monkeypatch.setattr(pathlib.Path, "lstat", lstat)
    found, unreadable = safety_module._files(tmp_path)
    assert sorted(p.name for p in found) == ["onedrive.txt", "plain.txt"] and unreadable == 0


def test_the_secret_scans_count_what_they_couldnt_read(tmp_path, monkeypatch):
    """A file that can't be looked at is counted, so a check never says it
    read every file when it didn't; one gone meanwhile isn't."""
    import pathlib

    from datalab import safety as safety_module

    for name in ("locked.txt", "gone.txt", "plain.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    real_lstat = pathlib.Path.lstat

    def lstat(self):
        if self.name == "locked.txt":
            raise PermissionError(13, "Access is denied")
        if self.name == "gone.txt":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real_lstat(self)

    monkeypatch.setattr(pathlib.Path, "lstat", lstat)
    found, unreadable = safety_module._files(tmp_path)
    assert [p.name for p in found] == ["plain.txt"] and unreadable == 1
    assert safety_module._unread(unreadable) == " 1 file couldn't be read."
    assert (
        safety_module._unread(3) == " 3 files couldn't be read." and safety_module._unread(0) == ""
    )
