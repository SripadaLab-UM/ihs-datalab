"""Judge a Content Security Policy against DataLab's browser promise.

The promise: a page in DataLab can only talk to DataLab. So the policy must
not allow scripts, connections, images, or frames from anywhere else. These
rules are written independently of the policy DataLab sends (web.py), so a
too-permissive change there is caught here.
"""

from __future__ import annotations

import re

# For each directive: the only sources it may contain. An absent fetch
# directive falls back to default-src, which must be exactly 'self'.
_ALLOWED: dict[str, set[str]] = {
    "default-src": {"'self'"},
    "script-src": {"'self'"},
    "connect-src": {"'self'"},
    "img-src": {"'self'", "data:", "blob:"},
    "font-src": {"'self'", "data:"},
    "style-src": {"'self'", "'unsafe-inline'"},
    "media-src": {"'self'", "data:", "blob:"},
    "frame-src": {"'self'"},
    "worker-src": {"'self'"},
    "object-src": {"'none'"},
    "base-uri": {"'none'", "'self'"},
    "form-action": {"'self'", "'none'"},
    "frame-ancestors": {"'none'", "'self'"},
}
# More specific directives override the ones above (CSP's fallback lists),
# so each is held to the rules of the one it refines.
_ALLOWED |= {
    "script-src-elem": _ALLOWED["script-src"],
    "script-src-attr": _ALLOWED["script-src"],
    "style-src-elem": _ALLOWED["style-src"],
    "style-src-attr": _ALLOWED["style-src"],
    "child-src": _ALLOWED["frame-src"],
    "fenced-frame-src": _ALLOWED["frame-src"],
    "manifest-src": {"'self'"},
    "prefetch-src": {"'self'"},
}
# Directives that grant no network access. Anything not named here or above
# is a problem: a directive this doesn't know could allow a request.
_NO_NETWORK = {
    "sandbox", "upgrade-insecure-requests", "block-all-mixed-content",
    "require-trusted-types-for", "trusted-types",
}  # fmt: skip
# form-action and frame-ancestors don't fall back to default-src, so they
# must be present themselves.
_REQUIRED = (
    "default-src", "script-src", "connect-src", "object-src", "form-action", "frame-ancestors",
)  # fmt: skip


_ASCII_SPACE = re.compile(r"[\t\n\f\r ]+")
_DIRECTIVE_NAME = re.compile(r"[a-z0-9-]+")


def _ascii_lower(text: str) -> str:
    # As browsers do: only A-Z are lowercased (Python's lower() maps more).
    return text.translate({c: c + 32 for c in range(ord("A"), ord("Z") + 1)})


def parse(header: str) -> dict[str, list[str]]:
    """Directives as a browser reads them: split on ASCII spaces, first copy wins."""
    directives: dict[str, list[str]] = {}
    for part in header.split(";"):
        words = [w for w in _ASCII_SPACE.split(part) if w]
        if words:
            directives.setdefault(_ascii_lower(words[0]), [_ascii_lower(w) for w in words[1:]])
    return directives


def _odd_names(header: str) -> list[str]:
    """Directive names a browser would skip, or repeats it would ignore: either can
    hide from a check what the browser actually enforces."""
    found, seen = [], set()
    for part in header.split(";"):
        words = [w for w in _ASCII_SPACE.split(part) if w]
        if not words:
            continue
        name = _ascii_lower(words[0])
        if not _DIRECTIVE_NAME.fullmatch(name):
            found.append(f"{name!r} isn't a valid directive name")
        elif name in seen:
            found.append(f"{name} appears more than once")
        seen.add(name)
    return found


def problems(header: str) -> list[str]:
    """Why this policy breaks the promise; empty if it doesn't."""
    if not header.strip():
        return ["no security policy is sent"]
    directives = parse(header)
    found = _odd_names(header)
    found += [f"{name} is missing" for name in _REQUIRED if name not in directives]
    for name, sources in directives.items():
        if name in _NO_NETWORK:
            continue
        allowed = _ALLOWED.get(name)
        if allowed is None:
            # report-uri and report-to send reports, which name blocked
            # addresses, somewhere: not allowed either.
            found.append(f"{name} isn't a directive this check knows is safe")
            continue
        extra = [s for s in sources if s not in allowed]
        if extra:
            found.append(f"{name} allows {' '.join(extra)}")
        if not sources:
            found.append(f"{name} is empty")
    return found


def preview_problems(header: str, folder: str) -> list[str]:
    """Why a preview's policy would let its page contact anything; empty if it wouldn't.

    A preview may load only files from its own folder, and must be sandboxed
    with no allowances (no scripts, forms, popups, or navigation).
    """
    if not header.strip():
        return ["no security policy is sent"]
    directives = parse(header)
    found = []
    if "sandbox" not in directives:
        found.append("the page isn't sandboxed")
    elif directives["sandbox"]:
        found.append(f"the sandbox allows {' '.join(directives['sandbox'])}")
    if directives.get("default-src") != ["'none'"]:
        found.append("default-src isn't 'none'")
    for name in ("form-action", "base-uri"):
        if directives.get(name) != ["'none'"]:
            found.append(f"{name} isn't 'none'")
    own = {folder.lower(), "data:", "'none'"}
    for name, sources in directives.items():
        if name in ("sandbox", "frame-ancestors"):
            continue
        allowed = own | ({"'unsafe-inline'"} if name == "style-src" else set())
        extra = [s for s in sources if s not in allowed]
        if extra:
            found.append(f"{name} allows {' '.join(extra)}")
    return found
