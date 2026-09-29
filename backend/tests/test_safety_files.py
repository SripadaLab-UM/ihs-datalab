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
    assert sorted(p.name for p in safety_module._files(tmp_path)) == ["onedrive.txt", "plain.txt"]
