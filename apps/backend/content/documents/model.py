"""The neutral document model — Content's own representation of a readable
document, independent of any renderer.

Everything the engine can render passes through this shape exactly once. It
exists for three reasons, in order of importance:

**Safety.** The Typst renderer feeds a real programming language: `#panic(…)`,
imports and file reads are ordinary syntax there. Serializing this model to JSON
and letting a server-owned template place the values keeps user text *data* from
end to end. Building `.typ` source by string concatenation would be a code
execution vector, not a style choice.

**One parser.** Two renderers reading Markdown independently would drift the
moment one of them learned a construct the other did not. Markdown is parsed
once, here; renderers consume the result.

**Honesty about coverage.** A flat list of spans makes it cheap to ask "which
characters does this document actually need?", which is what keeps a missing
glyph from silently becoming a blank page.

The vocabulary is deliberately the subset the engine really produces — LLM
summaries, extracted web pages, transcripts — not a general Markdown AST. Every
kind here is one a renderer must handle; there is no "other".
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Block kinds. Exhaustive by design: a renderer switching on these needs no
# default branch, so adding one is a compile-time-ish conversation rather than a
# silent fallthrough to plain text.
HEADING = "heading"
PARAGRAPH = "paragraph"
BULLETS = "bullets"
NUMBERED = "numbered"
QUOTE = "quote"
CODE = "code"
RULE = "rule"

BLOCK_KINDS = (HEADING, PARAGRAPH, BULLETS, NUMBERED, QUOTE, CODE, RULE)


@dataclass(frozen=True)
class Span:
    """A run of text with its marks. ``text`` is always literal content — never
    markup, in any syntax."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str = ""

    def as_dict(self) -> dict:
        """JSON shape for the Typst template. Marks are omitted when false so
        the payload stays small and diffable."""
        payload: dict = {"text": self.text}
        for name, value in (
            ("bold", self.bold),
            ("italic", self.italic),
            ("code", self.code),
        ):
            if value:
                payload[name] = True
        if self.href:
            payload["href"] = self.href
        return payload


@dataclass(frozen=True)
class Block:
    """One block-level element.

    Which fields are meaningful depends on ``kind``: ``spans`` for heading,
    paragraph and quote; ``items`` (a list of span lists) for the two list
    kinds; ``text`` for code; nothing for a rule. Kept as one dataclass rather
    than a class hierarchy because it round-trips to JSON directly, and the JSON
    is the contract with the Typst template.
    """

    kind: str
    spans: tuple[Span, ...] = ()
    items: tuple[tuple[Span, ...], ...] = ()
    text: str = ""
    level: int = 1

    def as_dict(self) -> dict:
        payload: dict = {"kind": self.kind}
        if self.kind == HEADING:
            payload["level"] = self.level
        if self.spans:
            payload["spans"] = [span.as_dict() for span in self.spans]
        if self.items:
            payload["items"] = [
                [span.as_dict() for span in item] for item in self.items
            ]
        if self.text:
            payload["text"] = self.text
        return payload


@dataclass
class Document:
    title: str = ""
    blocks: list[Block] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"title": self.title, "blocks": [b.as_dict() for b in self.blocks]}

    def text_content(self) -> str:
        """Every character the document will actually draw.

        Used for glyph-coverage validation, so it must include list items and
        code — anything a renderer puts on the page. The title is included
        because it is drawn too when a template shows it.
        """
        parts: list[str] = [self.title]
        for block in self.blocks:
            parts.extend(span.text for span in block.spans)
            for item in block.items:
                parts.extend(span.text for span in item)
            if block.text:
                parts.append(block.text)
        return "\n".join(parts)

    @property
    def is_empty(self) -> bool:
        return not any(
            block.spans or block.items or block.text for block in self.blocks
        )

    def map_text(self, transform) -> "Document":
        """A copy with *transform* applied to every drawable string.

        Substitution has to reach the same places coverage validation reads —
        spans, list items, code blocks and the title — or a policy that claims
        to have replaced every undrawable character would leave some behind.
        Both walk the document through this pair of methods for that reason.
        """

        def spans(items: tuple[Span, ...]) -> tuple[Span, ...]:
            return tuple(
                Span(
                    transform(span.text),
                    bold=span.bold,
                    italic=span.italic,
                    code=span.code,
                    href=span.href,
                )
                for span in items
            )

        return Document(
            title=transform(self.title),
            blocks=[
                Block(
                    kind=block.kind,
                    spans=spans(block.spans),
                    items=tuple(spans(item) for item in block.items),
                    text=transform(block.text),
                    level=block.level,
                )
                for block in self.blocks
            ],
        )
