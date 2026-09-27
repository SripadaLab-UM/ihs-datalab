"""Release signatures: Ed25519 over a release's SHA256SUMS.

The release workflow signs `SHA256SUMS` with the lab's release key, which
lives only in the "release" GitHub environment (`RELEASE_SIGNING_KEY`), and
publishes the signature as `SHA256SUMS.sig`. The updater checks it against
the public keys pinned in the installed package (release_keys.py) before it
trusts anything the release lists. See docs/DISTRIBUTION.md, "Release
signing".

Keys are the raw 32 bytes, base64-encoded; a signature is the raw 64 bytes,
base64-encoded, on one line. What is signed is a fixed prefix and then the
exact bytes of SHA256SUMS, so a signature made for anything else never
passes as one for a release.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNATURE = "SHA256SUMS.sig"
_CONTEXT = b"DataLab release SHA256SUMS, v1\n"


class BadKey(ValueError):
    """A key that isn't a base64 Ed25519 key."""


def _decode(text: str, size: int) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise BadKey("not base64") from None
    if len(raw) != size:
        raise BadKey(f"expected {size} bytes, got {len(raw)}")
    return raw


def public_key(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_decode(text, 32))


def valid_public(text: str) -> bool:
    """Whether `text` is a base64 Ed25519 public key (32 bytes)."""
    try:
        public_key(text)
    except (BadKey, ValueError):
        return False
    return True


def verify(sums: bytes, signature: bytes, keys: Iterable[str]) -> bool:
    """Whether `signature` (SHA256SUMS.sig's contents) is one of `keys`'s over `sums`."""
    try:
        raw = _decode(signature.decode("ascii", "strict"), 64)
    except (BadKey, UnicodeDecodeError):
        return False
    for key in keys:
        try:
            public_key(key).verify(raw, _CONTEXT + sums)
        except (BadKey, InvalidSignature, ValueError):
            continue
        return True
    return False


def sign(sums: bytes, private: str) -> bytes:
    """SHA256SUMS.sig's contents, for the release workflow (scripts/sign-release.py)."""
    key = Ed25519PrivateKey.from_private_bytes(_decode(private, 32))
    return base64.b64encode(key.sign(_CONTEXT + sums)) + b"\n"


def public_of(private: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(_decode(private, 32))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def new_key() -> tuple[str, str]:
    """A new key pair (private, public), base64. For the maintainer's one-time setup."""
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    private = base64.b64encode(raw).decode()
    return private, public_of(private)
