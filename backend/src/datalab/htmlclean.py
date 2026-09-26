"""Makes agent-made HTML safe to preview.

The preview already runs with scripts off, in a sandbox, under a policy that
blocks requests to anywhere but its own folder. A few things slip past such
policies, so the page is parsed and written out again with only what a
static report needs:
- resource hints (`<link rel=dns-prefetch>`, `preconnect`) look up an outside
  name, which can carry data in it;
- links: a click would take the frame, and any data in the URL, elsewhere;
- `<meta>`, `<base>`, frames, forms, and SVG animation, which can change
  links after the fact.
Addresses outside the page's folder are dropped too, though the policy would
block them: a second layer, not the only one. (CSS can spell `url(` in ways
this doesn't catch; there the policy is what holds.)

Writing it out again (rather than deleting bits of the original) matters:
whatever this parser read as text is escaped, so the browser can't read it
as a tag instead. Duplicate attributes keep the first copy, as browsers do.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from html.parser import HTMLParser

_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
    "track", "wbr",
}  # fmt: skip
# Dropped along with everything inside them.
_DROP_WITH_CONTENT = {
    "script", "noscript", "template", "iframe", "frame", "frameset", "object", "embed",
    "applet", "portal", "fencedframe",
    # SVG animation can rewrite attributes (such as a link's href) after cleaning.
    "animate", "set", "animatemotion", "animatetransform", "animatecolor", "discard",
}  # fmt: skip
# Dropped; their content (if any) is kept.
_DROP = {"base", "meta", "link", "form", "plaintext", "xmp"}
# Links become plain text: their content stays, the link doesn't.
_UNLINK = {"a": "span", "area": "span"}
# Quantifiers are bounded: a page with many unclosed "url(" can't make these slow.
_OUTSIDE_URL = re.compile(r"url\(\s*+(?![\"']?\s*+(?:data:|#))[^)]{0,4096}\)", re.IGNORECASE)
# In a preview, CSS may still use files from the page's own folder.
_ELSEWHERE_URL = re.compile(
    r"url\(\s*[\"']?\s*(?:[a-z][a-z0-9+.-]*:(?<!data:)|[/\\])[^)]{0,4096}\)", re.IGNORECASE
)
# Renamed, not removed: an unknown at-rule is skipped up to its ";", and the
# new name can't be pieced back into "@import".
_IMPORT = re.compile(r"@import", re.IGNORECASE)
_NO_IMPORT = "@-x-removed-import"
_IMAGE_SET = re.compile(
    r"(-webkit-)?image-set\((?:[^()]|\([^()]{0,1024}\)){0,4096}\)", re.IGNORECASE
)
# CSS in an attribute that could load something.
_LOADS = re.compile(r"url\(|image-set|@import", re.IGNORECASE)
_NAMED_LOADS = re.compile(r"image-set|@import", re.IGNORECASE)
# An attribute value with a CSS url() to anywhere but the page itself ("#…").
_URL_REFERENCE = re.compile(r"url\(\s*+(?![\"']?\s*+#)", re.IGNORECASE)
# A quoted CSS string naming somewhere else (as in `@import "https://…"`).
_SCHEME = r"(?:https?|ftp|wss?|file|blob|javascript):"
_OUTSIDE_STRING = re.compile(r"([\"'])\s*(?:" + _SCHEME + r"|//)[^\"']{0,4096}\1", re.IGNORECASE)
# Whatever could still load after cleaning (too long or unclosed for the
# rules above). In an export, CSS with any of it left is dropped whole.
_STILL_LOADS = re.compile(
    r"url\(\s*+(?![\"']?\s*+(?:data:|#))|image-set|\bsrc\(|[\"']\s*+(?:" + _SCHEME + r"|//)",
    re.IGNORECASE,
)
_TEXT_ATTRIBUTES = {"alt", "title", "aria-label", "aria-description", "placeholder"}
_TAG = re.compile(r"^[a-z][a-z0-9-]*(:[a-z][a-z0-9-]*)?$")
_ATTRIBUTE = re.compile(r"^[a-z_:][a-z0-9_:.-]*$")
# Attributes that make a request or navigate that the page's policy doesn't
# govern, or that change how the page behaves.
_DROP_ATTRIBUTES = {
    "ping", "srcdoc", "formaction", "action", "http-equiv", "attributionsrc",
    "href", "xlink:href", "target", "download", "manifest", "codebase", "lowsrc",
    "dynsrc", "longdesc", "archive", "profile", "itemtype", "cite",
}  # fmt: skip
# Where an href loads an image, which the policy governs (img-src). Elsewhere
# only links within the page ("#…") are kept.
_HREF_ALLOWED = {"image", "feimage"}


# Attributes that load something by URL. In offline mode (exported reports)
# only data: URLs and in-page links survive, so the report is inert even in
# an app that ignores its security policy (Word, an email client).
_URL_ATTRIBUTES = {"src", "srcset", "poster", "background", "href", "xlink:href", "data"}


class _Cleaner(HTMLParser):
    def __init__(
        self, *, offline: bool = False, inline: Callable[[str], str | None] | None = None
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._offline = offline
        self._inline = inline
        self.out: list[str] = []
        self._skipping: list[str] = []  # open elements whose content is dropped
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> None:
        if self._skipping or tag in _DROP_WITH_CONTENT:
            if tag in _DROP_WITH_CONTENT and not closed and tag not in _VOID:
                self._skipping.append(tag)
            return
        attrs = _first_of_each(attrs)
        if tag == "link" and self._offline:
            return
        if tag == "link":
            values = dict(attrs)
            rel = (values.get("rel") or "").strip().lower()
            href = values.get("href") or ""
            if rel == "stylesheet" and href and not href.startswith("#") and not _outside(href):
                # Only the page's own styles; the policy limits them to its folder.
                self.out.append(f'<link rel="stylesheet" href="{html.escape(href, quote=True)}">')
            return
        if tag in _DROP or not _TAG.match(tag):
            return
        tag = _UNLINK.get(tag, tag)
        kept = []
        for name, value in attrs:
            if not _ATTRIBUTE.match(name) or name.startswith("on"):
                continue
            if name in ("href", "xlink:href"):
                text = (value or "").strip()
                if not (tag in _HREF_ALLOWED or text.startswith("#")):
                    continue
            elif name in _DROP_ATTRIBUTES:
                continue
            if self._offline and name in _URL_ATTRIBUTES:
                text = (value or "").strip().lower()
                if not (text.startswith("data:") or text.startswith("#")):
                    # The page's own images can come along, embedded.
                    embedded = self._inline(value or "") if self._inline and name == "src" else None
                    if embedded is None:
                        continue
                    value = embedded
            if self._offline and name not in _TEXT_ATTRIBUTES and _may_load(value or ""):
                # Exports must be inert even where no policy applies. CSS
                # escapes ("u\\72l(") can spell anything, so they go too.
                continue
            if name == "style" and value:
                value = _ELSEWHERE_URL.sub("url()", _IMPORT.sub(_NO_IMPORT, value))
            elif name not in _TEXT_ATTRIBUTES and _ELSEWHERE_URL.search(value or ""):
                continue  # SVG's fill, filter, mask, clip-path… pointing elsewhere
            if name in _URL_ATTRIBUTES and _leaves_folder(name, value or ""):
                # Blocked by the policy anyway; not even left for it to block.
                continue
            kept.append(f' {name}="{html.escape(value or "", quote=True)}"')
        # A self-closing tag stays self-closing: inside <svg> the browser would
        # otherwise nest everything after it inside it, and draw none of it.
        self.out.append(f"<{tag}{''.join(kept)}{'/' if closed and tag not in _VOID else ''}>")
        if tag == "style" and not closed:
            self._in_style = True

    def handle_endtag(self, tag: str) -> None:
        if self._skipping:
            if tag == self._skipping[-1]:
                self._skipping.pop()
            return
        if tag in _DROP_WITH_CONTENT or tag in _DROP or tag in _VOID or not _TAG.match(tag):
            return
        tag = _UNLINK.get(tag, tag)
        if tag == "style":
            self._in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        if self._in_style:
            # CSS stays as it is (its fetches obey the policy), but no "<":
            # inside <svg> a browser reads a style's text as markup.
            if self._offline and "\\" in data:
                return  # escapes could spell a load in a way the rules below miss
            css = _IMPORT.sub(_NO_IMPORT, data.replace("<", "\\3c "))
            if self._offline:
                css = _OUTSIDE_URL.sub("url()", css)
                css = _OUTSIDE_STRING.sub('""', _IMAGE_SET.sub("none", css))
                if _STILL_LOADS.search(css):
                    return  # fail closed: an export must stay inert
            else:
                css = _ELSEWHERE_URL.sub("url()", css)
            self.out.append(css)
        else:
            self.out.append(html.escape(data, quote=False))

    # Comments, doctypes, and processing instructions are all dropped.


_C0_AND_SPACE = "".join(chr(c) for c in range(0x21))


def _may_load(value: str) -> bool:
    """Whether CSS in an attribute could load something (in-page "#…" references can't)."""
    return bool(_URL_REFERENCE.search(value) or _NAMED_LOADS.search(value)) or (
        "\\" in value and "(" in value
    )


def _outside(url: str) -> bool:
    """Whether a URL leaves the page's folder: another scheme, host, or path root."""
    # Browsers ignore tabs and newlines anywhere in a URL, and control
    # characters and spaces around it.
    text = re.sub(r"[\t\n\r]", "", url).strip(_C0_AND_SPACE).lower()
    if text.startswith("data:"):
        return False
    return bool(re.match(r"[a-z][a-z0-9+.-]*:", text)) or text.startswith(("/", "\\"))


def _leaves_folder(name: str, value: str) -> bool:
    if name == "srcset":
        return any(
            _outside(part.strip().split(" ")[0]) for part in value.split(",") if part.strip()
        )
    return _outside(value)


def _first_of_each(attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Browsers keep the first of duplicate attributes; so must we."""
    seen: set[str] = set()
    kept = []
    for name, value in attrs:
        if name not in seen:
            seen.add(name)
            kept.append((name, value))
    return kept


def clean_fragment(
    source: str, *, offline: bool = False, inline: Callable[[str], str | None] | None = None
) -> str:
    """The cleaned markup alone, to place inside a page DataLab builds.

    `offline` also drops every URL that isn't a data: URL or an in-page link;
    `inline` may turn an image's `src` into a data: URL instead.
    """
    cleaner = _Cleaner(offline=offline, inline=inline)
    cleaner.feed(source)
    cleaner.close()
    return "".join(cleaner.out)


def clean_html(source: str) -> str:
    return (
        '<!doctype html>\n<meta charset="utf-8">\n'
        '<meta http-equiv="x-dns-prefetch-control" content="off">\n' + clean_fragment(source)
    )
