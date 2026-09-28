"""DataLab's mark, and every icon file made from it.

    uv run --with pillow python branding/build.py

The mark is "d." (the wordmark's own period) set on a square of ink, in the
paper look of docs/DESIGN.md: ink and paper, and amber only where it means
something. Practice is the same letter on paper-cream with an amber period
and edge (amber is the colour of heads-ups), so the two never look alike,
even at 16 pixels.

The geometry below is the source. This script writes the SVGs from it
(branding/*.svg, frontend/public/favicon*.svg) and draws the bitmaps with
Pillow at 4x, then scales down: PNG favicons, macOS .icns and Windows .ico.
frontend/src/app/brand.tsx draws the header's copy with the same numbers.
Run it again after changing anything here, and commit what it writes.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "frontend" / "public"
PACKAGE = REPO / "backend" / "src" / "datalab" / "branding"

# On a 64-unit square. The letter: a round bowl and a stem on its right,
# then the period, bottoms aligned.
BOWL = (26.0, 38.0, 10.5)  # centre x, centre y, radius (to the middle of the stroke)
STROKE = 6.5
STEM = (36.5 - STROKE / 2, 12.0, 36.5 + STROKE / 2, 38.0 + 10.5 + STROKE / 2)  # x0 y0 x1 y1
DOT = (47.5, 47.5, 4.25)  # centre x, centre y, radius
TILE_RADIUS = 10.0  # a favicon or Windows icon: the whole square
EDGE = 2.0  # practice's amber edge
# The real icon's bitmaps get a faint lighter rim, so the ink square still
# has an outline on a dark Dock or taskbar. (The favicon SVG turns light
# instead, in a dark browser.)
RIM, RIM_WIDTH = "#5b5850", 1.0

# The paper palette (frontend/src/styles/index.css), light and dark.
COLOURS = {
    "real": {
        "light": {"tile": "#2b2a26", "letter": "#ffffff", "dot": "#ffffff", "edge": None},
        "dark": {"tile": "#e9e6de", "letter": "#161614", "dot": "#161614", "edge": None},
    },
    "practice": {
        "light": {"tile": "#f7f0e3", "letter": "#2b2a26", "dot": "#8a5a12", "edge": "#8a5a12"},
        "dark": {"tile": "#2e2618", "letter": "#e9e6de", "dot": "#d9a95a", "edge": "#d9a95a"},
    },
}


# ------------------------------------------------------------------ SVG


def _shapes(c: dict, *, class_prefix: str = "") -> str:
    cx, cy, r = BOWL
    x0, y0, x1, y1 = STEM
    dx, dy, dr = DOT
    edge = c["edge"]
    inset = EDGE / 2 if edge else 0
    tile = (
        f'<rect class="{class_prefix}tile" x="{inset}" y="{inset}" width="{64 - 2 * inset}" '
        f'height="{64 - 2 * inset}" rx="{TILE_RADIUS - inset}" fill="{c["tile"]}"'
        + (f' stroke="{edge}" stroke-width="{EDGE}"' if edge else "")
        + "/>"
    )
    return (
        f"{tile}\n"
        f'  <circle class="{class_prefix}letter" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="{c["letter"]}" stroke-width="{STROKE}"/>\n'
        f'  <rect class="{class_prefix}letter-fill" x="{x0}" y="{y0}" width="{x1 - x0}" '
        f'height="{y1 - y0}" fill="{c["letter"]}"/>\n'
        f'  <circle class="{class_prefix}dot" cx="{dx}" cy="{dy}" r="{dr}" fill="{c["dot"]}"/>'
    )


def svg(profile: str, *, adaptive: bool) -> str:
    """The mark as SVG. `adaptive`: switches to the dark palette when the
    browser is dark (a favicon); otherwise the light one only."""
    light, dark = COLOURS[profile]["light"], COLOURS[profile]["dark"]
    title = "DataLab (practice)" if profile == "practice" else "DataLab"
    style = ""
    if adaptive:
        rules = [
            f".tile{{fill:{dark['tile']}}}",
            f".letter{{stroke:{dark['letter']}}}",
            f".letter-fill{{fill:{dark['letter']}}}",
            f".dot{{fill:{dark['dot']}}}",
        ]
        if dark["edge"]:
            rules.append(f".tile{{stroke:{dark['edge']}}}")
        style = "  <style>@media (prefers-color-scheme: dark){" + "".join(rules) + "}</style>\n"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">\n'
        f"  <title>{title}</title>\n{style}  {_shapes(light)}\n</svg>\n"
    )


# ------------------------------------------------------------------ bitmaps


def draw(profile: str, size: int, *, mac: bool = False) -> Image.Image:
    """The mark as a square RGBA bitmap, light palette. `mac`: on Apple's
    icon grid (an 824/1024 rounded body, the rest transparent)."""
    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    c = COLOURS[profile]["light"]
    if mac:
        offset, unit, radius = big * 100 / 1024, big * 824 / 1024 / 64, big * 185 / 1024
    else:
        offset, unit, radius = 0.0, big / 64, TILE_RADIUS * big / 64

    def at(v: float) -> float:
        return offset + v * unit

    body = (at(0), at(0), at(64) - 1, at(64) - 1)
    edge, width = (c["edge"], EDGE) if c["edge"] else (RIM, RIM_WIDTH)
    if c["edge"] or size >= 32:
        pen.rounded_rectangle(body, radius=radius, fill=edge)
        e = width * unit
        inner = (body[0] + e, body[1] + e, body[2] - e, body[3] - e)
        pen.rounded_rectangle(inner, radius=max(radius - e, 0), fill=c["tile"])
    else:
        pen.rounded_rectangle(body, radius=radius, fill=c["tile"])
    cx, cy, r = BOWL
    outer, inner_r = r + STROKE / 2, r - STROKE / 2
    pen.ellipse((at(cx - outer), at(cy - outer), at(cx + outer), at(cy + outer)), fill=c["letter"])
    pen.ellipse(
        (at(cx - inner_r), at(cy - inner_r), at(cx + inner_r), at(cy + inner_r)), fill=c["tile"]
    )
    x0, y0, x1, y1 = STEM
    pen.rectangle((at(x0), at(y0), at(x1), at(y1)), fill=c["letter"])
    dx, dy, dr = DOT
    pen.ellipse((at(dx - dr), at(dy - dr), at(dx + dr), at(dy + dr)), fill=c["dot"])
    return image.resize((size, size), Image.Resampling.LANCZOS)


def icns(profile: str) -> bytes:
    """With Apple's iconutil where there is one (every size, as macOS
    expects); otherwise Pillow's writer."""
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as folder:
            iconset = Path(folder) / "DataLab.iconset"
            iconset.mkdir()
            for points in (16, 32, 128, 256, 512):
                for factor, name in ((1, ""), (2, "@2x")):
                    image = draw(profile, points * factor, mac=True)
                    image.save(iconset / f"icon_{points}x{points}{name}.png")
            out = Path(folder) / "DataLab.icns"
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
            return out.read_bytes()
    buffer = io.BytesIO()
    big = draw(profile, 1024, mac=True)
    sizes = [draw(profile, s, mac=True) for s in (16, 32, 64, 128, 256, 512)]
    big.save(buffer, format="ICNS", append_images=sizes)
    return buffer.getvalue()


def ico(profile: str) -> bytes:
    buffer = io.BytesIO()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    # Each size drawn on its own, so small ones stay crisp.
    frames = [draw(profile, s) for s in sizes]
    frames[-1].save(
        buffer,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    return buffer.getvalue()


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)
    written: dict[Path, bytes] = {}
    for profile in ("real", "practice"):
        suffix = "-practice" if profile == "practice" else ""
        written[REPO / "branding" / f"datalab-mark{suffix}.svg"] = svg(
            profile, adaptive=False
        ).encode()
        written[PUBLIC / f"favicon{suffix}.svg"] = svg(profile, adaptive=True).encode()
        written[PUBLIC / f"favicon{suffix}-32.png"] = png(draw(profile, 32))
        written[PUBLIC / f"apple-touch-icon{suffix}.png"] = png(draw(profile, 180))
        written[REPO / "branding" / f"datalab-mark{suffix}-1024.png"] = png(
            draw(profile, 1024, mac=True)
        )
        written[PACKAGE / f"DataLab{suffix}.icns"] = icns(profile)
        written[PACKAGE / f"DataLab{suffix}.ico"] = ico(profile)
    for path, data in written.items():
        path.write_bytes(data)
        print(f"wrote {path.relative_to(REPO)} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
