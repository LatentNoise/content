"""The neutral document model: one parse, shared by every renderer.

These tests own the *grammar* and the *coverage* questions. Renderer tests
assert what comes out the other end; nothing here imports a renderer, which is
the point — the model must be meaningful without one.
"""

import pathlib

import pytest

from content.documents.fonts import (
    FontCoverage,
    describe_missing,
    load_coverage,
    missing_characters,
    winansi_coverage,
)
from content.documents.markdown import parse_inline, parse_markdown
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

ARTICLE = """# On Declarative Engines

Declaring **what** you want beats invoking *how*. See [the spec](https://x/spec).

## Why it matters

- Stable intent
- Swappable `tools`

1. First it works
2. Then it is fast

> A contract you cannot change is a contract you cannot fix.

---

```python
plan = build(request)
```
"""


# --- grammar --------------------------------------------------------------------


def test_every_block_kind_the_engine_emits_is_recognised():
    document = parse_markdown(ARTICLE, title="T")
    kinds = [block.kind for block in document.blocks]
    assert kinds == [
        HEADING,
        PARAGRAPH,
        HEADING,
        BULLETS,
        NUMBERED,
        QUOTE,
        RULE,
        CODE,
    ]
    assert document.title == "T"


def test_inline_marks_become_flags_never_markup():
    """The text of a span is always literal. This is what lets the same model
    feed a renderer whose input language is executable."""
    spans = parse_inline("plain **bold** and *italic* and `code` and [x](https://y)")
    assert [(s.text, s.bold, s.italic, s.code, s.href) for s in spans] == [
        ("plain ", False, False, False, ""),
        ("bold", True, False, False, ""),
        (" and ", False, False, False, ""),
        ("italic", False, True, False, ""),
        (" and ", False, False, False, ""),
        ("code", False, False, True, ""),
        (" and ", False, False, False, ""),
        ("x", False, False, False, "https://y"),
    ]


def test_bold_is_not_read_as_two_italics():
    spans = parse_inline("**strong**")
    assert len(spans) == 1 and spans[0].bold and not spans[0].italic


def test_lists_keep_their_kind_and_split_when_it_changes():
    document = parse_markdown("- a\n- b\n1. c\n2. d\n")
    assert [b.kind for b in document.blocks] == [BULLETS, NUMBERED]
    assert len(document.blocks[0].items) == 2
    assert document.blocks[1].items[0][0].text == "c"


def test_code_blocks_are_kept_verbatim():
    document = parse_markdown("```\nline one\n  indented\n```\n")
    assert document.blocks[0].kind == CODE
    assert document.blocks[0].text == "line one\n  indented"


def test_unrecognised_syntax_stays_literal_text():
    """Better a stray character on the page than a renderer interpreting it."""
    document = parse_markdown("A | table | row\nand <html> & entities\n")
    text = "".join(s.text for s in document.blocks[0].spans)
    assert "<html>" in text and "&" in text and "|" in text


def test_the_model_serializes_to_the_json_the_template_consumes():
    document = parse_markdown("# T\n\nHello **you**\n", title="Doc")
    payload = document.as_dict()
    assert payload["title"] == "Doc"
    assert payload["blocks"][0] == {
        "kind": HEADING,
        "level": 1,
        "spans": [{"text": "T"}],
    }
    # Flags are omitted when false, so the payload stays small and readable.
    assert payload["blocks"][1]["spans"][1] == {"text": "you", "bold": True}


def test_text_content_covers_everything_that_will_be_drawn():
    """Coverage validation reads this. Missing a list item here would mean a
    glyph check that silently skips half the document."""
    document = parse_markdown("# H\n\n- item\n\n```\ncode\n```\n", title="Ti")
    content = document.text_content()
    for fragment in ("Ti", "H", "item", "code"):
        assert fragment in content


def test_an_empty_document_is_detectable():
    assert Document().is_empty
    assert parse_markdown("   \n\n").is_empty
    assert not parse_markdown("something").is_empty


# --- glyph coverage -------------------------------------------------------------


def test_winansi_coverage_matches_the_base_14_repertoire():
    coverage = winansi_coverage()
    assert missing_characters("plain ascii and café, naïve, œuvre", [coverage]) == []
    assert missing_characters("日本語", [coverage]) == ["日", "本", "語"]


def test_the_cmap_parser_agrees_with_reportlabs_on_a_real_font():
    """The parser is hand-written (no dependency), so it is checked against an
    independent implementation rather than trusted."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    vera = pathlib.Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("VeraCrossCheck", str(vera)))
    theirs = pdfmetrics.getFont("VeraCrossCheck").face.charToGlyph
    ours = load_coverage(vera)
    assert ours is not None

    disagreements = [
        codepoint
        for codepoint in range(0x2500)
        if (theirs.get(codepoint) not in (None, 0)) != ours.covers(codepoint)
    ]
    assert disagreements == []


def test_an_unreadable_font_is_never_assumed_to_cover_everything():
    assert load_coverage(pathlib.Path("/nonexistent/font.ttf")) is None


def test_a_font_that_cannot_be_parsed_returns_none(tmp_path):
    broken = tmp_path / "broken.ttf"
    broken.write_bytes(b"not a font at all")
    assert load_coverage(broken) is None


def test_coverage_is_the_union_across_available_fonts():
    """A renderer falls back between fonts, so a character is only missing when
    no candidate has it."""
    latin = FontCoverage(name="latin", codepoints={ord("a")})
    cjk = FontCoverage(name="cjk", codepoints={ord("日")})
    assert missing_characters("a日", [latin]) == ["日"]
    assert missing_characters("a日", [latin, cjk]) == []


def test_whitespace_and_control_characters_are_not_coverage_failures():
    coverage = FontCoverage(name="x", codepoints={ord("a")})
    assert missing_characters("a\n\t ​", [coverage]) == []


def test_no_known_font_means_no_claim_either_way():
    """With nothing to check against, reporting every character as missing
    would be as wrong as reporting none."""
    assert missing_characters("anything", []) == []


def test_the_operator_message_names_the_characters_and_the_remedy():
    message = describe_missing(["日", "本"])
    assert "U+65E5" in message
    assert "CONTENT_PDF_FONT" in message


def test_span_and_block_flags_round_trip_through_json():
    span = Span("x", bold=True, italic=True, code=True, href="https://h")
    assert span.as_dict() == {
        "text": "x",
        "bold": True,
        "italic": True,
        "code": True,
        "href": "https://h",
    }
    assert Span("plain").as_dict() == {"text": "plain"}


def test_quote_and_rule_survive_the_round_trip():
    document = parse_markdown("> quoted\n\n---\n")
    payload = document.as_dict()
    assert payload["blocks"][0]["kind"] == QUOTE
    assert payload["blocks"][1] == {"kind": RULE}
    assert document.blocks[0].spans[0].text == "quoted"
    assert parse_markdown("para").blocks[0].kind == PARAGRAPH
