# ADR 0017 — Artifact naming is the engine's job

Status: accepted (2026-08-07) · Refines ADR 0004 (cardinality) · Prepares ADR 0018

## Context

The engine produces artifacts; the *names* users see were never designed. An
audit of the current state (2026-08-07):

- Files in the job store are named after implementation details:
  `<output_id>[-<item_label>][.<language>]<ext>` (`executor.py`,
  `_promote_and_register`). That is where `video_main.webm` — the name the
  extension's first real download surfaced — comes from.
- The API download endpoint serves that technical name as the browser filename
  (`FileResponse(..., filename=artifact["filename"])`), so the bad name
  reaches users **even when delivery is not involved at all**.
- Delivery names files `delivery.filename or output.id`: when the client says
  nothing, the implementation detail leaks into the media library.
- Because the engine offers no naming, every client invents it: HomeTube
  prefills `resource.title` (and 422s on titles containing `/` — D-51), the
  browser extension carries its own sanitizer (`lib/filename.js`) and refuses
  to submit without a name, and bare SDK/CLI/curl users get `video_main.webm`.
  Three clients, three copies of the same logic, one broken.
- Yet the engine is the only party that holds the semantic material: the
  analyzed resource title (the plan references `analysis_id`), the output
  types, the provenance chain (`parent_artifact_ids`, producer), the language
  attributes and per-item labels.

Naming is a *semantic* function of information the engine owns. It does not
belong in clients.

## Decision

**1. Every Artifact has a user-facing filename, as a property.**

A new `display_filename` is computed for every artifact and stored on the
artifact row. It is exposed by the API, used as the download filename
(`Content-Disposition`), shown by UIs, and reused by delivery (ADR 0018). It
exists whether or not the artifact ever leaves the job store.

The **physical** name inside `jobs/<id>/artifacts/` stays technical and
id-based. The job store is machine-managed and addressed through the database;
renaming its files would buy nothing users can see and would import
title-collision handling into the source-of-truth tree. Naming is metadata
about the artifact, not a storage layout.

**2. A dedicated naming module: `content/naming/`.**

The ArtifactNamer is a pure, deterministic, domain-level module — no FastAPI,
no providers, no filesystem access. Two entry points:

- `resolve_naming_plan(request, analyses) -> NamingPlan` — used by the planner;
- `bind_filename(naming_plan, output_id, *, extension, language, item_title,
  item_index, item_count) -> str` — used at artifact registration.

**3. Two-phase resolution: plan the intent, bind the facts.**

- **Planning** resolves a `NamingPlan` and records it in the ExecutionPlan (and
  therefore in the plan snapshot — inspectable, deterministic): the base name
  and each output's qualifier. The base name follows a deterministic fallback
  chain: **analyzed resource title → source filename → provider resource id →
  output id**.
- **Binding** happens when the executor registers the artifact, because only
  execution knows the final extension (the container actually produced), the
  concrete languages, and the cardinality (one artifact or many — ADR 0004).
  Binding is mechanical template instantiation; the executor takes no naming
  decisions.

**4. Deterministic naming rules.**

With base name `B` (e.g. `My Conference`):

- **Primary output, no qualifier.** The primary output of a request is the
  first present in a fixed precedence list owned by the naming module
  (`video, audio, markdown, document_text, pdf`) — the renderings of *the
  resource itself*. It binds as `B.<ext>`. `pdf` sits last so "the page + a
  PDF of it" binds `B.md` + `B.pdf` rather than crowning the rendering, and a
  PDF is only eligible when what it presents is eligible: a PDF of the summary
  is a summary and keeps that word.

  An artifact *about* the resource — transcript, summary, translation,
  subtitles, chapters, metadata, thumbnail, keyframes — is never bare, **even
  when it is the only output requested**. See the amendment below.
- **Every other output carries its semantic qualifier**: `B - audio.opus`,
  `B - summary.md`. The qualifier names what the file *is*, not how it was
  made.
- **Presentation outputs inherit the qualifier of the declared output they
  derive from** (via `from_outputs`): a PDF rendering of the summary binds as
  `B - summary.pdf`, never `B - pdf.pdf` — the extension already carries the
  format. This applies to *presentation* types only (`pdf`, `translation`); a
  semantic transformation (a summary of the transcript) keeps its own name —
  a summary is not a transcript.
- **Language is appended when the artifact carries one**:
  `B - subtitles - en.srt`. Subtitles and translations are inherently
  language-addressed; the qualifier is information, not noise.
- **Numbering appears only when cardinality is the sole distinguisher**: a
  single thumbnail is `B - thumbnail.jpg`; several keyframes are
  `B - 01.jpg`, `02`… zero-padded, stable ordering. Language-addressed
  siblings (one subtitles file per language) are already distinct and take no
  number.
- **Per-item scopes use the item's own title as base** when the analysis
  provides one (a playlist entry is its own resource); the parent title plus
  the item label is the fallback.
- **Residual collisions** (two declared outputs of the same type over the
  same base) qualify every duplicate after the first with its own output id —
  deterministic, and it names the thing the user themself declared. A
  tautological qualifier (a second video labeled "video") never appears.

**5. One sanitizer, two profiles, server-side only.**

Sanitization remains exclusively the backend's job, in one module the naming
engine and the storage layer both import. The current conservative allowlist
(`[^A-Za-z0-9._-] → _`) is right for technical storage names but wrong for
display names — it is what turned a real delivery into
`Example_Domain_-_Test_Page.md`. The naming engine uses a **display profile**:
spaces and unicode letters preserved; path separators, control characters,
leading/trailing dots and reserved names removed; length-capped. Client-sent
name intent (today `delivery.filename`) goes through the same display profile —
*sanitized, not rejected*, which settles D-51 the way both docstrings already
promise.

## Consequences

- `video_main.webm` becomes impossible: API downloads, UIs and the delivery
  library all show `My Conference.mp4`-class names, with no client effort.
- HomeTube's title prefill and the extension's `lib/filename.js` +
  mandatory-filename rule become deletable; naming works for clients that do
  not even know it exists (SDK, CLI, MCP, curl).
- `display_filename` is additive in the API; the download header changes from
  the technical name to the display name — user-visible, but filenames were
  never contract identifiers (the artifact id is).
- The NamingPlan is visible in the plan snapshot, so a naming decision can be
  audited before execution runs.
- Delivery (ADR 0018) stops inventing names: it copies an artifact that
  already knows what it is called. Naming stays fully independent of delivery.

## Amendment (2026-08-21) — a lone output is not automatically primary

The first implementation carried two extra rules that the text above no longer
has: a single-output request named its output bare whatever its type, and
`transcript`/`summary` sat in the precedence list. Together they produced this,
observed in a real delivery library:

```text
Random tour through the Blender Institute - en.json      ← a transcript
twenty one pilots Stressed Out … - transcript - en.json  ← also a transcript
```

Both are transcripts. The first was asked for alone, the second beside a video.
A user browsing the folder cannot tell the first from anything else that
happens to be English and JSON — and the same type landing under two different
names depending on its neighbours is the opposite of what this ADR is for.

The rule is now about **what the artifact is**, not about how many were asked
for: only a rendering of the resource itself can take the bare name, and among
those the precedence decides (there can be only one). Everything else says what
it is, always.

Note that the ADR's own examples already assumed this — "a single thumbnail is
`B - thumbnail.jpg`" was written here from the start, while the code named a
lone thumbnail `B.jpg`. The implementation, not the decision, was the thing out
of line.

**Not retroactive.** Files already in a library keep their names; the library
is the user's (ADR 0023). Anyone keying automation on a delivered filename of a
transcript-, summary- or subtitles-only request will see the qualifier appear —
`docs/releases/` carries it as a visible change.
