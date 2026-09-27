"""scripts/sign-release.py: the release workflow's signing step."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from datalab import release_keys, signing

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sign-release.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("sign_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_signs_sha256sums_with_a_key_the_package_pins(script, tmp_path, monkeypatch):
    private, public = signing.new_key()
    monkeypatch.setattr(release_keys, "RELEASE_KEYS", (public,))
    monkeypatch.setenv("RELEASE_SIGNING_KEY", private)
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(b"0" * 64 + b"  datalab-0.2.0-py3-none-any.whl\n")
    assert script.main([str(sums)]) == 0
    signature = (tmp_path / "SHA256SUMS.sig").read_bytes()
    assert signing.verify(sums.read_bytes(), signature, [public])


def test_it_refuses_a_key_the_package_doesnt_pin(script, tmp_path, monkeypatch, capsys):
    private, _ = signing.new_key()
    monkeypatch.setenv("RELEASE_SIGNING_KEY", private)  # the placeholder is all that's pinned
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(b"x")
    assert script.main([str(sums)]) == 1
    assert "doesn't pin this signing key" in capsys.readouterr().err
    assert not (tmp_path / "SHA256SUMS.sig").exists()


def test_it_refuses_without_a_key(script, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RELEASE_SIGNING_KEY", raising=False)
    assert script.main([str(tmp_path / "SHA256SUMS")]) == 1
    assert "must be signed" in capsys.readouterr().err


def test_it_makes_a_key_pair(script, capsys):
    assert script.main(["--new-key"]) == 0
    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    private, public = lines[1], lines[3]
    assert signing.public_of(private) == public
