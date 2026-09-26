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

Writing it out again (rather than deleting bits of the original) matters:
whatever this parser read as text is escaped, so the browser can't read it
as a tag instead. Duplicate attributes keep the first copy, as browsers do.
"""

from __future__ import annotations

import html
import re
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


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
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
        if tag == "link":
            values = dict(attrs)
            rel = (values.get("rel") or "").strip().lower()
            href = values.get("href") or ""
            if rel == "stylesheet" and href and not href.startswith("#"):
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
            kept.append(f' {name}="{html.escape(value or "", quote=True)}"')
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag == "style":
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
            self.out.append(data.replace("<", "\\3c "))
        else:
            self.out.append(html.escape(data, quote=False))

    # Comments, doctypes, and processing instructions are all dropped.


def _first_of_each(attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Browsers keep the first of duplicate attributes; so must we."""
    seen: set[str] = set()
    kept = []
    for name, value in attrs:
        if name not in seen:
            seen.add(name)
            kept.append((name, value))
    return kept


def clean_html(source: str) -> str:
    cleaner = _Cleaner()
    cleaner.feed(source)
    cleaner.close()
    return (
        '<!doctype html>\n<meta charset="utf-8">\n'
        '<meta http-equiv="x-dns-prefetch-control" content="off">\n' + "".join(cleaner.out)
    )
