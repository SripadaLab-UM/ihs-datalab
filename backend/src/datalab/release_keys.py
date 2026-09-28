"""The public keys a release must be signed with (signing.py).

Each installed DataLab trusts only the keys listed here, in its own package.
To change keys, a release signed with the current key lists both the
current and the next key; the release after that can be signed with the
next one (docs/DISTRIBUTION.md, "Release signing").

The lab's release key was made on 2026-09-27 by the maintainer
(`python scripts/sign-release.py --new-key`). Its private half lives only
in the app repo's "release" environment secret, RELEASE_SIGNING_KEY. With no
valid key here, a DataLab trusts none and never offers an update.
"""

from __future__ import annotations

PLACEHOLDER = "REPLACE-WITH-THE-LABS-RELEASE-PUBLIC-KEY"

RELEASE_KEYS: tuple[str, ...] = (
    # The lab's release key, 2026-09-27.
    "cXvotu05isB3gXfFtDIh05bAVYqFTOSoy8/eFYM+TfM=",
)


def trusted_keys() -> tuple[str, ...]:
    """The pinned keys, without the placeholder."""
    return tuple(key for key in RELEASE_KEYS if key and key != PLACEHOLDER)
