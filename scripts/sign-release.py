"""Sign a release's SHA256SUMS, or make the lab's release key (once).

uv run --project backend python scripts/sign-release.py --new-key
    Prints a new key pair. The private key goes into the "release" GitHub
    environment as the secret RELEASE_SIGNING_KEY, and nowhere else; the
    public key goes into backend/src/datalab/release_keys.py. See
    docs/DISTRIBUTION.md, "Setting up release signing".

RELEASE_SIGNING_KEY=… uv run --project backend python scripts/sign-release.py assets/SHA256SUMS
    The release workflow: writes assets/SHA256SUMS.sig. It refuses unless
    the package being released pins this key's public key, so the next
    release, signed with the same key, is accepted by this one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from datalab import release_keys, signing


def main(argv: list[str]) -> int:
    if argv == ["--new-key"]:
        private, public = signing.new_key()
        print(
            "Private key (the RELEASE_SIGNING_KEY secret; never commit it or paste it elsewhere):"
        )
        print(f"  {private}")
        print("Public key (paste into RELEASE_KEYS in backend/src/datalab/release_keys.py):")
        print(f"  {public}")
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    private = os.environ.get("RELEASE_SIGNING_KEY", "").strip()
    if not private:
        print(
            "RELEASE_SIGNING_KEY isn't set: releases must be signed "
            "(docs/DISTRIBUTION.md, 'Setting up release signing').",
            file=sys.stderr,
        )
        return 1
    try:
        public = signing.public_of(private)
    except signing.BadKey as error:
        print(f"RELEASE_SIGNING_KEY isn't an Ed25519 key in base64 ({error}).", file=sys.stderr)
        return 1
    if public not in release_keys.trusted_keys():
        print(
            "The package doesn't pin this signing key's public key (release_keys.py), so "
            "DataLab would refuse the next release signed with it. Add the public key first.",
            file=sys.stderr,
        )
        return 1
    sums = Path(argv[0])
    data = sums.read_bytes()
    signature = signing.sign(data, private)
    assert signing.verify(data, signature, [public])
    sums.with_name(signing.SIGNATURE).write_bytes(signature)
    print(f"Signed {sums.name} ({signing.SIGNATURE}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
