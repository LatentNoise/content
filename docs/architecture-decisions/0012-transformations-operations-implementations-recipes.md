# ADR 0012 — Transformations: logical operation, implementation, recipe, execution

Status: accepted (2026-07-28) — migration in progress (recipes extracted one by one)

## Context

The engine turns resources into artifacts through a deterministic
`ExecutionPlan` (steps, `depends_on`, topological order, sharing by signature).
But the transformations were **implicit and scattered**: recipes hard-wired into
a ~1400-line `planner.py`, dispatch inside the providers, options in the
contract, no central representation; confusion between *provider / tool /
processor / implementation*; **macro** `media.acquire_*` operations (download +
remux + embed + sponsorblock all at once). We want to make the architecture
explicit **without** building a premature generic graph solver.

## Decision — four distinct concepts

1. **Logical operation** (`operation`) — *what must be done*, a stable verb
   independent of the provider: `media.acquire_video`, `subtitles.to_transcript`,
   `text.summarize`, **`video.cut`**…
2. **Implementation** — *who knows how to do it*: a runner (`ytdlp`, `ffmpeg`,
   `ollama`, `content.transcript`) that declares operations and executes a
   `PlanStep`. An identity **versioned by Content** (`implementation_version`),
   distinct from the tool's exact version.
3. **Planning recipe** — *how to compose* operations to satisfy an output
   (`planning/recipes/*.py`).
4. **Execution** — running the already-resolved steps
   (`execution/executor.py`), where a step's materials feed its dependents.

### Central registry (`planning/transformations.py`)

A single source of truth: one `TransformationDefinition` per operation
(`input_kinds`/`output_kinds` = the *shape* of the dataflow, **not**
feasibility: codecs/containers/precision stay in the recipes; `deterministic`,
`cacheable`, `lossy`, `macro`, extensible `properties`). **Implementations are
derived from the installed runners** (`build_registry`) → no independent
declaration that could drift; `missing_registrations` plus a test guarantee it.

### Signature & versions

The content-addressed signature now includes the **`implementation_version`
controlled by Content** (to be bumped when the produced bytes change for the
same inputs). The **tool's exact version** (yt-dlp/ffmpeg build) goes into
**provenance**, not into the signature — so a tool update does not invalidate
the whole cache; a Content bump does.

### Generalized PlanBuilder (`planning/builder.py`)

Primitives: `ensure_step(operation, implementation, inputs, params, …)`
(creates/shares, **validates against the registry** → `UnknownTransformation` if
the operation/implementation is unknown or incompatible) and
`bind_output(output_id, step_id)`. Sharing, signatures based on the
dependencies' signatures, `required` propagation and bindings kept separate from
steps are all preserved. `bound_step`/`acquisition_step` remain as transitional
wrappers.

## `video.cut` — the first atomic, composable transformation

`video → video`, implemented by ffmpeg (`stream copy`, `keyframes` mode;
`precise` = re-encoding → `option_not_supported`). The recipe composes:

```text
URL  : media.acquire_video (internal)  →  video.cut (bound)  → output
file : video.cut (reads the file directly)                   → output
```

The **same** transformation composes over a video coming from a URL *or* from a
file, without re-coding it — the matter (the video material) is agnostic to its
origin.

## Extension rules (adding a transformation)

1. **Operation**: a constant plus a `TransformationDefinition` in
   `transformations.py` (kinds + properties; do not encode feasibility).
2. **Implementation**: a runner declares the operation (`operations`) and
   handles it in its `execute`; the implementation is derived automatically.
3. **Recipe**: compose through `ensure_step`/`bind_output` (never provider
   syntax in the recipe/planner).
4. **Do not** bury a new transformation inside a macro-op (`media.acquire_*`).
5. **Tests**: determinism, sharing, signature, composition (source-agnostic),
   operation/implementation isolation.

## Consequences

- Every transformation can be added cleanly, tested in isolation, composed into
  several recipes and executed by one or more implementations — without
  inflating `planner.py`.
- The signature change (adding `implementation_version`) invalidates the
  existing cache: accepted (dev), documented.
- Accepted debt: `media.acquire_*` is still **macro**; the persistent
  `PlanStep.provider → implementation` rename is deferred (transitional
  compatibility); only the **video** recipe is extracted (as proof) — the other
  recipes will migrate one by one.

## Alternatives rejected

- **A generic graph solver** (typed path-finding): elegant but premature with
  ~8 operations; risk of non-determinism (multiple paths); we keep explicit
  deterministic recipes, with the "typed graph" target remaining a possible
  evolution with explicit cost/priority.
- **Mechanically moving** the 1400 lines before stabilizing the abstractions:
  refused — we migrate one recipe at a time behind the stable API.
