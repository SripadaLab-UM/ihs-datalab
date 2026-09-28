"""DataLab's logo, and every icon file made from it.

    uv run --with pillow python branding/build.py
    uv run --with pillow python branding/build.py --options <folder>   # the AI-mark options, side by side

Two marks, always side by side and never merged (U-M's brand rules forbid
altering the Block M or combining it with other shapes):

- The Block M, the University's mark, as the prototype used it
  (um-gpt-local-proxy/deploy/cognito/logo.png, traced here to its polygon):
  Maize (#FFCB05) on Blue (#00274C), with clear space round it of at least
  the height of its serifs.
- The IHS + AI mark: "IHS" with an AI cue. AI_MARK picks which of the three
  designs below is used (a one-line change); `--options` draws all three.

The app icon is Blue with the Block M and, from 64 pixels up, the IHS mark
under it; below 64 pixels (and in the favicon) the Block M alone, as the IHS
letters can't be read that small. Practice is the same icon inverted: Blue on
Maize, so the two never look alike, even at 16 pixels.

This script writes the SVGs (branding/*.svg, frontend/public/favicon*.svg),
the header's copy of the geometry (frontend/src/app/brandArt.ts), and draws
the bitmaps with Pillow at 4x, then scales down: PNG favicons, macOS .icns and
Windows .ico. Run it again after changing anything here, and commit what it
writes.
"""

from __future__ import annotations

import argparse
import io
import math
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "frontend" / "public"
PACKAGE = REPO / "backend" / "src" / "datalab" / "branding"
BRAND_TS = REPO / "frontend" / "src" / "app" / "brandArt.ts"

MAIZE = "#FFCB05"
BLUE = "#00274C"

# Which IHS + AI mark the app uses: "spark", "network" or "pulse".
AI_MARK = "spark"

# ------------------------------------------------------------------ geometry

# The Block M, traced from the prototype's logo (a 132 x 104 box). Symmetric
# about x = 66: the slab serifs, the two legs and the V between them.
BLOCK_M_SIZE = (132.0, 104.0)
BLOCK_M = [
    (0, 0), (46, 0), (66, 52), (86, 0), (132, 0), (132, 24), (119, 24), (119, 82),
    (132, 82), (132, 104), (81, 104), (81, 82), (92, 82), (92, 30.5), (72.5, 82),
    (59.5, 82), (40, 30.5), (40, 82), (51, 82), (51, 104), (0, 104), (0, 82),
    (13, 82), (13, 24), (0, 24),
]  # fmt: skip
SERIF = 22.0  # the serifs' height: the least clear space round the M

# A shape is a tuple: ("poly", points) | ("arc", cx, cy, r, start, end, width)
# | ("line", points, width) | ("dot", cx, cy, r) | ("spark", cx, cy, r).
# Angles are degrees clockwise from three o'clock (y points down).
Shape = tuple[Any, ...]


def _rect(x: float, y: float, w: float, h: float) -> Shape:
    return ("poly", [(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def _ihs_block(stroke: float = 4.5) -> tuple[list[Shape], float]:
    """Solid "IHS", 20 high: I, H, and an S of two stacked arcs."""
    shapes: list[Shape] = [_rect(0, 0, stroke, 20)]
    h = stroke + 3
    shapes += [
        _rect(h, 0, stroke, 20),
        _rect(h + 14 - stroke, 0, stroke, 20),
        _rect(h, 10 - stroke / 2, 14, stroke),
    ]
    r = (20 - stroke) / 4  # the bowls' radius, to the stroke's middle
    s = h + 14 + 3
    cx = s + r + stroke / 2
    top, bottom = stroke / 2 + r, stroke / 2 + 3 * r
    shapes += [
        ("arc", cx, top, r, 90, 330, stroke),
        ("arc", cx, bottom, r, 270, 510, stroke),
    ]
    return shapes, s + 2 * r + stroke


def _spark() -> tuple[list[Shape], tuple[float, float]]:
    """Option 1: "IHS" with a four-point spark (the usual AI cue) as its
    superscript."""
    letters, width = _ihs_block()
    shapes = letters + [("spark", width + 6.5, 4.5, 4.5)]
    return shapes, (width + 11.0, 20.0)


def _network() -> tuple[list[Shape], tuple[float, float]]:
    """Option 2: "IHS" drawn as a small network: lines between nodes."""
    w, r = 2.2, 2.3
    i = [(2.5, 2.5), (2.5, 17.5)]
    h1, h2, bar = [(9, 2.5), (9, 17.5)], [(20, 2.5), (20, 17.5)], [(9, 10), (20, 10)]
    s = [(37, 2.5), (27, 2.5), (27, 10), (37, 10), (37, 17.5), (26.5, 17.5)]
    shapes: list[Shape] = [("line", i, w), ("line", h1, w), ("line", h2, w), ("line", bar, w)]
    shapes.append(("line", s, w))
    for point in [*i, *h1, *h2, *bar, *s]:
        shapes.append(("dot", point[0], point[1], r))
    return shapes, (39.5, 20.0)


def _pulse() -> tuple[list[Shape], tuple[float, float]]:
    """Option 3: a heartbeat line (the study follows interns' sleep, mood and
    steps) that ends in a spark."""
    line = [(1.5, 11), (10, 11), (13.5, 3), (18.5, 19), (22.5, 7), (25, 11), (29, 11)]
    return [("line", line, 3.0), ("spark", 35.5, 11, 6.5)], (42.0, 22.0)


MARKS: dict[str, Callable[[], tuple[list[Shape], tuple[float, float]]]] = {
    "spark": _spark,
    "network": _network,
    "pulse": _pulse,
}

# The icon, on a 64-unit square.
TILE_RADIUS = 12.0
M_ALONE = 44.0  # the M's width when it's alone (small sizes, the favicon)
M_WITH = 34.0  # ... and above the IHS mark
MARK_WIDTH = 30.0  # the IHS mark's width under it
GAP = 6.5  # between them: more than the M's serif height at this size
LOCKUP_FROM = 64  # pixels: below this the icon is the Block M alone

COLOURS = {
    # tile, Block M, IHS mark
    "real": (BLUE, MAIZE, MAIZE),
    "practice": (MAIZE, BLUE, BLUE),
}


def _spark_points(cx: float, cy: float, r: float, steps: int = 12) -> list[tuple[float, float]]:
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    waist = 0.12 * r
    corners = [(1, -1), (1, 1), (-1, 1), (-1, -1)]
    points: list[tuple[float, float]] = []
    for k in range(4):
        a, b = tips[k], tips[(k + 1) % 4]
        c = (cx + corners[k][0] * waist, cy + corners[k][1] * waist)
        for n in range(steps):
            t = n / steps
            points.append(
                (
                    (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t**2 * b[0],
                    (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t**2 * b[1],
                )
            )
    return points


# ------------------------------------------------------------------ SVG


def _n(v: float) -> str:
    return f"{round(v, 3):g}"


def _polar(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    return cx + r * math.cos(math.radians(deg)), cy + r * math.sin(math.radians(deg))


def svg_paths(shapes: Sequence[Shape]) -> list[dict[str, Any]]:
    """Each shape as an SVG path: {"d": ..., "stroke": width or None, "cap": ...}."""
    out: list[dict[str, Any]] = []
    for shape in shapes:
        kind = shape[0]
        if kind == "poly":
            pts = shape[1]
            d = "M" + " ".join(f"{_n(x)} {_n(y)}" for x, y in pts) + "Z"
            out.append({"d": d, "stroke": None})
        elif kind == "arc":
            _, cx, cy, r, start, end, width = shape
            x0, y0 = _polar(cx, cy, r, start)
            x1, y1 = _polar(cx, cy, r, end)
            large = 1 if (end - start) % 360 > 180 else 0
            d = f"M{_n(x0)} {_n(y0)}A{_n(r)} {_n(r)} 0 {large} 1 {_n(x1)} {_n(y1)}"
            out.append({"d": d, "stroke": width, "cap": "butt"})
        elif kind == "line":
            pts, width = shape[1], shape[2]
            d = "M" + "L".join(f"{_n(x)} {_n(y)}" for x, y in pts)
            out.append({"d": d, "stroke": width, "cap": "round"})
        elif kind == "dot":
            _, cx, cy, r = shape
            d = (
                f"M{_n(cx - r)} {_n(cy)}A{_n(r)} {_n(r)} 0 1 0 {_n(cx + r)} {_n(cy)}"
                f"A{_n(r)} {_n(r)} 0 1 0 {_n(cx - r)} {_n(cy)}Z"
            )
            out.append({"d": d, "stroke": None})
        elif kind == "spark":
            _, cx, cy, r = shape
            tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
            waist = 0.12 * r
            corners = [(1, -1), (1, 1), (-1, 1), (-1, -1)]
            d = f"M{_n(tips[0][0])} {_n(tips[0][1])}"
            for k in range(4):
                b = tips[(k + 1) % 4]
                c = (cx + corners[k][0] * waist, cy + corners[k][1] * waist)
                d += f"Q{_n(c[0])} {_n(c[1])} {_n(b[0])} {_n(b[1])}"
            out.append({"d": d + "Z", "stroke": None})
    return out


def _svg_elements(shapes: Sequence[Shape], colour: str) -> str:
    parts = []
    for p in svg_paths(shapes):
        if p["stroke"] is None:
            parts.append(f'<path d="{p["d"]}" fill="{colour}"/>')
        else:
            join = ' stroke-linejoin="round"' if p["cap"] == "round" else ""
            parts.append(
                f'<path d="{p["d"]}" fill="none" stroke="{colour}" stroke-width="{_n(p["stroke"])}" '
                f'stroke-linecap="{p["cap"]}"{join}/>'
            )
    return "".join(parts)


def _block_m_shapes() -> list[Shape]:
    return [("poly", BLOCK_M)]


def _placements(mark: str, lockup: bool) -> list[tuple[list[Shape], float, float, float]]:
    """What goes on the 64-unit tile: (shapes, scale, x, y) for each mark."""
    mw, mh = BLOCK_M_SIZE
    if not lockup:
        scale = M_ALONE / mw
        return [(_block_m_shapes(), scale, (64 - M_ALONE) / 2, (64 - mh * scale) / 2)]
    shapes, (aw, ah) = MARKS[mark]()
    m_scale, a_scale = M_WITH / mw, MARK_WIDTH / aw
    total = mh * m_scale + GAP + ah * a_scale
    top = (64 - total) / 2
    return [
        (_block_m_shapes(), m_scale, (64 - M_WITH) / 2, top),
        (shapes, a_scale, (64 - MARK_WIDTH) / 2, top + mh * m_scale + GAP),
    ]


def icon_svg(profile: str, *, mark: str = AI_MARK, lockup: bool = True) -> str:
    tile, m_colour, a_colour = COLOURS[profile]
    title = "DataLab (practice)" if profile == "practice" else "DataLab"
    body = [f'<rect width="64" height="64" rx="{_n(TILE_RADIUS)}" fill="{tile}"/>']
    for i, (shapes, scale, x, y) in enumerate(_placements(mark, lockup)):
        colour = m_colour if i == 0 else a_colour
        body.append(
            f'<g transform="translate({_n(x)} {_n(y)}) scale({_n(scale)})">'
            f"{_svg_elements(shapes, colour)}</g>"
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">\n'
        f"  <title>{title}</title>\n  " + "\n  ".join(body) + "\n</svg>\n"
    )


def mark_svg(shapes: Sequence[Shape], size: tuple[float, float], colour: str, title: str) -> str:
    w, h = size
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(w)} {_n(h)}" '
        f'width="{_n(w * 4)}" height="{_n(h * 4)}">\n  <title>{title}</title>\n  '
        f"{_svg_elements(shapes, colour)}\n</svg>\n"
    )


def brand_ts(mark: str) -> str:
    """The header's copy of the geometry (frontend/src/app/brand.tsx)."""
    shapes, (aw, ah) = MARKS[mark]()
    m = svg_paths(_block_m_shapes())[0]["d"]
    paths = []
    for p in svg_paths(shapes):
        if p["stroke"] is None:
            paths.append(f'    {{ d: "{p["d"]}" }},')
        else:
            paths.append(f'    {{ d: "{p["d"]}", stroke: {_n(p["stroke"])}, cap: "{p["cap"]}" }},')
    mw, mh = BLOCK_M_SIZE
    [(_, ts, tx, ty)] = _placements(mark, lockup=False)
    return (
        "// Written by branding/build.py: change the geometry there and run it again.\n\n"
        'export type ArtPath = { d: string; stroke?: number; cap?: "butt" | "round" };\n\n'
        "/** The Block M, the University's mark (never altered or merged with another shape). */\n"
        f'export const BLOCK_M = {{ width: {_n(mw)}, height: {_n(mh)}, d: "{m}" }};\n\n'
        "/** The icon's tile, 64 units square: the Block M alone on it, as in the favicon.\n"
        " * Real: Maize on Blue; practice: Blue on Maize. */\n"
        f"export const TILE = {{ radius: {_n(TILE_RADIUS)}, x: {_n(tx)}, y: {_n(ty)}, scale: {_n(ts)} }};\n"
        f'export const COLOURS = {{ maize: "{MAIZE}", blue: "{BLUE}" }};\n\n'
        f"/** The IHS + AI mark ({mark!r} in branding/build.py). */\n"
        f"export const IHS_MARK: {{ name: string; width: number; height: number; paths: ArtPath[] }} = {{\n"
        f'  name: "{mark}",\n  width: {_n(aw)},\n  height: {_n(ah)},\n  paths: [\n'
        + "\n".join(paths)
        + "\n  ],\n};\n"
    )


# ------------------------------------------------------------------ bitmaps


def _paint(
    pen: ImageDraw.ImageDraw,
    shapes: Sequence[Shape],
    colour: str,
    at: Callable[[float, float], tuple[float, float]],
    unit: float,
) -> None:
    for shape in shapes:
        kind = shape[0]
        if kind == "poly":
            pen.polygon([at(x, y) for x, y in shape[1]], fill=colour)
        elif kind == "arc":
            _, cx, cy, r, start, end, width = shape
            x, y = at(cx, cy)
            outer = (r + width / 2) * unit
            pen.arc(
                (x - outer, y - outer, x + outer, y + outer),
                start,
                end,
                fill=colour,
                width=max(1, round(width * unit)),
            )
        elif kind == "line":
            pts, width = shape[1], shape[2]
            pen.line(
                [at(x, y) for x, y in pts],
                fill=colour,
                width=max(1, round(width * unit)),
                joint="curve",
            )
            for x, y in (pts[0], pts[-1]):
                px, py = at(x, y)
                half = width * unit / 2
                pen.ellipse((px - half, py - half, px + half, py + half), fill=colour)
        elif kind == "dot":
            _, cx, cy, r = shape
            x, y = at(cx, cy)
            pen.ellipse((x - r * unit, y - r * unit, x + r * unit, y + r * unit), fill=colour)
        elif kind == "spark":
            _, cx, cy, r = shape
            pen.polygon([at(x, y) for x, y in _spark_points(cx, cy, r)], fill=colour)


def draw(
    profile: str, size: int, *, mac: bool = False, mark: str = AI_MARK, lockup: bool | None = None
) -> Image.Image:
    """The icon as a square RGBA bitmap. `mac`: on Apple's icon grid (an
    824/1024 rounded body, the rest transparent). `lockup`: the IHS mark under
    the M (by default from LOCKUP_FROM pixels up)."""
    if lockup is None:
        lockup = size >= LOCKUP_FROM
    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    tile, m_colour, a_colour = COLOURS[profile]
    if mac:
        offset, unit, radius = big * 100 / 1024, big * 824 / 1024 / 64, big * 185 / 1024
    else:
        offset, unit, radius = 0.0, big / 64, TILE_RADIUS * big / 64
    pen.rounded_rectangle(
        (offset, offset, offset + 64 * unit - 1, offset + 64 * unit - 1), radius=radius, fill=tile
    )
    for i, (shapes, s, x, y) in enumerate(_placements(mark, lockup)):

        def at(u: float, v: float, s: float = s, x: float = x, y: float = y) -> tuple[float, float]:
            return offset + (x + u * s) * unit, offset + (y + v * s) * unit

        _paint(pen, shapes, m_colour if i == 0 else a_colour, at, s * unit)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def draw_art(
    shapes: Sequence[Shape], size: tuple[float, float], colour: str, height: int
) -> Image.Image:
    """A mark on its own, transparent, `height` pixels high."""
    w, h = size
    scale = 4
    unit = height * scale / h
    image = Image.new("RGBA", (max(1, round(w * unit)), height * scale), (0, 0, 0, 0))
    _paint(ImageDraw.Draw(image), shapes, colour, lambda u, v: (u * unit, v * unit), unit)
    return image.resize((max(1, round(w * height / h)), height), Image.Resampling.LANCZOS)


def icns(profile: str) -> bytes:
    """With Apple's iconutil where there is one (every size, as macOS
    expects); otherwise Pillow's writer."""
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as folder:
            iconset = Path(folder) / "DataLab.iconset"
            iconset.mkdir()
            for points in (16, 32, 128, 256, 512):
                for factor, name in ((1, ""), (2, "@2x")):
                    # By the size it's seen at (points), so a 16-point icon
                    # on a Retina screen is still the M alone.
                    image = draw(profile, points * factor, mac=True, lockup=points >= LOCKUP_FROM)
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


# ------------------------------------------------------------------ options


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (
        "/System/Library/Fonts/Supplemental/Iowan Old Style.ttc",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/Library/Fonts/Georgia.ttf",
        "C:/Windows/Fonts/georgia.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def preview(mark: str) -> Image.Image:
    """A sheet for choosing the IHS mark: the icons, the mark alone and the
    header, at 16, 32, 64 and 256 pixels, on light and on dark."""
    shapes, size = MARKS[mark]()
    sizes = (16, 32, 64, 256)
    row_h, pad = 300, 30
    sheet = Image.new("RGB", (2060, 2 * row_h + 70), "#ffffff")
    pen = ImageDraw.Draw(sheet)
    small, label = _font(15), _font(22)
    pen.text(
        (pad, 18), f"IHS + AI mark: {mark!r}  (branding/build.py AI_MARK)", fill=BLUE, font=label
    )
    for band, (bg, fg, name) in enumerate(
        ((("#ffffff", BLUE, "light")), ("#161614", MAIZE, "dark"))
    ):
        top = 60 + band * row_h
        pen.rectangle((0, top, sheet.width, top + row_h), fill=bg)
        text = "#5c6b7a" if name == "light" else "#aab4c0"
        x = pad
        for title, make in (
            ("app icon", lambda s: draw("real", s, mark=mark)),
            ("practice", lambda s: draw("practice", s, mark=mark)),
            ("mark alone", lambda s, fg=fg: draw_art(shapes, size, fg, max(8, round(s * 0.5)))),
        ):
            pen.text((x, top + 12), title, fill=text, font=small)
            cx = x
            for s in sizes:
                image = make(s)
                y = top + 40 + (256 - image.height) // 2 if s == 256 else top + 40
                sheet.paste(image, (cx, y), image)
                if s == 16:  # and the 16 enlarged, to see its pixels
                    zoom = image.resize(
                        (image.width * 4, image.height * 4), Image.Resampling.NEAREST
                    )
                    sheet.paste(zoom, (cx, top + 70), zoom)
                cx += (image.width if s != 16 else 64) + 14
            x = cx + 30
        # The header, at twice its size: the icon's tile (the Block M alone,
        # 22 px, where the "d." was), the IHS mark (Blue on light, Maize on
        # dark) and the "DataLab" wordmark in the paper's ink, unchanged.
        hx, hy = x, top + 40
        pen.text((hx, top + 12), "header (2x)", fill=text, font=small)
        pen.rectangle((hx, hy, hx + 360, hy + 84), fill=bg, outline=text)
        tile = draw("real", 44, lockup=False)
        sheet.paste(tile, (hx + 20, hy + 20), tile)
        a = draw_art(shapes, size, fg, 22)
        sheet.paste(a, (hx + 20 + 44 + 14, hy + 31), a)
        ink = "#2b2a26" if name == "light" else "#e9e6de"
        pen.text((hx + 20 + 44 + 14 + a.width + 16, hy + 20), "DataLab", fill=ink, font=_font(40))
    return sheet


# ------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--options", type=Path, help="write the three IHS marks and their preview sheets here"
    )
    args = parser.parse_args()
    if args.options:
        args.options.mkdir(parents=True, exist_ok=True)
        for n, name in enumerate(MARKS, start=1):
            shapes, size = MARKS[name]()
            (args.options / f"option{n}-{name}.svg").write_text(
                mark_svg(shapes, size, BLUE, f"IHS mark: {name}")
            )
            (args.options / f"option{n}-{name}-icon.svg").write_text(icon_svg("real", mark=name))
            preview(name).save(args.options / f"option{n}-{name}-preview.png")
            print(f"wrote {args.options}/option{n}-{name}*")
        return
    PUBLIC.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)
    shapes, size = MARKS[AI_MARK]()
    written: dict[Path, bytes] = {
        REPO / "branding" / "block-m.svg": mark_svg(
            _block_m_shapes(), BLOCK_M_SIZE, MAIZE, "Block M"
        ).encode(),
        REPO / "branding" / "ihs-mark.svg": mark_svg(shapes, size, BLUE, "IHS").encode(),
        BRAND_TS: brand_ts(AI_MARK).encode(),
    }
    for profile in ("real", "practice"):
        suffix = "-practice" if profile == "practice" else ""
        written[REPO / "branding" / f"datalab-mark{suffix}.svg"] = icon_svg(profile).encode()
        # The favicon is the Block M alone: it reads at 16 pixels, and the
        # same in a light or dark browser (it brings its own Blue).
        written[PUBLIC / f"favicon{suffix}.svg"] = icon_svg(profile, lockup=False).encode()
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
