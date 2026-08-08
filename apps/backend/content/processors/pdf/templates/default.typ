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

#set page(paper: meta.page_size, margin: 2.2cm)
#set text(size: 10.5pt, hyphenate: true)
#set par(justify: true, leading: 0.65em)
#show heading: it => block(above: 1.1em, below: 0.55em, it)
#show link: it => text(fill: rgb("#1a4fa0"), it)

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
    block(inset: (left: 1em), stroke: (left: 2pt + luma(200)))[
      #text(fill: luma(70), spans(b.spans))
    ]
  } else if kind == "code" {
    block(fill: luma(246), inset: 8pt, radius: 3pt, width: 100%, raw(b.text))
  } else if kind == "rule" {
    block(above: 0.9em, below: 0.9em, line(length: 100%, stroke: 0.5pt + luma(200)))
  }
}
