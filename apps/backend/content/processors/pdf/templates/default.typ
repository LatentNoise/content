// Content's default document template — SERVER-OWNED.
//
// This file is the only Typst source that ever runs. It is shipped with the
// engine, never supplied through the API, and never built by string
// concatenation: the document arrives as JSON and every value is *placed* by
// the functions below.
//
// That distinction is the security boundary. Typst is a real programming
// language — `#panic(…)`, `#read(…)` and imports are ordinary syntax — so
// interpolating summary text into markup would let a web page or an LLM choose
// what the compiler executes. Passing data through `json()` means user text is
// content in a string, and `strong()`/`emph()`/`link()` place it as content.
// There is no code path from a span's text to executable syntax.
//
// Selected by name via CONTENT_PDF_TEMPLATE; the public contract carries no
// template source, no path and no Typst options.

#let doc = json("document.json")
#let meta = json("meta.json")

// --- palette and type scale -------------------------------------------------
//
// One accent, one ink, one muted grey. A document that a person will read on a
// screen and occasionally print: enough contrast to survive a laser printer,
// not so much that a wall of text becomes a wall of black.
#let ink = rgb("#14181f")
#let muted = rgb("#5b6572")
#let accent = rgb("#2f5bd7")
#let hairline = rgb("#dfe3e9")

#set page(
  paper: meta.page_size,
  margin: (x: 2.4cm, y: 2.6cm),
  // A discreet folio, and nothing else in the furniture. Page one carries no
  // number: a one-page summary with "1" at the foot looks like a fragment.
  footer: context {
    let n = counter(page).get().first()
    if n > 1 {
      align(center, text(size: 8.5pt, fill: muted, str(n)))
    }
  },
)

// Libertinus is the most readable face shipped in the image, and the only one
// here that does not look like a 1994 manual. The fallbacks matter: the glyph
// policy upstream may have substituted characters, and DejaVu covers more.
#set text(
  font: ("Libertinus Serif", "DejaVu Serif", "DejaVu Sans"),
  size: 10.5pt,
  fill: ink,
  hyphenate: true,
)
#set par(justify: true, leading: 0.72em, spacing: 1.15em, first-line-indent: 0pt)

#show heading: set text(fill: ink)
#show heading.where(level: 1): it => block(
  above: 0em, below: 0.9em,
  {
    text(size: 20pt, weight: 600, it.body)
    // A hairline under the document title, not a full rule: it separates
    // without shouting, and it never repeats down the page.
    v(0.18em)
    line(length: 100%, stroke: 0.6pt + hairline)
  },
)
#show heading.where(level: 2): it => block(
  above: 1.5em, below: 0.55em,
  text(size: 13.5pt, weight: 600, it.body),
)
// Level 3 stays darker than the body it introduces. Greying it out read as
// *less* important than the list underneath — a heading that recedes behind
// its own content is a hierarchy upside down.
#show heading.where(level: 3): it => block(
  above: 1.3em, below: 0.45em,
  text(size: 11pt, weight: 600, fill: accent.darken(15%), it.body),
)

#show link: it => text(fill: accent, it)
#set list(marker: (text(fill: accent, sym.bullet), text(fill: muted, "–")), spacing: 0.85em)
#set enum(spacing: 0.85em)

#if meta.title != "" {
  set document(title: meta.title)
}
// A span is data: text plus flags. Marks are applied by wrapping the *value*,
// so nothing in `s.text` is ever parsed as Typst.
#let render-span(s) = {
  let t = s.text
  if s.at("code", default: false) { t = raw(t) }
  if s.at("bold", default: false) { t = strong(t) }
  if s.at("italic", default: false) { t = emph(t) }
  if "href" in s { t = link(s.href, t) }
  t
}

#let spans(ss) = ss.map(render-span).join()

#for b in doc.blocks {
  let kind = b.kind
  if kind == "heading" {
    heading(level: b.level, spans(b.spans))
  } else if kind == "paragraph" {
    par(spans(b.spans))
  } else if kind == "bullets" {
    list(..b.items.map(i => spans(i)))
  } else if kind == "numbered" {
    enum(..b.items.map(i => spans(i)))
  } else if kind == "quote" {
    block(
      inset: (left: 1.1em, y: 0.2em),
      stroke: (left: 2pt + accent.lighten(45%)),
      text(fill: muted, style: "italic", spans(b.spans)),
    )
  } else if kind == "code" {
    block(
      fill: rgb("#f6f7f9"),
      stroke: 0.5pt + hairline,
      inset: 9pt,
      radius: 4pt,
      width: 100%,
      text(font: ("DejaVu Sans Mono",), size: 9pt, raw(b.text)),
    )
  } else if kind == "rule" {
    block(above: 1.4em, below: 1.4em, line(length: 100%, stroke: 0.6pt + hairline))
  }
}
