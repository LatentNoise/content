# ADR 0019 — Collections orchestrate, they do not generate

Status: accepted (2026-08-09) · Supersedes the `each_item` planning of ADR 0007's
first slice · Related: ADR 0013 (capability resolver), ADR 0014 (addressable
analyses), ADR 0017 (naming)

## The invariant

> **A collection never generates artifacts. It orchestrates the canonical
> single-resource pipeline for each of its members.**
>
> Every concrete resource is analyzed and deterministically planned before its
> own execution begins. Nothing about a member is guessed.

## Context

A playlist is analyzed with `yt-dlp --flat-playlist`: members come back as
references (id, title, url) and **no facts**. No `audio_languages`, no
`original_audio_language`, no `video_heights`, no `video_codecs`, no
`subtitles`.

The first `each_item` slice planned around that absence instead of resolving
it. `_plan_each_item` builds one acquisition step per member from
`_each_item_video_params` / `_each_item_audio_params`, which invent what the
analysis does not know: codec preferences are stamped `available: True`
regardless of what the member offers, language lists are passed through with no
intersection, and no capability is ever resolved. 228 lines of planner, plus 11
`is_collection` branches in HomeTube, exist only to serve that guess.

The cost is not theoretical. Every playlist defect reported so far came from
this second pipeline, and each fix patched the copy rather than the original:

- members downloaded with a single audio track and no subtitles, because the UI
  had no track list to offer and therefore sent no language intent at all;
- `VO_FIRST` silently unhonoured, because "original" is a per-video fact the
  collection cannot carry;
- the resolution and codec ladders offered from a hardcoded fallback rather
  than from the member's real formats;
- no capability resolution, so a member that cannot produce a requested output
  fails at execution instead of answering honestly up front.

A second pipeline that only ever approximates the first is a defect generator.

## Decision

**Collections become orchestration over the canonical pipeline.**

```text
collection analysis
    → cheap, flat discovery of member resource references

each_item generation
    → per member, in order, with bounded concurrency:
         canonical source analysis      (AnalysisService, cached per resource)
         → capability resolution        (ADR 0013)
         → single-resource ExecutionPlan (build_plan, unchanged)
         → normal execution             → artifacts
```

Two plan levels, deliberately distinguished:

| | Resolved | Contains |
| --- | --- | --- |
| **Collection plan** | at submission, deterministically | the member references, the requested outputs, ordering and concurrency |
| **Per-item plan** | when that member enters execution | the ordinary `ExecutionPlan` for one concrete resource |

This keeps the planning invariant that matters — *a concrete resource is fully
resolved before its execution begins* — without requiring hundreds of probes
before a large playlist can produce anything. Determinism was never a claim
about *when* planning happens; it is a claim that planning is a pure function
of (request, analysis). That holds per member.

### What is explicitly refused

- **No optimistic fallback.** There is no size at which the engine reverts to
  guessing codecs, languages or capabilities. Correctness must not depend on
  how long a playlist is.
- **No eager whole-collection probing.** Analysis happens as each member enters
  execution, bounded to 1–2 concurrent members initially. The analysis cache
  (`CONTENT_ANALYSIS_TTL_HOURS`) applies per resource, so re-submitting a
  playlist re-reads rather than re-probes.
- **No duplicated planner logic.** A member is planned by `build_plan`, the
  same function a single video uses. If per-item planning ever needs a rule the
  single-resource path lacks, that rule belongs in the single-resource path.

### Partial results are the normal answer

When a member cannot satisfy every requested output — no subtitles on that
video, no LLM for a summary — the ordinary per-item capability behaviour
applies: the possible outputs are produced, the impossible ones are reported as
structured `partial_output` warnings against that member. A member failing does
not fail the collection; the failure policy already in the contract governs
that.

### `each_item` stops meaning "video or audio"

The restriction to `video` and `audio` was a property of the guessing code: it
was the only pair whose parameters could be invented without facts. With real
per-member analysis there is no such limit, and the model must not encode one —
any output the single-resource pipeline supports is meaningful per member
(subtitles, transcript, summary, translation, thumbnail, metadata…).

The first implementation slice may still *test* a smaller subset, but the
restriction, if any remains, is a temporary implementation note and not an
architectural rule.

## Audit: what is reused, what must be built

The audit asked for before implementation. Conclusion: **the existing services
are reusable directly. No planner logic is duplicated. A small orchestration
layer is needed, and it plans nothing.**

Reusable unchanged:

| Service | Signature | Fit |
| --- | --- | --- |
| `AnalysisService.analyze_sources` | `[SourceDescriptor] → ResourceAnalysis` | A member is an ordinary `url` source. Caching is keyed per resource already. |
| `build_plan` | `(request, analysis, providers, settings) → ExecutionPlan` | Pure. Given a member request and its analysis, it yields the ordinary plan. |
| `CapabilityResolver` | facts → capabilities | Applies to a member exactly as to a single video. |
| The executor's step loop, reuse (`reuse_existing`), naming (ADR 0017), delivery (ADR 0018) | — | Per-member steps are ordinary steps: content-addressed signatures already make each member independently reusable. |

To build — small, and none of it plans:

1. **`derive_member_request(collection_request, member_ref) → GenerationRequest`**
   — a pure derivation: swap the collection source for the member's `url`
   source (carrying the same `auth`), drop the `each_item` scope to `single`,
   keep the options, keep the delivery folder. This is the whole "collection
   orchestration abstraction": request rewriting, not planning.
2. **A member step in the collection plan**, whose execution runs
   analyze → `build_plan` → the ordinary step loop for that member's plan, and
   binds the resulting artifacts to the collection's output id (the existing
   `per_item` binding already expresses "one output, N artifacts").
3. **Bounded concurrency** over members, conservative to start (1–2), so a long
   playlist does not become a probe storm against the provider.

One question the implementation must settle rather than assume: **member
numbering**. Today `item_label` (`001-first`) is a planner-invented parameter.
Under this model the per-item plan names the artifact from the member's own
resource (ADR 0017), and the collection contributes the ordinal. The ordinal is
orchestration data — it belongs to the collection plan, not to the member's
naming.

## Becomes deletable

Planner (`content/planning/planner.py`, the `each_item` block, 228 lines):

| Symbol | Why it goes |
| --- | --- |
| `_each_item_video_params` | Exists only to invent video parameters without facts |
| `_each_item_audio_params` | Same, for audio |
| `_EACH_ITEM_OPERATIONS` | Encodes the video/audio-only restriction this ADR removes |
| `_plan_each_item` | Replaced by emitting member steps; the planning it did is `build_plan`'s job |

HomeTube (`apps/web-hometube/app.py`, 11 `is_collection` references). Deletable
once members resolve capabilities like any source:

| Line | Branch | Fate |
| --- | --- | --- |
| 499 | hardcoded Video / Audio radio instead of capability checkboxes | **deleted** — capability-driven, like a single video |
| 644 | the playlist-only audio-language selector | **deleted** — the real track list is known per member |
| 700 | the playlist-only subtitle selector | **deleted** — same |
| 767 | cutting hidden for collections | **deleted** — a member is a video like any other |
| 983 | `scope: each_item` injection for video/audio only | **simplified** — the scope applies to any output |
| 996 | sidecar subtitles suppressed for collections | **deleted** |
| 403, 408, 439, 552, 1020 | detection, labels, the entries expander, the button text | **kept** — presentation of a collection, which stays a real distinction |

The net effect is that "is this a collection?" stops being a question the
*generation* code asks, and remains only a question the *presentation* asks.

## Consequences

- One pipeline to keep correct. A fix to the single-resource path reaches
  playlists by construction, which is the property this ADR exists to buy.
- Members become fully honest: real codecs, real languages, real subtitles,
  real capabilities, real VO — and structured warnings when something is
  genuinely unavailable.
- A member's analysis is a cache entry, so re-submitting a playlist is cheap,
  and reuse (`reuse_existing`) already deduplicates the work per member.
- The first member starts producing without waiting for the whole collection to
  be probed.
- Cost: a member's plan is not known at submission time, so the collection's
  total step count is not either. The collection plan states the members and
  the outputs asked of them; per-member detail appears as each is resolved.
  Job progress therefore reports members, then steps within a member.
- `--flat-playlist` stays exactly where it belongs: discovery of references,
  which is all it was ever good for.
