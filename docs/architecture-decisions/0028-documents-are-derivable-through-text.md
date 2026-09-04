# ADR 0028 — A pdf or markdown is derivable through the text a source can produce

Status: accepted (2026-09-04) · Amends the stance recorded in the capability
catalog under ADR 0013 · Naming consequences follow ADR 0017/0018

## Context

The engine could always execute "summarize this video, then render that as a
PDF" — the composition proved it daily:

```json
[{"type": "transcript", "id": "t"},
 {"type": "summary", "id": "s", "from_outputs": ["t"]},
 {"type": "pdf", "from_outputs": ["s"]}]
```

Yet the plain form, `[{"type": "pdf"}]` on a video, was refused at feasibility
(`capability_unavailable`, *"'pdf' cannot be produced from source 'main'."*),
and `POST /capabilities` reported `pdf.render` and `markdown.export` as
`unavailable` with `missing_material: ["text"]` — while, three lines above,
`summary.generate` said `derivable`.

The catalog was explicit that this was a decision, not a gap: *"rendering
another output is a composition expressed with `from_outputs` — it is not a
property of the source."* That line drew the boundary at the source's own
materials. `transcript.generate` and `summary.generate` had already crossed it:
a video carries no transcript either, yet both are announced as `derivable`
because a chain of operations reaches them. Documents were the only outputs
judged by what the source *is* rather than by what it can *yield* — an
inconsistency, and one that surfaces exactly where it hurts: an agent fronting
the engine reads the capability feed and tells the user, falsely, that a PDF
of this video is impossible.

## Decision

**A capability is judged by what the source can yield through declared
recipe variants — for documents like for everything else.**

1. `pdf.render` gains `via_summary` (subtitles → transcript → summarize →
   render) and `via_summary_stt` (audio → transcribe → summarize → render)
   variants. On a text-less source they resolve to `derivable`, with
   `selected_variant` naming the path and `derived_from` the material — the
   existing schema, no new reason code.
2. `markdown.export` gains the same pair, minus the render step: the summary's
   canonical format already is Markdown.
3. **The summary is the default derivation, deliberately.** A PDF of a whole
   transcript is rarely what a person means by "a PDF of this video". The
   precise forms — transcript, translation — remain one explicit
   `from_outputs` away, and that form **always wins** when declared.
4. The planner honours the announcement (R3). A plain `pdf` on a text-less
   source first looks for a text output already in the request (a summary,
   then a transcript, then a translation, same source) and renders that —
   folded into the resolved references, so it behaves exactly like the
   declared composition. Otherwise it synthesizes the default summary chain,
   through the same helper `summary` outputs use, so the two cannot drift.
5. `text.extract` is exempt, deliberately: *extract* means text the source
   itself carries. A video still answers `unavailable` there, and that answer
   is the honest one.
6. When nothing can produce text, the refusal now says so in user terms:
   *"the source carries no text, subtitles or audio to build it from"*, or
   names the missing runner (`audio.transcribe`, `text.summarize`) or the
   blocking policy — instead of the bare *"cannot be produced"*.

## Naming

An implicitly derived document is named for what it contains, exactly as the
declared composition names it: `Talk - summary.pdf`, never `Talk.pdf`. The
bare name claims "this file *is* the resource", and a summary is not the
resource (ADR 0017's inheritance rule, extended to the planner's implicit
derivations via the plan's naming entries). Identical request intent —
declared or plain — produces identically named artifacts.

## Consequences

- `POST /capabilities` on a video now reports `pdf.render: derivable
  (pdf.render.via_summary)` and `markdown.export: derivable`; clients that
  render the feed (the MCP server passes it through untouched) start offering
  documents on media sources with no client-side change.
- A plain `{"type": "pdf"}` on a subtitled video plans
  `acquire_subtitles → to_transcript → summarize → render_pdf`; asking for
  `markdown` and `pdf` together costs one summarization, shared by signature.
- An installation without an LLM runner refuses the plain form and names
  `text.summarize` as the missing piece; the transcript-rendering composition
  keeps working there, since it needs no LLM.
- The capability feed stays honest under policy: a cloud-only summarizer with
  cloud disallowed turns the documents `restricted`, same as the summary.

## Rejected

- **`via_transcript` fallback variants** (render the transcript when no LLM is
  installed): silently substituting a 40-page transcript for the summary the
  default promises answers a question nobody asked. The explicit form exists
  for exactly this.
- **A new reason code (`via`) for the derivation path**: `selected_variant` +
  `derived_from` already carry it within the schema clients know.
- **Mutating the request to insert a summary output**: a GenerationRequest is
  user intent; the derivation is strategy, so it lives in the ExecutionPlan's
  steps and naming entries only.
