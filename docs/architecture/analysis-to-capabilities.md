# From the source to the capabilities — the underlying logic

How Content goes from **a raw source** to **what a client can actually ask for**,
without the client ever knowing the internal machinery. This is the product's
backbone chain (ADR 0012 for the transformations, ADR 0013 for the resolver). A
reader in a hurry can stop at the diagram and the example.

## Overview

```text
Source (SourceDescriptor)              "here is a URL / a file"
        │
        ▼  providers (yt-dlp, ffmpeg-probe, …) — specific to the source
1. ANALYSIS  ──────────────►  resource FACTS
        │                     (materials present, codecs, languages, duration…)
        │                     ❗ describes what the resource IS, not what can be done with it
        ▼
2. TRANSFORMATIONS  ◄─────────  central registry (logical operations)
        │            + IMPLEMENTATIONS (installed runners, versioned)
        ▼
3. CAPABILITIES  ◄────────────  Capability Resolver
        │                       (facts × registry × implementations × policy)
        │                       ►  "what you CAN produce, here and now"
        ▼   ← this is WHAT THE UI CONSUMES (POST /capabilities)
4. PLANNING  ─────────────────►  a deterministic ExecutionPlan (same recipes)
        │
        ▼
5. EXECUTION ─────────────────►  runners → Artifacts
```

One principle runs through it all: **a single source of truth per fact**. The
providers describe; the resolver and the planner *compute* feasibility by
reading the **same** registry. Nothing is declared twice.

---

## 1. Analysis → facts (`materials`)

Analyzing a source produces a **factual description**
([`SourceAnalysis`](../../apps/backend/content/domain/analysis.py)), never a
feasibility judgement:

- `resource` — the normalized nature (`resource_type`: video / audio / collection
  / …), title, duration, thumbnail;
- `streams`, `subtitles` — the tracks actually present;
- `media` ([`MediaFacts`](../../apps/backend/content/domain/analysis.py)) —
  `has_video/has_audio`, heights, codecs, audio languages;
- `entries` — the items of a collection (a playlist).

These facts determine the **materials** available at the source. A *material* is
a "type" that flows through the engine — the dataflow vocabulary defined in
[`transformations.py`](../../apps/backend/content/planning/transformations.py):

> `source`, `video`, `audio`, `subtitles`, `image`, `metadata`, `transcript`,
> `summary`, `chapters`, `text`

Two YouTube videos therefore produce different facts (one has `en`+`de`
subtitles, the other does not) → different materials → different capabilities.
**Analysis is the basis of everything else**, cached with a TTL and identified
(`analysis_id`).

The providers (yt-dlp, ffmpeg) only **fill in those facts**; they no longer emit
any capability (ADR 0013).

## 2. Transformations (operations) and implementations

A **transformation** is a **logical operation** — a stable verb, independent of
the tool
([`TransformationDefinition`](../../apps/backend/content/planning/transformations.py)):

| Operation | consumes → produces |
| --- | --- |
| `media.acquire_video` | source → video |
| `media.acquire_audio` | source → audio |
| `media.acquire_subtitles` | source → subtitles |
| `media.acquire_thumbnail` | source → image |
| `metadata.export` | source → metadata |
| `text.extract` | source → text *(a web page or a document, read as Markdown)* |
| `video.cut` | video → video |
| `subtitles.to_transcript` | subtitles → transcript |
| `audio.transcribe` | audio → transcript *(defined, with no runner while no STT is installed)* |
| `text.summarize` | transcript → summary |

An **implementation** is a **concrete way** of executing an operation
([`Implementation`](../../apps/backend/content/planning/transformations.py)):
`video.cut` may be carried out by `ffmpeg` (today) or another strategy tomorrow.
Implementations are **not declared by hand**: they are **derived from the
installed runners** (`build_registry(providers)`), and versioned by an
`implementation_version` controlled by Content (the tool's exact version lives in
the provenance, not in the signature). The registry is the **central source**
shared by the resolver and the planner.

## 3. Capabilities — the public vocabulary, computed

A **capability** is a **public action** a user can request (the product's
vocabulary) — distinct from an operation and from an `output_type`. The
**Capability Catalog**
([`catalog.py`](../../apps/backend/content/capabilities/catalog.py)) is the
**only explicit public list**:

> `video.download`, `video.clip`, `audio.download`, `subtitles.download`,
> `thumbnail.download`, `metadata.export`, `transcript.generate`,
> `summary.generate`, `chapters.generate`, `translation.generate`,
> `text.extract`, `markdown.export`

Each capability points to one or more **recipe variants** (`RecipeVariant`) — a
fixed, explicit path of operations. The **alternative paths are named** (R2):

- `transcript.generate` = `transcript.from_subtitles` **or** `transcript.from_audio`;
- `summary.generate` = `summary.from_subtitles`, `summary.from_audio` **or**
  `summary.from_text` (an article needs no transcript at all).

**Availability is never hard-coded**: it is **computed** by the **Capability
Resolver**
([`resolver.py`](../../apps/backend/content/capabilities/resolver.py)) which
crosses:

`SourceFacts × Transformation Registry × Implementation Registry × Instance Policy`

For each capability it evaluates the variants **in order** and keeps the **first
concretely feasible one** (`classify_capability`): a variant is feasible if the
source supplies its materials **and** every operation has an active
implementation allowed by the policy. It returns a
[`ResolvedCapability`](../../apps/backend/content/domain/capability.py):

```jsonc
{
  "id": "summary.generate",
  "status": "derivable",          // available | derivable | unavailable | restricted | unknown
  "selected_variant": "summary.from_subtitles",
  "derived_from": ["subtitles"],
  "derivation": ["subtitles", "transcript", "summary"],
  "reason": null                  // structured when unavailable (see below)
}
```

The per-source envelope of `POST /capabilities` also carries the resource's
identity (`resource_type`, `title`) and **`suggested_filename`** — the base
name the naming engine (ADR 0017) would give this source's artifacts, so a UI
prefills its filename field with an editable proposal instead of
re-implementing the display profile client-side.

`derivation` is the whole material chain the selected variant walks, source
first, artifact last — `["subtitles", "transcript", "summary", "pdf"]` for
`pdf.render.via_summary` (ADR 0028). `derived_from` says where a derivation
starts; `derivation` says what it passes through, which is how a client can
show that a derivable PDF on a video is a PDF *of the summary* rather than
"a PDF from subtitles". It is computed from the transformation registry's
`output_kinds` declarations, never hand-written per capability.

### Statuses and tri-state materials

The presence of a material is **tri-state**: `present` / `absent` / `unknown`.
Hence the statuses:

- `available` — a direct download (the material IS the output);
- `derivable` — produced by transformation from another material
  (carries `derived_from`);
- `restricted` — feasible but blocked by the policy (e.g. cloud disabled);
- `unavailable` — impossible, with a **structured reason**
  ([`CapabilityReason`](../../apps/backend/content/domain/capability.py)):
  - `missing_material` → the **source** is incompatible (the material is absent);
  - `implementation_unavailable` → the source could, but **the installation** is
    missing a runner (e.g. `audio.transcribe`) → *install it*;
  - `policy_restricted` → blocked by the policy → *enable it*;
- `unknown` — **no proven impossibility**, facts insufficient to conclude. Never
  presented as `available`; the UI receives the signal, an attempt is allowed,
  and the uncertainty stays traceable through to execution (rule R8 of ADR 0013).

### Policies

Feasibility takes into account the **instance policies** (e.g. cloud allowed) and
an optional **overlay of request constraints**. The effective policy is their
**intersection**: a request can only *restrict*, never reopen what the instance
forbids ([`policy.py`](../../apps/backend/content/capabilities/policy.py)).

## 4. Planning — the same judgement, on the construction side

The client builds a `GenerationRequest` (output + options) from the capabilities
on offer. The **planner** stays deterministic and recipe-explicit (no generic
solver). Above all, it **shares the same selection** as the resolver (R3) through
[`feasibility.py`](../../apps/backend/content/planning/feasibility.py):
`output_feasibility(...)` calls `classify_capability` — **the planner offers
exactly what `/capabilities` announces**, and an `unknown` capability is
attempted with a `capability_unknown` warning. The resolver answers "**is it
feasible?**", the planner "**how do we do it?**", both through
`Recipe → Registry → Implementations`.

## 5. Execution

The plan (an `ExecutionPlan` of `PlanStep`s) is executed by the runners; the
materials produced are promoted into `Artifacts` through the `output_bindings`.
See [domain.md](../domain.md) §3–§6 for cardinality, the state machine and
provenance.

---

## A concrete example — a YouTube video with subtitles

1. **Analysis**: `resource_type=video`, `media.has_video/has_audio=true`,
   `subtitles=[en, de]`, thumbnail present. Available materials: `video`,
   `audio`, `subtitles`, `image`, (`source` → `metadata`).
2. **Registry**: `media.acquire_*`, `metadata.export`, `video.cut`,
   `subtitles.to_transcript`, `text.summarize` have a runner;
   `audio.transcribe` does not.
3. **Resolution** (`POST /capabilities`):
   - `video.download`, `audio.download`, `subtitles.download`,
     `thumbnail.download`, `metadata.export`, `video.clip` → **available**;
   - `transcript.generate` → **derivable** through `transcript.from_subtitles`,
     `derived_from=[subtitles]`;
   - `summary.generate` → **derivable** through `summary.from_subtitles` (if a
     summarizer is active; otherwise `unavailable / implementation_unavailable`,
     or `restricted` if only a cloud runner exists and the policy forbids it).
   - Without subtitles, `transcript.generate` would become **unavailable /
     implementation_unavailable [audio.transcribe]** — the source could, but no
     STT is installed (a fix for the old over-promise).
4. **Planning**: the user asks for a `summary` → the planner selects the **same**
   `summary.from_subtitles` variant and builds the chain
   `acquire_subtitles → to_transcript → summarize`.
5. **Execution**: the steps produce the transcript (internal) then the summary
   (the delivered artifact).

## The guard rails (invariants)

A summary of the normative rules, detailed in
[ADR 0013](../architecture-decisions/0013-capability-resolver.md):

- **R1** one recipe = a single declaration, read by the resolver **and** the planner;
- **R2** explicit alternative paths (`*.from_subtitles` / `*.from_audio`);
- **R3** resolver and planner share the deterministic selection;
- **R4** request constraints can only restrict (intersection);
- **R5** the static option schema ≠ the dynamic domains (the source's values);
- **R6** the catalog is the only public list; providers ⇒ facts, never capabilities;
- **R7** anti-drift tests (capability→recipe→operations→implementation);
- **R8** the semantics of `unknown` (never `available`, traceable).
