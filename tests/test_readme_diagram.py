"""The README's diagram exists twice, and must say the same thing twice.

GitHub chooses between a light and a dark SVG with `<picture>` — the only
mechanism that follows the *site* theme rather than the operating system's. Two
files is therefore not a choice, but it is the kind of duplication that rots
silently: a ninth client gets added to the light file, ships, and the dark
readers keep seeing eight. So both are generated from one geometry
(`make readme-diagram`) and this guard fails when a committed copy is stale.

Standard library only, deliberately: the root suite runs in the plain test
venv, which carries no XML or SVG library beyond what ships with Python.
"""

from __future__ import annotations

import pathlib
import re
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from gen_readme_diagram import ARTIFACTS, CLIENTS, MARGIN, SOURCES, TARGETS, W, render

README = REPO / "README.md"


def test_the_committed_diagrams_match_their_source():
    for scheme, target in TARGETS.items():
        assert target.read_text() == render(scheme), (
            f"{target.relative_to(REPO)} is stale — the generator changed "
            "without regenerating it. Run: make readme-diagram"
        )


def test_both_schemes_carry_the_same_words():
    """The whole reason this guard exists. Colours may differ; nothing else may."""
    texts = {
        scheme: [
            (e.text or "").strip()
            for e in ET.fromstring(target.read_text()).iter(
                "{http://www.w3.org/2000/svg}text"
            )
        ]
        for scheme, target in TARGETS.items()
    }
    assert texts["light"] == texts["dark"]
    for name in (*CLIENTS, *SOURCES, *ARTIFACTS):
        assert name in texts["light"], f"{name} is drawn nowhere"


def test_no_chip_escapes_the_canvas():
    """The rows are computed, not typed, so a ninth client re-flows the row
    instead of sliding off the edge. This is what pins that."""
    for target in TARGETS.values():
        root = ET.fromstring(target.read_text())
        for rect in root.iter("{http://www.w3.org/2000/svg}rect"):
            left = float(rect.get("x", 0))
            right = left + float(rect.get("width", 0))
            assert left >= MARGIN - 0.5 and right <= W - MARGIN + 0.5, (
                f"a box spans {left}..{right}, outside the {MARGIN}px margins"
            )


def test_the_readme_offers_both_files_and_describes_the_picture():
    """A `<source>` without its `<img>` fallback renders as nothing at all on
    any surface that does not implement `<picture>` — and the alt text is the
    only version of this diagram a screen reader ever gets."""
    readme = README.read_text()
    for target in TARGETS.values():
        assert str(target.relative_to(REPO)) in readme, target

    picture = re.search(r"<picture>.*?</picture>", readme, re.DOTALL)
    assert picture, "the diagram is not published through <picture>"
    block = picture.group(0)
    assert 'media="(prefers-color-scheme: dark)"' in block
    alt = re.search(r'alt="([^"]+)"', block)
    assert alt and len(alt.group(1)) > 120, "the diagram needs a real description"
