"""The public keys a release must be signed with (signing.py).

Each installed DataLab trusts only the keys listed here, in its own package.
To change keys, a release signed with the current key lists both the
current and the next key; the release after that can be signed with the
next one (docs/DISTRIBUTION.md, "Release signing").

PLACEHOLDER: no real key is pinned yet. Until the maintainer pastes the
lab's public key in (base64, from `python scripts/sign-release.py
--new-key`), this DataLab trusts no key, so it never offers or installs an
update; Settings → Updates says updates aren't set up.
"""

from __future__ import annotations

PLACEHOLDER = "REPLACE-WITH-THE-LABS-RELEASE-PUBLIC-KEY"

RELEASE_KEYS: tuple[str, ...] = (PLACEHOLDER,)


def trusted_keys() -> tuple[str, ...]:
    """The pinned keys, without the placeholder."""
    return tuple(key for key in RELEASE_KEYS if key and key != PLACEHOLDER)
