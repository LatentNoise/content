#!/usr/bin/env python3
"""Generate the README's architecture diagram, once per colour scheme.

The README opens with a picture because the first question a visitor has is
structural — *what is this thing, and where do I stand in it* — and two dense
paragraphs answer that more slowly than one drawing. The shape is the argument:
sources go in on the left, artifacts come out at the bottom, every client sits
above the engine rather than inside it, and they all reach it through one
contract.

GitHub picks between two files with `<picture media="(prefers-color-scheme:…)">`
— the only mechanism that follows the *site* theme rather than the operating
system's. So the diagram exists twice, which is exactly the kind of duplication
that rots: someone adds a client, edits the light file, ships, and the dark
file quietly keeps the old list. Hence one geometry, two palettes, and
`tests/test_readme_diagram.py` to fail when the committed SVGs go stale.

Standard library only: this runs inside the plain test venv on CI.
"""

from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

REPO = pathlib.Path(__file__).resolve().parents[1]
ASSETS = REPO / "docs" / "assets"
TARGETS = {
    "light": ASSETS / "architecture-light.svg",
    "dark": ASSETS / "architecture-dark.svg",
}

# --- the content, in one place so the two files cannot disagree ------------------

CLIENTS = (
    "HomeTube",
    "Studio",
    "Console",
    "Extension",
    "MCP server",
    "CLI",
    "Python SDK",
    "REST API",
)
SOURCES = ("a URL", "a file", "text")
STEPS = ("analyze", "plan", "run", "deliver")
ARTIFACTS = (
    "video",
    "audio",
    "subtitles",
    "transcript",
    "summary",
    "translation",
    "chapters",
    "PDF",
)
ALSO = "…and metadata, images, Markdown, keyframes"

# --- geometry --------------------------------------------------------------------
#
# One coordinate system, computed rather than typed, so a ninth client re-flows
# the row instead of overlapping the eighth.

W, H = 960, 476
MARGIN = 40
ROW_GAP = 10

ENGINE = {"x": 250, "y": 152, "w": 460, "h": 150, "rx": 14}
CLIENT_ROW_Y, CHIP_H = 38, 34
BUS_Y = 92
SOURCE_X, SOURCE_W, SOURCE_H = 60, 130, 30
ARTIFACT_ROW_Y = 380

FONT = (
    "ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Inter,Roboto,Helvetica,Arial,sans-serif"
)

PALETTES = {
    # The engine is the same blue in both schemes: it is the one element whose
    # job is to read as the centre, and a colour that flips loses that.
    "light": {
        "muted": "#64748b",
        "line": "#cbd5e1",
        "chip_fill": "#ffffff",
        "chip_stroke": "#e2e8f0",
        "chip_text": "#334155",
        "engine_fill": "#1d4ed8",
        "engine_stroke": "#1d4ed8",
        "engine_text": "#ffffff",
        "engine_sub": "#bfdbfe",
        "pill_fill": "#ffffff26",
        "pill_stroke": "#ffffff40",
        "pill_text": "#eaf1ff",
        "out_fill": "#f0fdf4",
        "out_stroke": "#bbf7d0",
        "out_text": "#166534",
    },
    "dark": {
        "muted": "#8b949e",
        "line": "#30363d",
        "chip_fill": "#161b22",
        "chip_stroke": "#30363d",
        "chip_text": "#c9d1d9",
        "engine_fill": "#1f6feb",
        "engine_stroke": "#1f6feb",
        "engine_text": "#ffffff",
        "engine_sub": "#cddffb",
        "pill_fill": "#ffffff26",
        "pill_stroke": "#ffffff40",
        "pill_text": "#eaf1ff",
        "out_fill": "#0d1f14",
        "out_stroke": "#238636",
        "out_text": "#7ee787",
    },
}


# --- primitives ------------------------------------------------------------------


def _row(count: int) -> tuple[float, float]:
    """Chip width and stride for `count` chips spanning the margins."""
    span = W - 2 * MARGIN
    width = (span - ROW_GAP * (count - 1)) / count
    return width, width + ROW_GAP


def _chip(x, y, w, h, label, fill, stroke, text, size=12.5, weight=500, rx=9):
    cx, cy = x + w / 2, y + h / 2
    return (
        f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{rx}" fill="{fill}" stroke="{stroke}"/>\n'
        f'  <text x="{cx:.1f}" y="{cy:.1f}" fill="{text}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="middle" dominant-baseline="central">'
        f"{escape(label)}</text>\n"
    )


def _label(x, y, label, fill, size=10, anchor="middle", weight=600, spacing=1.7):
    return (
        f'  <text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'font-weight="{weight}" letter-spacing="{spacing}" text-anchor="{anchor}">'
        f"{escape(label)}</text>\n"
    )


def _note(x, y, label, fill, size=11.5, anchor="middle"):
    return (
        f'  <text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'text-anchor="{anchor}">{escape(label)}</text>\n'
    )


def _line(x1, y1, x2, y2, stroke, arrow=False):
    head = ' marker-end="url(#arrow)"' if arrow else ""
    return (
        f'  <path d="M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}" stroke="{stroke}" '
        f'stroke-width="1.5" fill="none" stroke-linecap="round"{head}/>\n'
    )


# --- the drawing -----------------------------------------------------------------


def render(scheme: str) -> str:
    p = PALETTES[scheme]
    cx = W / 2
    out = []

    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" font-family="{FONT}" role="img" '
        f'aria-labelledby="title desc">\n'
        f"  <title id=\"title\">Content's architecture</title>\n"
        f'  <desc id="desc">Sources — a URL, a file, or text — enter one '
        f"self-hosted engine that analyzes, plans, runs and delivers. Every "
        f"client (HomeTube, Studio, Console, the browser extension, the MCP "
        f"server, the CLI, the SDK and the REST API) sits above the engine and "
        f"reaches it through the same public contract. Artifacts come out: "
        f"video, audio, subtitles, transcript, summary, translation, chapters "
        f"and PDF.</desc>\n"
        f"  <defs>\n"
        f'    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">\n'
        f'      <path d="M 0 0 L 10 5 L 0 10 z" fill="{p["line"]}"/>\n'
        f"    </marker>\n"
        f"  </defs>\n"
    )

    # Clients, in a row, converging on one bus rather than eight diagonals.
    out.append(_label(cx, 22, "CLIENTS", p["muted"]))
    cw, stride = _row(len(CLIENTS))
    centers = []
    for i, name in enumerate(CLIENTS):
        x = MARGIN + i * stride
        centers.append(x + cw / 2)
        out.append(
            _chip(
                x,
                CLIENT_ROW_Y,
                cw,
                CHIP_H,
                name,
                p["chip_fill"],
                p["chip_stroke"],
                p["chip_text"],
            )
        )
    for c in centers:
        out.append(_line(c, CLIENT_ROW_Y + CHIP_H, c, BUS_Y, p["line"]))
    out.append(_line(centers[0], BUS_Y, centers[-1], BUS_Y, p["line"]))
    out.append(_line(cx, BUS_Y, cx, ENGINE["y"] - 6, p["line"], arrow=True))
    out.append(
        _note(cx + 12, BUS_Y + 30, "one public contract", p["muted"], 11, "start")
    )

    # Sources, entering from the left.
    mid = ENGINE["y"] + ENGINE["h"] / 2
    src_cx = SOURCE_X + SOURCE_W / 2
    block = len(SOURCES) * SOURCE_H + (len(SOURCES) - 1) * 8
    top = mid - block / 2
    out.append(_label(src_cx, top - 16, "SOURCES", p["muted"]))
    for i, name in enumerate(SOURCES):
        out.append(
            _chip(
                SOURCE_X,
                top + i * (SOURCE_H + 8),
                SOURCE_W,
                SOURCE_H,
                name,
                p["chip_fill"],
                p["chip_stroke"],
                p["chip_text"],
                size=12,
            )
        )
    # A spine, not a single arrow off the middle chip: drawn straight, the
    # arrow left of the engine lines up with "a file" and reads as though the
    # other two go nowhere. Same manifold as the clients, for the same reason.
    spine = SOURCE_X + SOURCE_W + 18
    for i in range(len(SOURCES)):
        y = top + i * (SOURCE_H + 8) + SOURCE_H / 2
        out.append(_line(SOURCE_X + SOURCE_W, y, spine, y, p["line"]))
    out.append(_line(spine, top + SOURCE_H / 2, spine, mid + block / 2 - SOURCE_H / 2, p["line"]))
    out.append(_line(spine, mid, ENGINE["x"] - 6, mid, p["line"], arrow=True))

    # The engine.
    out.append(
        f'  <rect x="{ENGINE["x"]}" y="{ENGINE["y"]}" width="{ENGINE["w"]}" '
        f'height="{ENGINE["h"]}" rx="{ENGINE["rx"]}" fill="{p["engine_fill"]}" '
        f'stroke="{p["engine_stroke"]}"/>\n'
    )
    out.append(
        f'  <text x="{cx}" y="{ENGINE["y"] + 34}" fill="{p["engine_text"]}" '
        f'font-size="20" font-weight="650" text-anchor="middle">Content engine</text>\n'
    )
    out.append(
        _note(
            cx,
            ENGINE["y"] + 56,
            "self-hosted · the only business logic",
            p["engine_sub"],
        )
    )
    pw, pstride = (ENGINE["w"] - 48 - 12 * (len(STEPS) - 1)) / len(STEPS), 0
    pstride = pw + 12
    for i, step in enumerate(STEPS):
        out.append(
            _chip(
                ENGINE["x"] + 24 + i * pstride,
                ENGINE["y"] + 78,
                pw,
                32,
                step,
                p["pill_fill"],
                p["pill_stroke"],
                p["pill_text"],
                size=12,
                rx=8,
            )
        )

    # Artifacts, coming out at the bottom.
    out.append(
        _line(cx, ENGINE["y"] + ENGINE["h"], cx, ARTIFACT_ROW_Y - 28, p["line"], True)
    )
    out.append(
        _note(
            cx + 12,
            ENGINE["y"] + ENGINE["h"] + 26,
            "delivered where you want them",
            p["muted"],
            11,
            "start",
        )
    )
    out.append(_label(cx, ARTIFACT_ROW_Y - 12, "ARTIFACTS", p["muted"]))
    aw, astride = _row(len(ARTIFACTS))
    for i, name in enumerate(ARTIFACTS):
        out.append(
            _chip(
                MARGIN + i * astride,
                ARTIFACT_ROW_Y,
                aw,
                CHIP_H,
                name,
                p["out_fill"],
                p["out_stroke"],
                p["out_text"],
            )
        )
    out.append(_note(cx, ARTIFACT_ROW_Y + CHIP_H + 22, ALSO, p["muted"], 11))

    out.append("</svg>\n")
    return "".join(out)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for scheme, target in TARGETS.items():
        target.write_text(render(scheme))
        print(f"wrote {target.relative_to(REPO)}")


if __name__ == "__main__":
    main()
