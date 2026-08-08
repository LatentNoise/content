# ADR 0013 — Capabilities: a public catalog + a derived resolver

Status: accepted (2026-07-28) — phases 0→5 delivered (5a: `ResolvedCapability`
frozen in the OpenAPI; 5b: API client extracted into `packages/content-client`,
consumed by the 3 UIs).

> **Superseded in part by [ADR 0015](0015-sdk-cli-mcp-layering.md).**
> `packages/content-client` no longer exists: it was replaced by
> `packages/python-sdk` (`content_sdk`), the one official API client, which the
> UIs reach through `content_sdk.compat`. Nothing about the resolver itself
> changed — only the package that consumes it.

## Context

ADR 0012 made the **transformations** explicit (operation / implementation /
recipe / execution) and introduced a central registry. What was missing was the
**outward projection**: how a client (HomeTube, the future Content Studio) knows
*what it is possible to produce* from a source, without embedding business
logic.

Today `Capability` objects are **emitted by the providers** (yt-dlp, ffmpeg) and
completed ad hoc in `AnalysisService._enrich_with_installation`. That is
_hardcoding per source type_: the provider decides "what is possible", when it
should only describe "what the resource **is**". The result: two rule books
(frontend + backend) that drift, and a capability == `output_type` unable to
carry the product's public vocabulary of **actions**.

## Decision — the five-stage flow

```
Source → Analysis (facts) → Capability Resolver → Planning → Execution
```

- **The user never knows about the internal transformations.** They supply
  sources; the backend answers *what the source contains*, *what can be
  produced*, *which options*, *which constraints*.
- **Analysis produces FACTS only** (materials, codecs, languages, subtitles,
  duration, resolution, entries, metadata). It no longer declares any
  capability.
- **The Capability Resolver** crosses `SourceAnalysis × Transformation Registry ×
  Implementation Registry × Capability Catalog × Instance Policies` and returns
  the list of resolved capabilities. It is the **only** input to the UI.
- **The planner** is unchanged in philosophy: deterministic, explicit recipes,
  **no generic graph solver**.

### Three framing decisions (validated)

1. **`GenerationRequest` stays `output + options`.** A capability is a *public
   offer* that produces a **request fragment** (output + option defaults),
   declared by the catalog — not the engine's core contract.
2. **Resolution lives in a dedicated endpoint** (`POST /capabilities`), distinct
   from analysis: analysis becomes purely factual and cacheable again;
   resolution depends on the installation (a daemon starting/stopping changes
   availability) and is recomputed on read.
3. **The resolver takes the instance policies + an optional overlay of request
   constraints.**

## Components and responsibilities

| Component | Receives | Produces — single responsibility | Does not do |
|---|---|---|---|
| **Source Analysis** (`analysis/`, providers) | SourceDescriptor | Resource **facts** + `resource_key` (TTL cache) | ❌ declares no capability |
| **Transformation Registry** (`planning/transformations.py`) | — static | The **internal** vocabulary of operations | ❌ knows neither tools nor concrete feasibility |
| **Implementation Registry** (derived from the runners) | `providers.describe()` | "who executes operation X, available now?" + `implementation_version` | ❌ composes nothing |
| **Recipe** (single declaration) | the targeted capability | An **operation DAG** interpreted by the resolver *and* the planner | ❌ no solver; ❌ no double source of truth |
| **Capability Catalog** (`capabilities/catalog.py`) | — declared | The **public vocabulary**: `id`, title, description, variants, output produced, **static** option schema | ❌ declares no feasibility |
| **Capability Resolver** (`capabilities/resolver.py`) | Analysis × Registry × Impl × Catalog × Policies | Per capability: `{status, selected_variant, derived_from, options(domains), constraints, warnings}` | ❌ does not build the plan |
| **Planner** (`planning/planner.py`) | the chosen outputs | A deterministic `ExecutionPlan` through the recipes | ❌ does not judge "should we offer it" |
| **Execution** (`execution/`) | the plan | runners execute | — |

## Invariant rules (design constraints)

These rules are **normative** — every evolution must preserve them; anti-drift
tests guard them.

- **R1 — One recipe = a single declaration.** It is forbidden to have a
  declarative `describe()` and a procedural `build()` that could diverge. A
  recipe (or variant) is declared **once** (its *shape*: ordered operations,
  required input materials, option groups) and interpreted by the resolver
  (feasibility) **and** the planner (construction). The option *domain*
  (resolver) and the *options→params mapping* (planner) are two
  **non-overlapping projections** of the same static schema — not
  redeclarations.
- **R2 — Explicit alternative paths.** A capability may have several named
  variants (`summary.from_subtitles`, `summary.from_audio`). The resolver must
  identify **at least one concretely feasible variant** — not abstractly
  conclude "available". It exposes the variant it selected.
- **R3 — Planner and resolver share the selection.** Variant and implementation
  selection is a **shared deterministic function**: the planner builds exactly
  the variant the resolver deemed feasible. A capability announced as available
  cannot then fail through divergence.
- **R4 — Request constraints can only restrict.** Effective policy =
  `instance_policy ∩ request_constraints`. A request can never relax an instance
  policy.
- **R5 — Static schema ≠ dynamic domains.** The catalog/recipe describes an
  option's **type and semantics**; the resolver supplies the **allowed values,
  bounds and defaults** specific to the analyzed source.
- **R6 — The catalog is the only public list.** Providers no longer produce
  capabilities; clients never redeclare feasibility.
- **R7 — Anti-drift tests.** Guarantee that: (i) every capability references an
  existing recipe; (ii) all of a variant's operations are registered in the
  registry; (iii) every variant announced as *available* has at least one
  resolvable compatible implementation.
- **R8 — Semantics of `unknown`.** `unknown` means "no proven impossibility,
  attempt allowed" — never "available". Rules:
  - `unknown` is **never** presented as `available`/`derivable` (a distinct
    status in the public contract and in the planner's feasibility);
  - it is **reserved** for facts insufficient to conclude — a known cause
    (missing material, no implementation, policy) yields `unavailable`/
    `restricted` with a structured reason, never `unknown`;
  - the UI **receives the signal**: an `unknown` status on `/capabilities`, and
    a `capability_unknown` warning at submission;
  - the uncertainty is **traceable** end to end: the `capability_unknown`
    warning is carried by the plan (snapshot) and the `job.planned` event, so
    that an execution failure remains correlatable to the speculative attempt.

## A single source of truth (not three lists)

Every fact lives in **one place**, along a chain — not as copies:

- operations → `DEFINITIONS` (the registry);
- implementations → **computed** from the runners (0 lists);
- public actions → the **Capability Catalog** (1 explicit, authorized list);
- compositions → the **recipes** (1 declaration per variant, R1);
- on the client side → **nothing** (the UI reads the resolver's output, R6).

The resolver answers "**is it feasible?**", the planner "**how do we do it?**",
both through `Recipe → Registry → Implementations` — hence without duplication
(R1/R3).

## Multi-client

- **Generic web client (Studio)**: renders the resolved capabilities
  generically, **zero** YouTube/PDF logic.
- **Specialized clients (HomeTube)**: subscribe to a **subset** of `id`s and
  give them a bespoke rendering, always driven by `status`/`options`. They hide,
  preconfigure, restyle — but **never** redeclare feasibility (ADR 0011). The
  `cut` hidden for a playlist becomes data (`video.clip` `unavailable` on a
  collection), no longer an `if` in the frontend.

## Consequences

- Analysis must **expose as facts** what is buried today in
  `capability.details` (heights, codecs, audio languages) so the resolver can
  compute the domains (R5). Migrated in phase 4.
- `audio.transcribe` is **defined** in the registry (audio→transcript) but
  **without an implementation** as long as no STT runner is installed: the
  `*.from_audio` variants are thus declared and explicitly `unavailable`
  (R2/R7), ready to light up as soon as a runner arrives — without touching the
  catalog. *Verified in practice*: the optional Whisper runner (`[stt]`,
  `providers/whisper.py`) activates them exactly that way.
- `POST /capabilities {sources, constraints?}` is added; `POST /analyses`
  remains.

## Migration plan (incremental, tests always green)

- **Phase 0** — this ADR; name the Implementation Registry; extend
  docs/domain.md.
- **Phase 1** — **Capability Catalog + declarative recipes + static option
  schema + anti-drift tests (R7).** Inert data, providers/planner unchanged.
- **Phase 2** — **CapabilityResolver** as a pure function + policies
  (R4 intersection) + deterministic variant selection (R3); unit tests
  reproducing the current statuses on fixtures. Not wired in.
- **Phase 3** — wiring in **dual emission** (`/capabilities` + provider
  capabilities kept); an **equivalence** test.
- **Phase 4** — **switchover**: providers → facts only; the resolver is the sole
  producer; the `summary` special case is removed; the frontends read the
  resolved capabilities; the old path is deleted.
- **Phase 5** — the `ResolvedCapability` shape frozen in the OpenAPI; shared
  client/renderer extracted into `packages/` when Studio arrives.

## Alternatives rejected

- **A generic graph solver**: deferred (R1/deterministic planner). The recipes
  stay explicit.
- **Capabilities emitted by the providers**: that is the current state —
  rejected (R6, drift, hardcoding per source type).
- **A generic JSON-Schema form engine in the frontend**: heavy, generic UX,
  violates "no broad unjustified abstraction". The catalog is a domain
  vocabulary, not arbitrary JSON-Schema.
- **Outputs referencing a `capability_id`** in the core contract: churns the
  contract and the planner — rejected (framing decision 1).
