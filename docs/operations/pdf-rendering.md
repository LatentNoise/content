# PDF rendering

The `pdf` output type is produced by one logical transformation with two
interchangeable implementations. Which one runs is an operator decision; the
public contract never mentions a renderer.

```
public capability      pdf.render
logical transformation document.render_pdf
implementations        content.pdf.typst      (preferred)
                       content.pdf.reportlab  (fallback)
```

A request asks for `{"type": "pdf"}` and composes it with `from_outputs`. It
carries no renderer, no template, no font and no compiler option — see
[contract.md](../contract.md).

## The pipeline

Markdown is parsed **once**, into Content's own document model. Both renderers
consume that model; neither parses Markdown.

```
Markdown / structured text
  └─► DocumentModel (blocks + spans)      content/documents/
        ├─► ReportLab flowables ─────────────────────────► PDF
        └─► document.json + server-owned template.typ ─► typst ─► PDF
```

The model exists mainly for safety. Typst is a full programming language —
`#panic(…)`, `#read(…)` and imports are ordinary syntax — so building `.typ`
source by concatenating summary text would let a web page or an LLM choose what
the compiler executes. Instead the document is serialized to JSON and a template
shipped with the engine places each value with `strong()`/`emph()`/`link()`.
There is no path from a span's text to executable syntax, and the test suite
asserts it per backend with a payload of `#panic("pwned")`.

## Choosing a renderer

| Setting | Effect |
| --- | --- |
| `CONTENT_PDF_RENDERER=auto` *(default)* | Typst when its binary is healthy, otherwise ReportLab |
| `CONTENT_PDF_RENDERER=typst` | Typst only. If it is unusable the request **fails** rather than downgrading silently |
| `CONTENT_PDF_RENDERER=reportlab` | ReportLab only |
| `CONTENT_TYPST_BINARY` | Typst executable (default `typst`) |
| `CONTENT_PDF_TEMPLATE` | Server-side template **name** (default `default`) — never a path, never template source |
| `CONTENT_PDF_FONT` | Extra TrueType font file or directory for scripts the built-in faces cannot draw |
| `CONTENT_PDF_MISSING_GLYPHS` | `replace` *(default)* \| `error` \| `warn` — see [Missing glyphs](#missing-glyphs) |

A client may express a preference with
`preferences.providers: {"pdf": ["content.pdf.reportlab"]}` — the same generic
family map already used for LLM runners. The operator's pin wins over it.

The chosen renderer is recorded on the plan step and in the artifact's
provenance (`producer.provider`, `producer.tool_version`), so a document can
always be traced to the backend that made it.

## Differences and limitations

| | Typst | ReportLab |
| --- | --- | --- |
| **Delivery** | 46 MB static binary (musl, x86_64 + aarch64) | Pure-Python wheel, any architecture |
| **Image cost** | ~74 MB on the official image | negligible |
| **Typography** | Ligatures, hyphenation, real justification, Libertinus Serif | Helvetica (base-14) or a TrueType fallback |
| **Speed** | ~15 ms per document | ~10 ms per document |
| **Byte reproducibility** | **No** — see below | Yes |
| **Bundled scripts** | Latin, Greek, Cyrillic (Libertinus) | WinAnsi only (Helvetica) |
| **CJK / Arabic / Indic** | Needs a font via `CONTENT_PDF_FONT` | Needs a font via `CONTENT_PDF_FONT` |
| **Missing-glyph handling** | Identical — engine policy, see below | Identical — engine policy, see below |

**Typst output is not byte-reproducible.** It assigns PDF font-subset prefix
tags (`ABCDEF+LibertinusSerif`) non-deterministically: two runs of identical
input render identically but differ in a handful of bytes. Measured at four
distinct outputs over twelve runs of the same document. This does not affect
correctness or caching — the artifact cache keys on the step signature, never on
output bytes — but do not expect `sha256` equality across renders. ReportLab
*is* byte-reproducible, because its embedded creation date is derived from the
content hash.

**Glyph coverage is validated for both**, because neither reports it. ReportLab
draws a notdef box; Typst exits 0 and draws tofu. A successful exit code is not
a successful render, so the engine decides what happens — see
[Missing glyphs](#missing-glyphs) below.

**Markdown support is identical by construction** — one parser, one model. The
supported subset is what the engine actually emits: headings, paragraphs, bullet
and numbered lists, block quotes, fenced code, thematic breaks, and inline
bold/italic/code/links. Anything else stays literal text.

## Missing glyphs

When the text needs characters no available font can draw, `CONTENT_PDF_MISSING_GLYPHS`
decides what happens. The policy is resolved by the engine, not by a backend, so
the three modes mean exactly the same thing whichever renderer runs.

| Mode | Behaviour |
| --- | --- |
| `replace` *(default)* | Substitute a drawable placeholder (`�`, or `?` when the font lacks even that) and report. |
| `error` | Refuse the step with `unsupported_glyphs` and structured `details`. |
| `warn` | Render unchanged and report. The only mode that can put an undrawable glyph on a page. |

No mode is silent. `replace` and `warn` attach a record to the artifact's
provenance; `error` carries the same structure on the failure event:

```json
{
  "missing_glyphs": {
    "policy": "replace",
    "count": 2,
    "characters": ["🙂", "日"],
    "code_points": ["U+1F642", "U+65E5"],
    "replaced_with": "�"
  }
}
```

The record is **absent** when nothing was missing — its absence is a positive
statement that the document is complete, not merely that nobody looked.

### Why `replace` is the default

General web and LLM content routinely contains a stray emoji, arrow or
box-drawing character. Under `error` a single one destroys an otherwise perfect
summary, which is a poor trade for a document nobody will reprint. Under `warn`
the reader gets a blank square with no indication anything was lost.

`replace` keeps the document usable, makes the loss visible on the page, and
records exactly what was dropped. Choose `error` when a PDF is a deliverable
that must be perfect or not exist; choose `warn` when you know your fonts and
want the raw output.

An unrecognised value falls back to `replace`: a typo in an environment variable
must not take PDF output down, and the effective policy is on every affected
artifact.

### Coverage differs between renderers, policy does not

The two backends can legitimately disagree about *what* is missing: ReportLab
searches the system font path, while Typst runs with `--ignore-system-fonts` so
that output does not depend on the host. The same document may therefore report
one missing character under ReportLab and three under Typst on the same machine.
That is the fonts differing, not the policy — install the font you need and both
agree.

### Font packs (not implemented)

Shipping optional font bundles — a CJK pack, an Arabic pack — would remove most
`replace` substitutions for non-Latin content. It is deliberately **not** part
of this work: it is a packaging and licensing question (font licences vary and
must be audited before redistribution), not a rendering one, and today's
`CONTENT_PDF_FONT` already covers the operator who has a font to hand. Treat it
as a separate proposal.

## Fonts

Typst runs with `--ignore-system-fonts` and an explicit `--font-path`. Without
that it discovers whatever the host happens to have installed, and the same
request renders differently on two machines.

The official image ships DejaVu (`ttf-dejavu`): Latin, Greek and Cyrillic. It
carries **no CJK font** — that would add ~15 MB for a case most deployments
never hit. To render Japanese, Chinese, Korean, Arabic or Indic scripts, mount a
font and point `CONTENT_PDF_FONT` at it:

```yaml
volumes:
  - /usr/share/fonts/noto:/fonts:ro
environment:
  CONTENT_PDF_FONT: /fonts
```

## Building the image

Typst is installed by default from a **pinned release, verified by checksum**:

```bash
docker compose build content                                  # Typst included
docker compose build --build-arg INSTALL_TYPST=false content  # ReportLab only
```

The version and both architecture checksums are literals in
[apps/backend/Dockerfile](../../apps/backend/Dockerfile). They are pinned
deliberately: Alpine's package lags well behind (0.12 vs 0.15) and Typst is
pre-1.0 with breaking syntax changes between minor versions, so tracking a
distro package would silently change how the shipped template compiles. The
Python binding is not an option either — it publishes no musl wheels.

On an architecture with no Typst build the image still builds, without it;
ReportLab then renders every PDF. That is why ReportLab is a supported fallback
rather than a temporary implementation.

Typst is Apache-2.0. The image installs its licence at
`/usr/local/share/licenses/typst/LICENSE`, and [NOTICE](../../NOTICE) records
the attribution required for redistribution.

## Templates

Templates are server-owned resources selected by name. `CONTENT_PDF_TEMPLATE`
takes an identifier (`[a-z0-9][a-z0-9_-]{0,31}`); it is validated before it is
joined to a path, and the resolved file is confirmed to sit inside the shipped
template directory, so neither `../` nor an absolute path nor a symlink can
escape. Arbitrary Typst code cannot be supplied through the API by design.

The shipped template lives at
`apps/backend/content/processors/pdf/templates/default.typ`. Adding one means
adding a file there and shipping it — deliberately a release-time act, not a
runtime input.
