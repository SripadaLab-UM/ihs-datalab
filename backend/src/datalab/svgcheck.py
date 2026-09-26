"""Whether an SVG is a static picture: nothing in it can run, load, or link.

An SVG exported as `.svg` is opened later outside DataLab (a browser, a
file previewer, Word), where no security policy applies. So instead of
looking for known-bad patterns, which an SVG can spell in too many ways
(SMIL `<set>`, CSS escapes, presentation attributes), the file is parsed as
XML and every element, attribute, and bit of CSS must be on a short list of
things a chart needs. Anything else, or anything unparseable, fails: the
file is then exported as text instead (exports.py).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
XML = "http://www.w3.org/XML/1998/namespace"

# Static drawing only: no <a>, animation, <script>, <foreignObject>,
# <feImage>, fonts, cursors, or views.
_ELEMENTS = {
    "svg", "g", "defs", "desc", "title", "metadata", "symbol", "use", "switch",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "image", "style",
    "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "marker",
    "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
    "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap", "feDistantLight",
    "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR",
    "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset",
    "fePointLight", "feSpecularLighting", "feSpotLight", "feTile", "feTurbulence",
}  # fmt: skip
# Metadata (as matplotlib writes it) is never drawn and never loads anything,
# so other vocabularies may appear inside <metadata>, but not SVG or XHTML.
_METADATA_NAMESPACES = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://purl.org/dc/elements/1.1/",
    "http://purl.org/dc/terms/",
    "http://creativecommons.org/ns#",
    "http://www.inkscape.org/namespaces/inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
}
# The only DOCTYPEs allowed: the standard ones, with no internal subset.
_DOCTYPE = re.compile(
    r'<!DOCTYPE\s+svg\s+PUBLIC\s+"-//W3C//DTD SVG 1\.[01]//EN"\s+'
    r'"http://www\.w3\.org/(?:TR/2001/REC-SVG-20010904/DTD/svg10|Graphics/SVG/1\.1/DTD/svg11)\.dtd"\s*>'
)
_XML_DECLARATION = re.compile(r"<\?xml\s[^?>]*\?>")
# Elements whose href may point within the file.
_REFERENCES = {"use", "textPath", "linearGradient", "radialGradient", "pattern", "filter"}
_LOCAL_REFERENCE = re.compile(r"#[A-Za-z_][\w.:-]*")
_EMBEDDED_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]*")
_URL = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)\s]*))\s*\)", re.IGNORECASE)
# CSS that could load something, however it's spelled: escapes can spell
# anything, and at-rules (@import, @font-face) are never needed by a chart.
_CSS_LOADS = re.compile(r"\\|@|image-set|\bsrc\s*\(|expression\s*\(|-moz-binding", re.IGNORECASE)


def is_static_svg(text: str) -> bool:
    if not _prologue_ok(text):
        return False
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return False
    return _local(root)[0] == "svg" and _element_ok(root, in_metadata=False)


def _prologue_ok(text: str) -> bool:
    """Only the XML declaration and a standard DOCTYPE; no other <? or <!…>."""
    rest = _XML_DECLARATION.sub("", text, count=1) if text.lstrip().startswith("<?xml") else text
    rest = _DOCTYPE.sub("", rest, count=1)
    if "<?" in rest:
        return False  # processing instructions (xml-stylesheet)
    # Comments and CDATA are fine; any other declaration (ENTITY, DOCTYPE) isn't.
    return not re.search(r"<!(?!--|\[CDATA\[)", rest)


def _local(element: ET.Element) -> tuple[str, str]:
    tag = element.tag
    if not isinstance(tag, str):
        return ("", "")
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return (name, namespace)
    return (tag, "")


def _element_ok(element: ET.Element, *, in_metadata: bool) -> bool:
    name, namespace = _local(element)
    if in_metadata:
        if namespace not in _METADATA_NAMESPACES:
            return False
        # Metadata isn't drawn; its attributes (rdf:resource=…) are only text.
        return all(_element_ok(child, in_metadata=True) for child in element)
    # Without a namespace some viewers still draw it as SVG: same rules.
    if namespace not in (SVG, "") or name not in _ELEMENTS:
        return False
    if not all(_attribute_ok(name, key, value) for key, value in element.attrib.items()):
        return False
    if name == "style":
        # A browser reads a stylesheet from the style's own text nodes,
        # including text after any child element (which ElementTree keeps as
        # that child's tail). A chart's style never has children: refuse them,
        # so the text checked is all the CSS there is.
        return len(element) == 0 and _css_ok(element.text or "")
    return all(_element_ok(child, in_metadata=(name == "metadata")) for child in element)


def _attribute_ok(element: str, key: str, value: str) -> bool:
    if key.startswith("{"):
        namespace, _, local = key[1:].partition("}")
        if namespace == XLINK and local == "href":
            return _href_ok(element, value)
        if namespace == XML and local in ("space", "lang"):
            return True
        return namespace == "http://www.inkscape.org/namespaces/inkscape" or (
            namespace == "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
        )  # editor bookkeeping, never acted on by a viewer
    lowered = key.lower()
    if lowered.startswith("on"):
        return False
    if lowered == "href":
        return _href_ok(element, value)
    if lowered in ("src", "srcset", "xml:base", "requiredextensions", "cursor", "attributename"):
        return False
    # Presentation attributes are read as CSS, so the same rules apply.
    return _css_ok(value)


def _href_ok(element: str, value: str) -> bool:
    value = value.strip()
    if _LOCAL_REFERENCE.fullmatch(value):
        return element in _REFERENCES
    return element == "image" and bool(_EMBEDDED_IMAGE.fullmatch(value))


def _css_ok(css: str) -> bool:
    if _CSS_LOADS.search(css):
        return False
    # Every url(…) must point within the file, and nothing else may say url.
    for match in _URL.finditer(css):
        target = next(group for group in match.groups() if group is not None)
        if not _LOCAL_REFERENCE.fullmatch(target.strip()):
            return False
    return len(_URL.findall(css)) == len(re.findall(r"url\s*\(", css, re.IGNORECASE))
