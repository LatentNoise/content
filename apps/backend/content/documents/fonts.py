"""Glyph coverage: can the available fonts actually draw this document?

Neither renderer answers this on its own. ReportLab silently substitutes a
notdef box; Typst exits 0 while drawing tofu. A document that renders to blank
squares while the job reports success is exactly the "plausible garbage" this
engine refuses elsewhere (D-26), so coverage is validated before a PDF is
accepted — for both renderers, from one implementation.

The cmap is parsed here with nothing but ``struct``. Borrowing ReportLab's font
machinery would have been shorter, but it would tie coverage checking to the
renderer we may *not* be using: a Typst-only deployment has no ReportLab at all.
Both cmap encodings that matter in practice are supported — format 4 (BMP) and
format 12 (full Unicode) — which together cover every modern TrueType/OpenType
font.
"""

from __future__ import annotations

import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Preferred (platformID, encodingID) pairs, best first: full-Unicode subtables
# before BMP-only ones, Windows before Unicode-platform duplicates.
_ENCODING_PREFERENCE = (
    (3, 10),  # Windows, UCS-4
    (0, 4),  # Unicode, full repertoire
    (0, 6),  # Unicode, full repertoire (newer tag)
    (3, 1),  # Windows, BMP
    (0, 3),  # Unicode, BMP
)


@dataclass
class FontCoverage:
    """The set of code points one font can draw."""

    name: str = ""
    codepoints: set[int] = field(default_factory=set)
    ranges: list[tuple[int, int]] = field(default_factory=list)

    def covers(self, codepoint: int) -> bool:
        if codepoint in self.codepoints:
            return True
        return any(start <= codepoint <= end for start, end in self.ranges)


def winansi_coverage() -> FontCoverage:
    """The base-14 PDF fonts (Helvetica, Courier…) are exactly WinAnsi.

    They carry no cmap to parse — their repertoire is defined by the encoding,
    so it is derived from cp1252 directly.
    """
    covered = set()
    for byte in range(256):
        try:
            covered.add(ord(bytes([byte]).decode("cp1252")))
        except UnicodeDecodeError:
            continue
    return FontCoverage(name="WinAnsi", codepoints=covered)


def load_coverage(path: Path) -> FontCoverage | None:
    """Read *path*'s character map. Returns None when the file is unreadable or
    uses no cmap subtable we understand — an unknown font is never silently
    treated as covering everything."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return _parse(data, path.stem)
    except (struct.error, IndexError, ValueError):
        return None


def _parse(data: bytes, name: str) -> FontCoverage | None:
    if len(data) < 12:
        return None
    tag = data[:4]
    if tag == b"ttcf":  # font collection: use the first font's directory
        (offset,) = struct.unpack_from(">I", data, 12)
        return _parse_directory(data, offset, name)
    return _parse_directory(data, 0, name)


def _parse_directory(data: bytes, base: int, name: str) -> FontCoverage | None:
    (num_tables,) = struct.unpack_from(">H", data, base + 4)
    cmap_offset = None
    for index in range(num_tables):
        record = base + 12 + index * 16
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, record)
        if tag == b"cmap":
            cmap_offset = offset
            break
    if cmap_offset is None:
        return None

    (num_subtables,) = struct.unpack_from(">H", data, cmap_offset + 2)
    subtables: dict[tuple[int, int], int] = {}
    for index in range(num_subtables):
        record = cmap_offset + 4 + index * 8
        platform, encoding, offset = struct.unpack_from(">HHI", data, record)
        subtables[(platform, encoding)] = cmap_offset + offset

    for key in _ENCODING_PREFERENCE:
        if key in subtables:
            coverage = _parse_subtable(data, subtables[key], name)
            if coverage is not None:
                return coverage
    return None


def _parse_subtable(data: bytes, offset: int, name: str) -> FontCoverage | None:
    (subtable_format,) = struct.unpack_from(">H", data, offset)
    if subtable_format == 4:
        return _parse_format4(data, offset, name)
    if subtable_format == 12:
        return _parse_format12(data, offset, name)
    return None


def _parse_format4(data: bytes, offset: int, name: str) -> FontCoverage:
    """BMP segment mapping. A code point inside a segment can still map to
    glyph 0, so the glyph id is resolved rather than assuming the range is
    solid — that distinction is the whole point of the check."""
    seg_count_x2 = struct.unpack_from(">H", data, offset + 6)[0]
    seg_count = seg_count_x2 // 2
    ends_at = offset + 14
    starts_at = ends_at + seg_count_x2 + 2
    deltas_at = starts_at + seg_count_x2
    range_offsets_at = deltas_at + seg_count_x2

    ends = struct.unpack_from(f">{seg_count}H", data, ends_at)
    starts = struct.unpack_from(f">{seg_count}H", data, starts_at)
    deltas = struct.unpack_from(f">{seg_count}h", data, deltas_at)
    range_offsets = struct.unpack_from(f">{seg_count}H", data, range_offsets_at)

    covered: set[int] = set()
    for index in range(seg_count):
        start, end = starts[index], ends[index]
        if start == 0xFFFF:
            continue  # the mandatory terminating segment
        for code in range(start, min(end, 0xFFFE) + 1):
            if range_offsets[index] == 0:
                glyph = (code + deltas[index]) & 0xFFFF
            else:
                position = (
                    range_offsets_at
                    + index * 2
                    + range_offsets[index]
                    + (code - start) * 2
                )
                if position + 2 > len(data):
                    continue
                (glyph,) = struct.unpack_from(">H", data, position)
                if glyph != 0:
                    glyph = (glyph + deltas[index]) & 0xFFFF
            if glyph != 0:
                covered.add(code)
    return FontCoverage(name=name, codepoints=covered)


def _parse_format12(data: bytes, offset: int, name: str) -> FontCoverage:
    """Full-Unicode grouped mapping. Kept as ranges rather than expanded: a CJK
    font maps tens of thousands of code points and enumerating them all would
    cost more than every other part of rendering."""
    (num_groups,) = struct.unpack_from(">I", data, offset + 12)
    ranges: list[tuple[int, int]] = []
    for index in range(num_groups):
        group = offset + 16 + index * 12
        start, end, start_glyph = struct.unpack_from(">III", data, group)
        if start_glyph != 0:
            ranges.append((start, end))
    return FontCoverage(name=name, ranges=ranges)


# --- document-level validation --------------------------------------------------


def _is_drawable_candidate(char: str) -> bool:
    """Control and format characters draw nothing by design, and whitespace is
    handled by layout rather than glyphs — neither is a coverage failure."""
    if char.isspace():
        return False
    return not unicodedata.category(char).startswith("C")


def missing_characters(text: str, coverages: list[FontCoverage]) -> list[str]:
    """Characters no available font can draw, in first-seen order.

    The union is taken across fonts because a renderer falls back between them:
    a document is only truly undrawable when *every* candidate lacks the glyph.
    """
    if not coverages:
        return []
    missing: list[str] = []
    for char in dict.fromkeys(text):
        if not _is_drawable_candidate(char):
            continue
        if not any(coverage.covers(ord(char)) for coverage in coverages):
            missing.append(char)
    return missing


def describe_missing(missing: list[str]) -> str:
    """A message an operator can act on: what is missing, and the way out."""
    sample = " ".join(f"{char!r} (U+{ord(char):04X})" for char in missing[:6])
    more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
    return (
        f"{len(missing)} character(s) cannot be drawn by any available font: "
        f"{sample}{more}. Set CONTENT_PDF_FONT to a TrueType font covering this "
        "script, or install one into the renderer's font path."
    )


# --- missing-glyph policy -------------------------------------------------------

# What to do when a document needs characters no available font can draw. The
# policy is *operator* configuration (CONTENT_PDF_MISSING_GLYPHS) and applies
# identically to every renderer, because it is resolved here rather than in a
# backend. It never appears in the public ArtifactRequest: a client asks for a
# PDF, not for a font strategy.
POLICY_ERROR = "error"
POLICY_REPLACE = "replace"
POLICY_WARN = "warn"
POLICIES = (POLICY_ERROR, POLICY_REPLACE, POLICY_WARN)
DEFAULT_POLICY = POLICY_REPLACE

# U+FFFD is the conventional "something was here" mark, but it is itself a glyph
# a font may lack — a replacement that cannot be drawn would defeat the whole
# point. '?' is in every font's repertoire and is the guaranteed floor.
REPLACEMENT = "�"
REPLACEMENT_FALLBACK = "?"


def normalize_policy(value: str) -> str:
    """An unrecognised policy falls back to the default rather than failing the
    installation: a typo in an environment variable must not take PDF output
    down, and the effective policy is reported on every affected artifact."""
    candidate = (value or "").strip().lower()
    return candidate if candidate in POLICIES else DEFAULT_POLICY


def choose_replacement(coverages: list[FontCoverage]) -> str:
    for candidate in (REPLACEMENT, REPLACEMENT_FALLBACK):
        if all(coverage.covers(ord(candidate)) for coverage in coverages):
            return candidate
    return REPLACEMENT_FALLBACK


def missing_glyph_report(
    missing: list[str], policy: str, replacement: str = ""
) -> dict:
    """The machine-readable record attached to the artifact (or the error).

    Code points rather than only characters: a reader diagnosing this needs to
    look the glyph up, and a bare '□' in a log tells them nothing.
    """
    report = {
        "policy": policy,
        "count": len(missing),
        "characters": missing[:32],
        "code_points": [f"U+{ord(char):04X}" for char in missing[:32]],
    }
    if len(missing) > 32:
        report["truncated"] = True
    if replacement:
        report["replaced_with"] = replacement
    return report
