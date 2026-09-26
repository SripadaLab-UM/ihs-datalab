"""Judge a Content Security Policy against DataLab's browser promise.

The promise: a page in DataLab can only talk to DataLab. So the policy must
not allow scripts, connections, images, or frames from anywhere else. These
rules are written independently of the policy DataLab sends (web.py), so a
too-permissive change there is caught here.
"""

from __future__ import annotations

# For each directive: the only sources it may contain. A directive that's
# absent falls back to default-src, which must be exactly 'self'.
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
_REQUIRED = ("default-src", "script-src", "connect-src", "object-src", "frame-ancestors")


def parse(header: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in header.split(";"):
        words = part.split()
        if words:
            directives.setdefault(words[0].lower(), [w.lower() for w in words[1:]])
    return directives


def problems(header: str) -> list[str]:
    """Why this policy breaks the promise; empty if it doesn't."""
    if not header.strip():
        return ["no security policy is sent"]
    directives = parse(header)
    found = [f"{name} is missing" for name in _REQUIRED if name not in directives]
    for name, sources in directives.items():
        allowed = _ALLOWED.get(name)
        if allowed is None:
            continue  # directives that don't grant network access (sandbox, etc.)
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
