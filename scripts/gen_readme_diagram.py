#!/usr/bin/env python3
"""Generate the README's architecture diagram, once per colour scheme.

The README opens with a picture because the first question a visitor has is
structural — *what is this thing, and where do I stand in it* — and two dense
paragraphs answer that more slowly than one drawing. The shape is the argument:
sources feed in from the left, every client sits above the engine rather than
inside it, and artifacts come out the bottom.

GitHub picks between two files with `<picture media="(prefers-color-scheme:…)">`
— the only mechanism that follows the *site* theme rather than the operating
system's. So the diagram exists twice, which is exactly the kind of duplication
that rots: someone adds a client, edits the light file, ships, and the dark
file quietly keeps the old list. Hence one geometry, two palettes, and
`tests/test_readme_diagram.py` to fail when the committed SVGs go stale.

One rule holds the composition together, and it is the one a hand-drawn version
kept losing: **the clients, the engine and the artifacts share a single axis and
a single band of width.** Only the sources hang off to the left, because only
they are upstream. Everything fans from that axis, so the curves stay short and
symmetrical instead of sweeping across the canvas to correct an offset.

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

# --- the words, in one place so the two files cannot disagree --------------------
#
# Each is (name, what it is). The second half is what turns a row of nouns into
# something readable by somebody who has never heard of any of them.

CLIENTS = (
    ("HomeTube", "YouTube UI"),
    ("Studio", "Web UI"),
    ("Console", "Operations"),
    ("Extension", "Browser"),
    ("MCP server", "Agents"),
    ("CLI", "Terminal"),
    ("Python SDK", "Code"),
    ("REST API", "Anything"),
)
SOURCES = (
    ("URLs", "one, many, or a playlist"),
    ("Files", "one or many, uploaded"),
    ("Texts", "pasted or batched"),
)
# Plural, because `sources` is documented 1..N and a job really does carry
# several — Studio offers up to eight. The caption bounds the claim to what
# runs today: several sources, each producing its own artifacts, in one job.
# Aggregating several sources into one artifact is the `all_sources` scope,
# which is designed, documented as "one aggregated result (fan-in)", and not
# yet implemented — so the engine answers `scope_not_supported` rather than
# refusing the request.
SOURCES_NOTE = "several in a single job"
STEPS = (
    ("Analyze", "understand"),
    ("Plan", "resolve the path"),
    ("Run", "process"),
    ("Deliver", "where you want"),
)
ARTIFACTS = (
    "Video",
    "Audio",
    "Subtitles",
    "Transcript",
    "Summary",
    "Translation",
    "Chapters",
    "PDF",
)
BADGES = ("all clients · one public contract", "independent persistent jobs")
ENGINE_SUB = "self-hosted · persistent · all business logic lives here"
ALSO = "…plus metadata, images, Markdown, keyframes, thumbnails, and more."

# --- geometry --------------------------------------------------------------------
#
# Computed, never typed: a ninth client re-flows its row instead of overlapping
# the eighth, and the fan re-spreads to match.

W, H = 1280, 772
MARGIN = 40

# The shared axis. Clients, engine and artifacts all live inside this band;
# the sources column is the only thing outside it.
BAND_X0, BAND_X1 = 360, 1240
AXIS = (BAND_X0 + BAND_X1) / 2

GAP = 10
CLIENT_Y, CLIENT_H = 52, 64
ENGINE_Y, ENGINE_H = 196, 306
ENGINE_R = 44
SOURCE_X, SOURCE_W, SOURCE_H, SOURCE_GAP = MARGIN, 240, 62, 16
FAN_Y = 606
ARTIFACT_Y, ARTIFACT_H = 648, 66

FONT = (
    'Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",'
    "Roboto,Helvetica,Arial,sans-serif"
)

PALETTES = {
    # The engine keeps the same blue-to-violet in both schemes: it is the one
    # element whose job is to read as the centre, and a colour that flips loses
    # that. Everything around it inverts.
    "light": {
        "label": "#64748b",
        "card_fill": "#ffffff",
        "card_stroke": "#d7dee9",
        "card_title": "#172033",
        "card_sub": "#64748b",
        "out_fill": "#f0fdf4",
        "out_stroke": "#bbf7d0",
        "out_title": "#14532d",
        "icon_fill": "#ecfdf5",
        "icon_stroke": "#a7f3d0",
        "icon_ink": "#16a34a",
        "engine": ("#2563eb", "#4f46e5", "#7c3aed"),
        "flow": ("#22c55e", "#3b82f6", "#7c3aed"),
        "arrow": "#4f46e5",
        "shadow": ("#0f172a", 0.09),
        "engine_glow": ("#3157d5", 0.20),
    },
    "dark": {
        "label": "#8b949e",
        "card_fill": "#161b22",
        "card_stroke": "#30363d",
        "card_title": "#e6edf3",
        "card_sub": "#8b949e",
        "out_fill": "#0d1f14",
        "out_stroke": "#238636",
        "out_title": "#7ee787",
        "icon_fill": "#0d1f14",
        "icon_stroke": "#238636",
        "icon_ink": "#3fb950",
        "engine": ("#1f6feb", "#4f46e5", "#8957e5"),
        "flow": ("#3fb950", "#58a6ff", "#a371f7"),
        "arrow": "#a371f7",
        # A dark drop shadow on a dark page is invisible mud; the cards carry
        # their own border there and need no lift.
        "shadow": ("#010409", 0.0),
        "engine_glow": ("#1f6feb", 0.28),
    },
}

# Little marks that say what a source *is* without a word. Drawn at the origin
# and translated into place, so the icon and its chip cannot drift apart.
SOURCE_ICONS = {
    "URLs": "M-6 0h12 M0 -6c-3 3 -3 9 0 12 M0 -6c3 3 3 9 0 12",
    "Files": "M-5 -6h7l4 4v8h-11z M2 -6v4h4",
    "Texts": "M-6 -4h12 M-6 0h9 M-6 4h7",
}


# --- primitives ------------------------------------------------------------------


def _spread(count: int, x0: float, x1: float, gap: float) -> tuple[float, float]:
    """Item width and stride for `count` items filling x0..x1."""
    width = ((x1 - x0) - gap * (count - 1)) / count
    return width, width + gap


def _card(x, y, w, h, title, sub, p, *, out=False, rx=18):
    """A rounded card carrying a name and, under it, what the name means."""
    fill = p["out_fill"] if out else p["card_fill"]
    stroke = p["out_stroke"] if out else p["card_stroke"]
    ink = p["out_title"] if out else p["card_title"]
    cx = x + w / 2
    svg = (
        f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{rx}" fill="{fill}" stroke="{stroke}" filter="url(#lift)"/>\n'
    )
    if sub:
        svg += (
            f'  <text x="{cx:.1f}" y="{y + h / 2 - 4:.1f}" class="ct" '
            f'fill="{ink}" text-anchor="middle">{escape(title)}</text>\n'
            f'  <text x="{cx:.1f}" y="{y + h / 2 + 17:.1f}" class="cs" '
            f'fill="{p["card_sub"]}" text-anchor="middle">{escape(sub)}</text>\n'
        )
    else:
        svg += (
            f'  <text x="{cx:.1f}" y="{y + h / 2 + 6:.1f}" class="ct" '
            f'fill="{ink}" text-anchor="middle">{escape(title)}</text>\n'
        )
    return svg


def _curve(x1, y1, x2, y2, stroke, width=2.0, opacity=0.62, bend=0.55):
    """A vertical S between two points.

    Control points sit straight above and below their anchors, so a set of
    curves whose endpoints are both left-to-right ordered can never cross.
    """
    dy = (y2 - y1) * bend
    return (
        f'  <path d="M {x1:.1f} {y1:.1f} C {x1:.1f} {y1 + dy:.1f}, '
        f'{x2:.1f} {y2 - dy:.1f}, {x2:.1f} {y2:.1f}" fill="none" '
        f'stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" '
        f'opacity="{opacity}"/>\n'
    )


def _label(x, y, text, p, anchor="middle"):
    return (
        f'  <text x="{x:.1f}" y="{y:.1f}" class="lb" fill="{p["label"]}" '
        f'text-anchor="{anchor}">{escape(text)}</text>\n'
    )


# --- the drawing -----------------------------------------------------------------


def render(scheme: str) -> str:
    p = PALETTES[scheme]
    e0, e1, e2 = p["engine"]
    f0, f1, f2 = p["flow"]
    sh_color, sh_op = p["shadow"]
    gl_color, gl_op = p["engine_glow"]
    o = []

    o.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" aria-labelledby="t d">\n'
        f'  <title id="t">Content\'s architecture</title>\n'
        f'  <desc id="d">Sources — URLs, files, or texts, several of them in '
        f"a single job — feed one self-hosted Content engine that analyzes, "
        f"plans, runs and delivers. "
        f"HomeTube, Studio, Console, the browser extension, the MCP server, the "
        f"CLI, the Python SDK and the REST API all sit above the engine and "
        f"reach it through the same public contract. Artifacts come out: video, "
        f"audio, subtitles, transcript, summary, translation, chapters and "
        f"PDF.</desc>\n"
        f"  <defs>\n"
        f'    <linearGradient id="eg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{e0}"/>'
        f'<stop offset="58%" stop-color="{e1}"/>'
        f'<stop offset="100%" stop-color="{e2}"/></linearGradient>\n'
        f'    <linearGradient id="fl" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{f0}"/>'
        f'<stop offset="45%" stop-color="{f1}"/>'
        f'<stop offset="100%" stop-color="{f2}"/></linearGradient>\n'
        f'    <filter id="lift" x="-30%" y="-30%" width="160%" height="180%">'
        f'<feDropShadow dx="0" dy="6" stdDeviation="11" flood-color="{sh_color}" '
        f'flood-opacity="{sh_op}"/></filter>\n'
        f'    <filter id="glow" x="-30%" y="-30%" width="160%" height="180%">'
        f'<feDropShadow dx="0" dy="12" stdDeviation="22" flood-color="{gl_color}" '
        f'flood-opacity="{gl_op}"/></filter>\n'
        f"    <style>\n"
        f"      text {{ font-family: {FONT}; }}\n"
        f"      .lb {{ font-size: 15px; font-weight: 700; letter-spacing: .18em; }}\n"
        # text-anchor stays off these classes on purpose: a class beats the
        # presentation attribute, so putting it here silently centres every
        # left-aligned label in the sources column.
        f"      .ct {{ font-size: 17px; font-weight: 700; }}\n"
        f"      .cs {{ font-size: 13.5px; font-weight: 500; }}\n"
        f"    </style>\n"
        f"  </defs>\n"
    )

    # --- clients, on the shared band -------------------------------------------
    o.append(_label(AXIS, 30, "CLIENTS", p))
    cw, cstride = _spread(len(CLIENTS), BAND_X0, BAND_X1, GAP)
    client_cx = []
    for i, (name, sub) in enumerate(CLIENTS):
        x = BAND_X0 + i * cstride
        client_cx.append(x + cw / 2)
        o.append(_card(x, CLIENT_Y, cw, CLIENT_H, name, sub, p))

    # No badge on HomeTube. A 64px card holding two centred lines has no free
    # corner, and a mark placed anyway lands on top of the word it decorates.

    # Each client lands on its own point along the engine's top edge, in the
    # same order it sits in the row — a fan, not a knot.
    land0, land1 = BAND_X0 + 90, BAND_X1 - 90
    for i, cx in enumerate(client_cx):
        lx = land0 + i * (land1 - land0) / (len(client_cx) - 1)
        o.append(_curve(cx, CLIENT_Y + CLIENT_H, lx, ENGINE_Y, "url(#fl)"))

    # --- sources, the only thing off the axis ----------------------------------
    mid = ENGINE_Y + ENGINE_H / 2
    block = len(SOURCES) * SOURCE_H + (len(SOURCES) - 1) * SOURCE_GAP
    top = mid - block / 2
    o.append(_label(SOURCE_X + 4, top - 22, "SOURCES", p, anchor="start"))
    for i, (name, sub) in enumerate(SOURCES):
        y = top + i * (SOURCE_H + SOURCE_GAP)
        o.append(
            f'  <rect x="{SOURCE_X}" y="{y:.1f}" width="{SOURCE_W}" '
            f'height="{SOURCE_H}" rx="17" fill="{p["card_fill"]}" '
            f'stroke="{p["card_stroke"]}" filter="url(#lift)"/>\n'
            f'  <g transform="translate({SOURCE_X + 34},{y + SOURCE_H / 2:.1f})">\n'
            f'    <circle r="13" fill="{p["icon_fill"]}" '
            f'stroke="{p["icon_stroke"]}"/>\n'
            f'    <path d="{SOURCE_ICONS[name]}" fill="none" '
            f'stroke="{p["icon_ink"]}" stroke-width="1.4" stroke-linecap="round" '
            f'stroke-linejoin="round"/>\n'
            f"  </g>\n"
            f'  <text x="{SOURCE_X + 60}" y="{y + SOURCE_H / 2 - 4:.1f}" '
            f'class="ct" fill="{p["card_title"]}" text-anchor="start">'
            f"{escape(name)}</text>\n"
            f'  <text x="{SOURCE_X + 60}" y="{y + SOURCE_H / 2 + 16:.1f}" '
            f'class="cs" fill="{p["card_sub"]}" text-anchor="start">'
            f"{escape(sub)}</text>\n"
        )
        # A horizontal S into the engine's left edge, spread over its height so
        # three sources do not all point at the same spot.
        ty = mid + (i - 1) * 34
        o.append(
            f'  <path d="M {SOURCE_X + SOURCE_W + 6} {y + SOURCE_H / 2:.1f} '
            f"C {SOURCE_X + SOURCE_W + 56} {y + SOURCE_H / 2:.1f}, "
            f'{BAND_X0 - 56} {ty:.1f}, {BAND_X0 - 12} {ty:.1f}" fill="none" '
            f'stroke="{p["label"]}" stroke-width="2" stroke-linecap="round" '
            f'opacity="0.6"/>\n'
            f'  <path d="M {BAND_X0 - 2} {ty:.1f} l-11 -5.5 v11 z" '
            f'fill="{p["label"]}" opacity="0.6"/>\n'
        )
    o.append(
        f'  <text x="{SOURCE_X + 4}" y="{top + block + 26:.1f}" class="cs" '
        f'fill="{p["label"]}" text-anchor="start">{escape(SOURCES_NOTE)}</text>\n'
    )

    # --- the engine ------------------------------------------------------------
    ew = BAND_X1 - BAND_X0
    o.append(
        f'  <rect x="{BAND_X0}" y="{ENGINE_Y}" width="{ew}" height="{ENGINE_H}" '
        f'rx="{ENGINE_R}" fill="url(#eg)" filter="url(#glow)"/>\n'
        f'  <text x="{AXIS:.1f}" y="{ENGINE_Y + 62}" text-anchor="middle" '
        f'fill="#ffffff" font-size="36" font-weight="800" '
        f'letter-spacing="-0.02em">Content engine</text>\n'
        f'  <text x="{AXIS:.1f}" y="{ENGINE_Y + 92}" text-anchor="middle" '
        f'fill="#dbeafe" font-size="16" font-weight="500">'
        f"{escape(ENGINE_SUB)}</text>\n"
    )

    # Two badges: what every client shares, and what the engine does with them.
    bw, bstride = _spread(len(BADGES), BAND_X0 + 76, BAND_X1 - 76, 20)
    for i, badge in enumerate(BADGES):
        bx = BAND_X0 + 76 + i * bstride
        o.append(
            f'  <rect x="{bx:.1f}" y="{ENGINE_Y + 108}" width="{bw:.1f}" '
            f'height="34" rx="17" fill="#ffffff" fill-opacity="0.10" '
            f'stroke="#ffffff" stroke-opacity="0.22"/>\n'
        )
        for d, colour in enumerate(("#86efac", "#93c5fd", "#c4b5fd")[: 1 if i == 0 else 3]):
            o.append(
                f'  <circle cx="{bx + 22 + d * 15:.1f}" cy="{ENGINE_Y + 125}" '
                f'r="4.5" fill="{colour}"/>\n'
            )
        o.append(
            f'  <text x="{bx + bw / 2 + 14:.1f}" y="{ENGINE_Y + 130}" '
            f'text-anchor="middle" fill="#eef2ff" font-size="13.5" '
            f'font-weight="700">{escape(badge)}</text>\n'
        )

    # The four stages, joined so they read as a sequence rather than a menu.
    sw, sstride = _spread(len(STEPS), BAND_X0 + 44, BAND_X1 - 44, 22)
    sy = ENGINE_Y + 164
    for i, (name, sub) in enumerate(STEPS):
        sx = BAND_X0 + 44 + i * sstride
        o.append(
            f'  <rect x="{sx:.1f}" y="{sy}" width="{sw:.1f}" height="94" rx="20" '
            f'fill="#ffffff" fill-opacity="0.11" stroke="#ffffff" '
            f'stroke-opacity="0.22"/>\n'
            f'  <text x="{sx + sw / 2:.1f}" y="{sy + 42}" text-anchor="middle" '
            f'fill="#ffffff" font-size="17" font-weight="700">'
            f"{escape(name)}</text>\n"
            f'  <text x="{sx + sw / 2:.1f}" y="{sy + 65}" text-anchor="middle" '
            f'fill="#dce7ff" font-size="13">{escape(sub)}</text>\n'
        )
        if i:
            o.append(
                f'  <path d="M {sx - 22:.1f} {sy + 47} H {sx:.1f}" '
                f'stroke="#dce7ff" stroke-width="2" opacity="0.7"/>\n'
            )

    # --- artifacts -------------------------------------------------------------
    o.append(
        f'  <path d="M {AXIS:.1f} {ENGINE_Y + ENGINE_H} V {FAN_Y - 46:.1f}" '
        f'stroke="{p["arrow"]}" stroke-width="3.4" stroke-linecap="round" '
        f'fill="none"/>\n'
        f'  <path d="M {AXIS:.1f} {FAN_Y - 34:.1f} l-8 -13 h16 z" '
        f'fill="{p["arrow"]}"/>\n'
    )
    # The label sits above where the fan opens, not inside it.
    o.append(_label(AXIS, FAN_Y - 12, "ARTIFACTS", p))
    aw, astride = _spread(len(ARTIFACTS), BAND_X0 - 320, BAND_X1, GAP)
    for i, name in enumerate(ARTIFACTS):
        x = BAND_X0 - 320 + i * astride
        o.append(_curve(AXIS, FAN_Y, x + aw / 2, ARTIFACT_Y, "url(#fl)", 1.6, 0.45))
        o.append(_card(x, ARTIFACT_Y, aw, ARTIFACT_H, name, "", p, out=True))
    o.append(
        f'  <text x="{AXIS:.1f}" y="{ARTIFACT_Y + ARTIFACT_H + 34}" '
        f'text-anchor="middle" fill="{p["label"]}" font-size="13.5" '
        f'font-weight="600">{escape(ALSO)}</text>\n'
    )

    o.append("</svg>\n")
    return "".join(o)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for scheme, target in TARGETS.items():
        target.write_text(render(scheme))
        print(f"wrote {target.relative_to(REPO)}")


if __name__ == "__main__":
    main()
