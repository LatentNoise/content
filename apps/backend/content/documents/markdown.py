"""The one Markdown parser: text → :class:`Document`.

Previously this logic produced reportlab flowables directly, which coupled the
grammar to one renderer. It now produces the neutral model, so ReportLab and
Typst are guaranteed to lay out the *same* parse — a construct cannot render in
one and vanish in the other.

Scope is the subset the engine actually emits (LLM summaries, extracted pages,
transcripts): headings, paragraphs, bullet and numbered lists, block quotes,
fenced code and thematic breaks, with inline emphasis, code and links. Anything
unrecognised stays literal text rather than leaking syntax onto the page.
"""

from __future__ import annotations

import re

from content.documents.model import (
    BULLETS,
    CODE,
    HEADING,
    NUMBERED,
    PARAGRAPH,
    QUOTE,
    RULE,
    Block,
    Document,
    Span,
)

_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")

# Inline marks, applied in this order so that `**bold**` is not mistaken for two
# italics and code spans keep their contents verbatim.
_INLINE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)\s]+\))"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<italic>(?<!\*)\*[^*]+\*(?!\*))"
)


def parse_inline(text: str) -> tuple[Span, ...]:
    """Split a line into marked spans. Text never becomes markup: each span
    carries flags, and the literal characters travel untouched."""
    spans: list[Span] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            spans.append(Span(text[position : match.start()]))
        raw = match.group(0)
        if match.lastgroup == "code":
            spans.append(Span(raw[1:-1], code=True))
        elif match.lastgroup == "bold":
            spans.append(Span(raw[2:-2], bold=True))
        elif match.lastgroup == "italic":
            spans.append(Span(raw[1:-1], italic=True))
        else:  # link
            label, _, target = raw[1:].partition("](")
            spans.append(Span(label, href=target[:-1]))
        position = match.end()
    if position < len(text):
        spans.append(Span(text[position:]))
    return tuple(span for span in spans if span.text)


def parse_markdown(markdown: str, *, title: str = "") -> Document:
    """Parse *markdown* into the neutral model.

    ``title`` is document metadata (the PDF's title, what a template may print);
    it is not injected into the body, so a reading that already opens with its
    own heading is not repeated.
    """
    document = Document(title=title)
    lines = markdown.replace("\r\n", "\n").split("\n")
    paragraph: list[str] = []
    pending: list[tuple[Span, ...]] = []
    ordered = False
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            document.blocks.append(
                Block(kind=PARAGRAPH, spans=parse_inline(" ".join(paragraph)))
            )
            paragraph.clear()

    def flush_list() -> None:
        nonlocal pending
        if pending:
            document.blocks.append(
                Block(kind=NUMBERED if ordered else BULLETS, items=tuple(pending))
            )
            pending = []

    while index < len(lines):
        line = lines[index]

        if line.lstrip().startswith("```"):
            flush_paragraph()
            flush_list()
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].lstrip().startswith("```"):
                block.append(lines[index])
                index += 1
            if block:
                document.blocks.append(Block(kind=CODE, text="\n".join(block)))
            index += 1
            continue

        if not line.strip():
            flush_paragraph()
            flush_list()
            index += 1
            continue

        if _RULE.match(line):
            flush_paragraph()
            flush_list()
            document.blocks.append(Block(kind=RULE))
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            document.blocks.append(
                Block(
                    kind=HEADING,
                    level=min(len(heading.group(1)), 4),
                    spans=parse_inline(heading.group(2).strip()),
                )
            )
            index += 1
            continue

        quote = _QUOTE.match(line)
        if quote:
            flush_paragraph()
            flush_list()
            document.blocks.append(
                Block(kind=QUOTE, spans=parse_inline(quote.group(1)))
            )
            index += 1
            continue

        numbered = _NUMBERED.match(line)
        bullet = _BULLET.match(line)
        if numbered or bullet:
            flush_paragraph()
            item_ordered = numbered is not None
            if pending and item_ordered != ordered:
                flush_list()
            ordered = item_ordered
            raw = (numbered.group(2) if numbered else bullet.group(1)).strip()
            pending.append(parse_inline(raw))
            index += 1
            continue

        paragraph.append(line.strip())
        index += 1

    flush_paragraph()
    flush_list()
    _drop_repeated_title(document)
    return document


def _drop_repeated_title(document: Document) -> None:
    """Collapse a title that the document states twice in a row.

    Observed in a real PDF: the heading appeared, a rule, then the very same
    heading and another rule. It happens whenever something writes a header
    above a body that already titles itself — and an LLM summary titles itself
    almost every time, because that is what "format the output as Markdown"
    produces.

    Only the narrow case is touched: two headings of the same level with the
    same text, adjacent or separated by nothing but rules. That is never a
    document someone meant to write. Two identical headings further apart are
    left alone — a recurring section title is a legitimate shape, and guessing
    at it would be the renderer editing the author.
    """
    heads = [i for i, b in enumerate(document.blocks) if b.kind == HEADING]
    for first, second in zip(heads, heads[1:]):
        between = document.blocks[first + 1 : second]
        if any(b.kind != RULE for b in between):
            continue
        a, b = document.blocks[first], document.blocks[second]
        if a.level != b.level:
            continue
        if _spans_text(a.spans).strip() != _spans_text(b.spans).strip():
            continue
        # Keep the first heading and drop the duplicate with the rules it
        # dragged along, so the page does not end up with a stray double line.
        del document.blocks[first + 1 : second + 1]
        return


def _spans_text(spans) -> str:
    return "".join(s.text for s in spans)
