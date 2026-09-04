# ADR 0029 — A document with sections

Status: proposed (2026-09-04) · Extends ADR 0028 (documents derivable through
text) · Lifts one arity rule from D3 (linear derivation chains) for
presentation types only · Naming under ADR 0017/0018

## Context

A `pdf` (or `markdown`) output renders exactly **one** upstream output. The
contract's `from_outputs` is already an array, but D3 keeps derivation chains
linear in V1 and the planner reads only the first reference. So every
composition a user can ask for today is a chain, never a join:

- *a PDF of the summary* — expressible (and the ADR 0028 default);
- *a PDF of the transcript* — expressible, one reference;
- *a PDF containing the summary, then the chapters, then the full
  transcript* — **not expressible at all**.

The third is the natural ask the moment documents work: "one well-organized
PDF of this talk" means a *document*, and a document has sections. Users reach
for it immediately after their first summary-PDF; the maintainer did.

The architecture underneath is already shaped for it. Semantic outputs
(summary, transcript, translation, chapters) are produced independently and
composed by reference; presentation types render readable Markdown into a
page; `Artifact.provenance.parent_artifact_ids` is already a list. The only
thing that says "one" anywhere is the planner's first-reference read.

## Decision (proposed)

**An ordered multi-reference on a presentation type is a sectioned
document.**

```json
[{"id": "s", "type": "summary"},
 {"id": "c", "type": "chapters"},
 {"id": "t", "type": "transcript", "options": {"format": "text"}},
 {"type": "pdf", "from_outputs": ["s", "c", "t"],
  "options": {"title": "The talk, on paper", "toc": true}}]
```

1. **Order is document order.** `from_outputs: ["s", "c", "t"]` renders the
   summary first, then the chapters, then the transcript. No new field, no
   new vocabulary: the array the contract always had, finally read past its
   first element — for `pdf` and `markdown` only. Semantic outputs keep D3's
   single input; a summary of three things at once stays future work with its
   own semantics (`scope: all_sources` territory).
2. **Each section opens with a heading naming what it is** (Summary,
   Chapters, Transcript — the output's title vocabulary, localized by the
   client if it wants to, not by the engine). A new `toc` option (default
   false) prepends a table of contents; `title` keeps its meaning as the
   document title.
3. **Every referenced output must be renderable** — the existing
   `RENDERABLE_OUTPUT_TYPES` rule, applied per reference, same refusal
   message. A machine-format section (`json`) warns exactly as it does
   today, per section.
4. **Naming**: one reference keeps today's inheritance (`… - summary.pdf`).
   Several references stop pretending the file is one thing: the qualifier
   becomes `document` (`… - document.pdf`), overridable as ever by
   `delivery.filename`. The bare name stays reserved for a document that *is*
   the resource, which a mixed dossier is not.
5. **Provenance carries all parents**, in section order, in
   `parent_artifact_ids` — the field is already a list because this decision
   was always coming.
6. **The defaults ladder does not move.** A plain `{"type": "pdf"}` still
   means the ADR 0028 summary; sections exist only when references are
   spelled out. Defaults answer "I didn't say"; sections are the opposite of
   not saying.
7. **`markdown` gets the same lift**, symmetrically: the sections
   concatenated as one `.md` with the same headings. One rule, two renderers,
   no drift.

## Kept out of the contract, deliberately

- **Named presets** ("dossier", "report"): a client checkbox can emit the
  three-output request above; a preset in the contract would freeze one
  opinion of what a report is and leak it into every client.
- **Templates, fonts, layout**: operator configuration (`CONTENT_PDF_*`),
  recorded on the step, never requestable — unchanged from ADR 0028's
  renderer decisions.
- **A new `document` output type**: it would duplicate `pdf`/`markdown`
  entirely to express what an array element already says.

## Consequences

- The planner's render step takes N dependencies instead of one; the builder
  and executor already handle N-ary `depends_on`, so the change is the
  planner's read and the renderer's concatenation.
- The renderer composes Markdown sections under generated headings — string
  assembly before the existing render, not a new engine.
- Capabilities are untouched: sections compose *outputs*, and feasibility of
  each referenced output is already judged on its own. Nothing new to
  announce in `derivation`.
- Tests: section order, per-section renderability refusal, naming for one vs
  several references, TOC on/off, markdown symmetry, provenance order.

## Open questions for the maintainer

1. Is `… - document.pdf` the right qualifier for a multi-section file, or
   should the first section win (`… - summary.pdf` even with a transcript
   appended)?
2. Should `toc` default to true once a document has ≥3 sections, or stay a
   plain opt-in? (Proposal: plain opt-in; defaults that depend on counts
   surprise.)
3. Does `markdown` ship in the same slice or follow? (Proposal: same slice —
   the symmetry is cheap and drift is not.)
