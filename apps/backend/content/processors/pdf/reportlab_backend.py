"""ReportLab implementation of ``document.render_pdf``.

The supported fallback, not a stepping stone: it is pure Python, needs no
external binary and no particular architecture, so it keeps PDF output working
on any installation where Typst cannot be shipped.

Chosen over the lighter fpdf2 because it is BSD-licensed — an LGPL dependency
would attach obligations to the commercial licence Content deliberately keeps
open (COMMERCIAL.md).

**Determinism**: PDFs normally embed a creation timestamp, which would make the
same input produce different bytes on every run. ``invariant=1`` fixes most of
it and the remaining date is derived from the content hash, so the operation is
genuinely reproducible — the registry declares it deterministic and that must
be true.
"""

from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import re
from pathlib import Path

from content.documents.fonts import FontCoverage, load_coverage, winansi_coverage
from content.documents.model import (
    BULLETS,
    CODE,
    HEADING,
    NUMBERED,
    PARAGRAPH,
    QUOTE,
    RULE,
    Document,
    Span,
)
from content.processors.pdf.base import BasePdfProcessor
from content.providers.base import ExecutionContext

RENDERER_NAME = "content.pdf.reportlab"

# Candidate Unicode fonts, in preference order. Only consulted when the text
# needs characters the base-14 fonts cannot draw.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",  # Alpine ttf-dejavu
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _inline(spans: tuple[Span, ...]) -> str:
    """Model spans → ReportLab's mini-HTML.

    The span text is escaped, never interpreted: it comes from a web page or an
    LLM, and a stray ``<`` would otherwise corrupt the markup or the page.
    """
    out = []
    for span in spans:
        text = html.escape(span.text, quote=False)
        if span.code:
            text = f'<font face="Courier">{text}</font>'
        if span.bold:
            text = f"<b>{text}</b>"
        if span.italic:
            text = f"<i>{text}</i>"
        if span.href:
            # Keep the label visible and clickable; a bare URL helps nobody in
            # print, and losing the destination entirely would be worse.
            target = html.escape(span.href, quote=True)
            text = f'<link href="{target}" color="#1a4fa0">{text}</link>'
        out.append(text)
    return "".join(out)


def document_to_flowables(document: Document, styles: dict) -> list:
    """Translate the neutral model into ReportLab flowables.

    Free of any step/context notion so it is directly testable, and so the
    mapping is exercised without running a job.
    """
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        Preformatted,
        Spacer,
    )

    flowables: list = []
    for block in document.blocks:
        if block.kind == HEADING:
            level = min(max(block.level, 1), 4)
            flowables.append(Paragraph(_inline(block.spans), styles[f"h{level}"]))
        elif block.kind == PARAGRAPH:
            flowables.append(Paragraph(_inline(block.spans), styles["body"]))
        elif block.kind == QUOTE:
            flowables.append(Paragraph(_inline(block.spans), styles["quote"]))
        elif block.kind in (BULLETS, NUMBERED):
            flowables.append(
                ListFlowable(
                    [
                        ListItem(Paragraph(_inline(item), styles["body"]))
                        for item in block.items
                    ],
                    bulletType="1" if block.kind == NUMBERED else "bullet",
                    leftIndent=18,
                )
            )
        elif block.kind == CODE:
            flowables.append(Preformatted(block.text, styles["code"], maxLineLength=90))
        elif block.kind == RULE:
            flowables.append(Spacer(1, 6))
            flowables.append(HRFlowable(width="100%", thickness=0.6, color="#c8c8c8"))
            flowables.append(Spacer(1, 6))
    return flowables


class ReportLabPdfProcessor(BasePdfProcessor):
    name = RENDERER_NAME

    def __init__(self, font_path: str = ""):
        super().__init__(font_path)
        self._available: bool | None = None

    # --- availability ----------------------------------------------------------

    def available(self) -> bool:
        """The optional ``pdf`` extra is installed. Probed once — a Python
        library does not appear or vanish mid-process."""
        if self._available is None:
            self._available = importlib.util.find_spec("reportlab") is not None
            if self._available:
                try:
                    version = importlib.metadata.version("reportlab")
                except importlib.metadata.PackageNotFoundError:
                    version = "?"
                self.tool_version = f"reportlab/{version}"
        return self._available

    def unavailable_message(self) -> str:
        return (
            "PDF rendering with ReportLab requires the optional 'pdf' extra "
            "(pip install content-backend[pdf])."
        )

    # --- fonts -----------------------------------------------------------------

    def coverages(self, text: str) -> list[FontCoverage]:
        """What the chosen font can draw.

        Only the *selected* font counts, not every installed one: ReportLab
        draws the document in one family, so a glyph living in a font we did not
        pick is still a blank square on the page.
        """
        regular, _bold = self._fonts(text)
        if regular == "Helvetica":
            return [winansi_coverage()]
        path = self._chosen_font_path(text)
        coverage = load_coverage(path) if path else None
        return [coverage] if coverage else [winansi_coverage()]

    def _font_files(self) -> list[Path]:
        candidates = [Path(self.font_path)] if self.font_path else []
        return candidates + [Path(p) for p in _FONT_CANDIDATES]

    def _chosen_font_path(self, text: str) -> Path | None:
        """The first installed candidate that covers *text*, else the first that
        exists at all (partial coverage still beats Helvetica for a non-Latin
        document, and the base class reports what remains undrawable)."""
        fallback = None
        for path in self._font_files():
            if not path.is_file():
                continue
            coverage = load_coverage(path)
            if coverage is None:
                continue
            if not [c for c in text if not coverage.covers(ord(c)) and c.strip()]:
                return path
            fallback = fallback or path
        return fallback

    def _fonts(self, text: str) -> tuple[str, str]:
        """``(regular, bold)`` font names for *text*.

        Helvetica is preferred: it is one of the base-14 fonts and has genuine
        bold and italic faces, so emphasis renders as emphasis. It only covers
        WinAnsi, though, so anything outside it needs a TrueType font instead.
        Deciding per document keeps both true — Latin gets real typography,
        other scripts get real glyphs.
        """
        base = winansi_coverage()
        if all(base.covers(ord(c)) or c.isspace() for c in text):
            return "Helvetica", "Helvetica-Bold"
        path = self._chosen_font_path(text)
        if path is not None:
            registered = self._register_family(path)
            if registered is not None:
                return registered
        return "Helvetica", "Helvetica-Bold"

    def _register_family(self, regular: Path) -> tuple[str, str] | None:
        """Register *regular* plus whatever sibling faces sit next to it.

        A font shipping a full family (DejaVu) keeps bold and italic; a
        single-file font maps every face to itself, so emphasis flattens rather
        than falling back to a font that cannot draw the script at all.
        """
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        alias = "ContentText-" + hashlib.sha256(str(regular).encode()).hexdigest()[:8]

        def sibling(*suffixes: str) -> Path | None:
            for suffix in suffixes:
                candidate = regular.with_name(regular.stem + suffix + regular.suffix)
                if candidate.is_file():
                    return candidate
            return None

        faces = {
            "": regular,
            "-Bold": sibling("-Bold", "-bold") or regular,
            "-Italic": sibling("-Oblique", "-Italic", "-italic") or regular,
            "-BoldItalic": sibling("-BoldOblique", "-BoldItalic") or regular,
        }
        try:
            for suffix, path in faces.items():
                pdfmetrics.registerFont(TTFont(f"{alias}{suffix}", str(path)))
            pdfmetrics.registerFontFamily(
                alias,
                normal=alias,
                bold=f"{alias}-Bold",
                italic=f"{alias}-Italic",
                boldItalic=f"{alias}-BoldItalic",
            )
        except Exception:  # noqa: BLE001 - a broken font must not fail the job
            return None
        return alias, f"{alias}-Bold"

    # --- rendering -------------------------------------------------------------

    def render(
        self,
        document: Document,
        target: Path,
        ctx: ExecutionContext,
        *,
        page_size: str,
        title: str,
        params: dict,
    ) -> int:
        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.platypus import SimpleDocTemplate

        styles = self._styles(document.text_content())
        flowables = document_to_flowables(document, styles)
        template = SimpleDocTemplate(
            str(target),
            pagesize=LETTER if page_size == "letter" else A4,
            title=title or None,
            author="Content",
            invariant=1,
            leftMargin=56,
            rightMargin=56,
            topMargin=56,
            bottomMargin=56,
        )
        template.build(flowables)
        self._stamp_deterministic_date(target, document)
        return getattr(template, "page", 1)

    def _stamp_deterministic_date(self, target: Path, document: Document) -> None:
        """Replace the embedded creation date with one derived from the content,
        so identical input keeps producing identical bytes."""
        digest = hashlib.sha256(document.text_content().encode("utf-8")).hexdigest()
        seconds = int(digest[:4], 16) % 60
        stamp = f"D:20000101000{seconds // 10}{seconds % 10}+00'00'".encode()
        raw = target.read_bytes()
        patched = re.sub(rb"D:\d{14}[+\-]\d{2}'\d{2}'", stamp, raw)
        if patched != raw:
            target.write_bytes(patched)

    def _styles(self, text: str) -> dict:
        from reportlab.lib.enums import TA_JUSTIFY
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

        base = getSampleStyleSheet()
        font, bold = self._fonts(text)
        return {
            "h1": ParagraphStyle(
                "h1",
                parent=base["Title"],
                fontName=bold,
                fontSize=20,
                leading=25,
                spaceAfter=14,
                alignment=0,
            ),
            "h2": ParagraphStyle(
                "h2",
                parent=base["Heading2"],
                fontName=bold,
                fontSize=14,
                leading=19,
                spaceBefore=14,
                spaceAfter=7,
            ),
            "h3": ParagraphStyle(
                "h3",
                parent=base["Heading3"],
                fontName=bold,
                fontSize=12,
                leading=16,
                spaceBefore=11,
                spaceAfter=5,
            ),
            "h4": ParagraphStyle(
                "h4",
                parent=base["Heading4"],
                fontName=bold,
                fontSize=11,
                leading=15,
                spaceBefore=9,
                spaceAfter=4,
            ),
            "body": ParagraphStyle(
                "body",
                parent=base["BodyText"],
                fontName=font,
                fontSize=10.5,
                leading=15.5,
                spaceAfter=8,
                alignment=TA_JUSTIFY,
            ),
            "quote": ParagraphStyle(
                "quote",
                parent=base["BodyText"],
                fontName=font,
                fontSize=10.5,
                leading=15.5,
                leftIndent=18,
                textColor="#444444",
                spaceAfter=8,
            ),
            "code": ParagraphStyle(
                "code",
                parent=base["Code"],
                fontName="Courier",
                fontSize=8.5,
                leading=11,
                leftIndent=10,
                spaceAfter=8,
            ),
        }
