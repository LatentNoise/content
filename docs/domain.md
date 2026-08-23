# Content's domain

Content is a **declarative engine that transforms resources into artifacts**: the client declares what it supplies and what it wants to obtain; the engine determines the technical strategy.

```text
Sources → Analyses → Capabilities → GenerationRequest → Feasibility Validation
        → ExecutionPlan → Job → Artifacts
```

This document defines the concepts, their invariants and the state machines. The public contract (JSON schemas) is in [contract.md](contract.md), the architecture in [architecture.md](architecture.md).

## 1. Concepts

| Concept | The question it answers | Nature |
| --- | --- | --- |
| `SourceDescriptor` | What does the client supply, and how? | Public contract (input) |
| `ResourceAnalysis` | What **actually is** the resource? | Analysis result, normalized |
| `ResolvedCapability` | What can be produced from this resource, here and now? | **Computed** by the Capability Resolver (facts × registry × implementations × policy), exposed through `POST /capabilities` — ADR 0013 |
| `GenerationRequest` | What does the client want, from what, under which preferences/constraints? | Public contract (input) |
| `ArtifactRequest` | A **requested** result (an element of `outputs[]`) | Public contract (input) |
| `ExecutionPlan` | How does the engine intend to satisfy the request? | Internal, persisted as a snapshot |
| `PlanStep` | A resolved operation (operation + provider/processor + dependencies) | Internal |
| `Job` | A persistent execution of the plan | Internal, exposed read-only |
| `JobStep` | The execution of a PlanStep | Internal, exposed read-only |
| `JobEvent` | A timestamped, ordered fact that occurred during the job | Internal, exposed (SSE/poll) |
| `Artifact` | A **produced** result, with provenance | Exposed read-only + content |

### Fundamental invariants

- `GenerationRequest ≠ ExecutionPlan`: the intent never contains a provider, a tool, a command or a path. The plan does.
- `GenerationRequest ≠ Job`: the same request may be planned/executed several times; the job carries the execution state, never the intent.
- `ExecutionPlan ≠ Job`: the plan is an immutable strategy; the job is its execution (retries, timestamps, events).
- `ArtifactRequest ≠ Artifact`: one request produces **0..N** artifacts (0: failure or nothing available; N: e.g. multi-language subtitles, keyframes).
- Every Artifact carries a user-facing `display_filename`, computed by the engine (ADR 0017): resolved as a `NamingPlan` at planning time (analyzed title, per-output qualifiers), bound at registration when the extension/language/cardinality are known. The physical job-store name stays technical; naming is metadata about the artifact, not a storage layout.
- `SourceDescriptor.type` (the supply mode: `url`, `file`, …) ≠ `ResourceAnalysis.resource_type` (the detected nature: `video`, `pdf`, …). A URL may point at anything.
- Every reference between objects goes through **explicit local identifiers** (`source.id`, `output.id`), never through an array index.
- The job references the **normalized** request (a snapshot): modifying a request after submission is impossible by construction.

### Internal execution vocabulary

- **Provider**: reaches a source or an external service (`ytdlp`, `http`, `file`, `ollama`…).
- **Processor**: transforms materials (`ffmpeg`, speech-to-text, summarizer…).
- **Operation** (logical transformation): an abstract, stable verb, independent of the tool (`media.acquire_audio`, `subtitles.to_transcript`, `text.summarize`…); the central registry is its source of truth (ADR 0012).
- **Implementation**: a concrete way of executing an operation (a given, versioned runner); derived from the installed runners, never declared by hand.
- **Material**: a step's intermediate input/output (`source`, `video`, `audio`, `subtitles`, `image`, `metadata`, `transcript`, `summary`, `chapters`, `text`, `pdf`, `image`). A material is not an artifact until it is promoted by an `output_binding`. `text` is the readable content of a non-media resource and is canonically **Markdown** — the structure is what makes a `markdown` artifact faithful, and plain text is a flattening of it, never a separate extraction. `pdf` is deliberately distinct from `text`: it is *presentation*, and reflowing a rendered page back into content is lossy, which is why rendering is its own `document.render_pdf` step rather than a `format` option repeated on every readable output. Note the deliberate split: `pdf.render` is the **public capability id**, `document.render_pdf` is the **logical transformation**, and each renderer (`content.pdf.typst`, `content.pdf.reportlab`) is an **implementation** of it.

## 2. Analysis and capabilities: three levels not to be confused

> Detailed end-to-end view: [architecture/analysis-to-capabilities.md](architecture/analysis-to-capabilities.md).

1. **Resource facts** (`SourceAnalysis`: `resource`, `streams`, `subtitles`, `media`): "the video has an audio track, en/de subtitles, and lasts 5420 s". **Independent of the installation**; that is all the analysis produces — never feasibility.
2. **Installation**: which operations have an active implementation (the `build_registry(providers)` registry), plus the instance policies.
3. **Feasibility**: the **Capability Resolver** crosses the two (plus the request-constraints overlay) and **computes** the capabilities. It is the only producer; the providers no longer emit any (ADR 0013).

Level 3 is exposed by `POST /capabilities` (not in the analysis), as a list of `ResolvedCapability` objects from the **public catalog** (`video.download`, `summary.generate`, …), with the statuses:

- `available` — a direct download (the material is the output);
- `derivable` — produced by transformation (`derived_from: [...]`);
- `unavailable` — impossible, with a **structured reason** (`missing_material` = incompatible source; `implementation_unavailable` = missing runner; `policy_restricted`);
- `restricted` — feasible but blocked by the effective policy;
- `unknown` — no proven impossibility, insufficient facts; never presented as `available`, an attempt is allowed, the uncertainty is traceable (R8).

The analysis is **cached with a TTL** and identified (`analysis_id`); the resolution, in contrast, is recomputed on demand (it depends on the installation, which changes when a daemon starts or stops). An analysis that is stale at execution time is a normalized error case (`analysis_stale`).

The `analysis_id` is **addressable** (ADR 0014): `GET /analyses/{id}` reads it back without ever re-running the analysis (`404 analysis_not_found`, `410 analysis_expired`), and `/capabilities`//`jobs` accept `sources` **XOR** `analysis_id`. The addressable record **references** the fact cache (by `resource_key`) instead of duplicating the heavy facts.

### Which provider analyses a source

Several providers may claim the same source; they are tried in an explicit
precedence order (`analysis_priority`), and **the first one that can characterise
the resource wins**. Routing is therefore an outcome of analysis, not a
pattern-match on the URL: every URL is offered to yt-dlp first, and only one it
does not recognise as media falls through to the web-page reader. A news article
that embeds a video stays a video, correctly, because yt-dlp found one.

The same rule settles local files: the document reader claims only document
suffixes and runs before ffmpeg, which claims every file — the narrower claim
wins, so a `.md` is read rather than probed. Ordering by provider name would be
a trap: `webpage` sorts before `ytdlp`.

## 3. Cardinality: scope and artifacts

`ArtifactRequest.scope` defines the logical scope:

- `single` (V1) — one logical output from the explicitly bound inputs;
- `each_source` — one instance per selected source;
- `each_item` — one instance per item of a composite resource (a playlist, the pages of a PDF);
- `all_sources` — one aggregated result (fan-in);
- `collection` / `group` — reserved (validated, not implemented).

The scope defines the number of **logical instances**; each instance may still produce 0..N artifacts (e.g. `subtitles` with `languages: ["fr","en"]` → one artifact **per language found**; see decision D7 in [contract.md](contract.md)). Total cardinality is therefore `scope × type`, and only the `artifacts` table is authoritative after the fact.

## 4. The Job state machine

Job statuses:

```text
created → validating → planning → queued → running → succeeded
                                                   → partially_succeeded
                                                   → failed
   (created|validating|planning|queued|running) → cancelled
   (validating|planning) → failed          (validation/planning impossible)
```

- Terminal states: `succeeded`, `partially_succeeded`, `failed`, `cancelled`. No transition leaves them (a `retry` creates a **new job** linked to the old one, it does not resurrect the same id).
- Every transition goes through a single function in the domain layer (`job.transition_to`), which rejects unlisted transitions. No free-form `UPDATE status` anywhere else.
- `cancelled` requires `cancel_requested`; a `queued` job is cancelled immediately, a `running` job is cancelled cooperatively (a flag read by the process runner).
- An orphaned `running` job (dead worker) is re-queued on startup (heartbeat/claim later on).

Step statuses:

```text
pending → ready → running → succeeded
                          → failed
pending|ready → skipped     (a failed dependency or a useless branch)
pending|ready|running → cancelled
```

### Step → job aggregation (tied to `required` and `failure_policy`)

At the end of execution:

- all `required` outputs produced → `succeeded` (even if optional outputs failed **and** at least one optional artifact is missing ⇒ `partially_succeeded`; if everything is produced ⇒ `succeeded`);
- at least one `required` output not produced, but at least one artifact produced → `failed` under `required_only` (success demands all required outputs);
- no artifact produced → `failed`;
- `fail_fast`: on the first blocking error → no new steps are executed, the remaining steps become `skipped`, aggregation is identical;
- `best_effort`: everything still executable is executed; the job ends `partially_succeeded` as soon as at least one artifact is produced, `failed` otherwise.

Documented cases:

| Case | Behaviour |
| --- | --- |
| A required output fails | The job is not `succeeded` (the policy decides whether to stop) |
| An optional output fails | Continue; at worst `partially_succeeded` |
| An optional output that a required output depends on | The dependency is **de facto required**: the planner marks the corresponding step as required ("requiredness" propagates to transitive dependencies) |
| An output impossible at planning time | A feasibility error **before** the job is created (`capability_unavailable`); never a job that fails immediately |
| An output skipped because a dependency failed | The step is `skipped`, a `step.skipped` event carries the cause; the output counts as not produced |
| An output partially produced | An incomplete artifact is never promoted; artifacts are created atomically (write-then-register) |
| An output → several artifacts, some of which fail | The instance counts as produced if ≥ 1 artifact was created; a `partial_output` warning is emitted with the details |

## 5. Events

`JobEvent` is an **append-only journal, sequenced per job** (a strictly increasing `sequence`), persisted, replayable, and independent of the raw logs (stdout/stderr go to `logs/`, never into the events).

Types: `job.created`, `job.validating`, `job.planned`, `job.queued`, `job.started`, `step.started`, `step.progress`, `step.succeeded`, `step.failed`, `step.skipped`, `step.warning`, `artifact.created`, `artifact.delivered`, `job.succeeded`, `job.partially_succeeded`, `job.failed`, `job.cancelled`.

`step.warning` carries `{step_id, code, message, details}` and means a step is **succeeding while doing less than was asked** — the engine had no way to say that before, so a runner in that position had to choose between failing and lying. The first case is an LLM whose context window silently dropped the tail of a long transcript (`partial_output`). The same warnings are attached to the artifacts that step produces, under `provenance.warnings`, because "what happened during this job" and "can I trust this file" are asked at different times and only one of them is answered by an event stream.

`artifact.delivered` carries `{artifact_id, artifact_request_id, path, renamed_from}`. `path` is relative to the delivery root; `renamed_from` is empty unless the name was already taken by *different* content, in which case it names what was wanted before the `-1` counter fired — a collision is a thing the user should be able to find out about, not discover in the folder weeks later.


`step.progress` is throttled at emission (a HomeTube legacy: 1 % for downloads, 5 % for processing) so it stays persistable.

## 6. Artifact provenance

Every artifact records: `artifact_request_id`, `job_id`, `source_ids`, `parent_artifact_ids`, `producer` (operation, provider, tool version), the normalized options applied, `media_type`, `size_bytes`, `checksum` (sha256), timestamps. This is the basis for auditing, caching (`reuse_existing`), debugging and invalidation.

## 7. Domain boundaries (what Content is not)

- The public contract knows **no tool** (yt-dlp, ffmpeg, Whisper, Ollama, OpenAI). Those names only appear in plans, provenance and provider preferences (`preferences.providers`, which reference **logical families**, not flags).
- The web interface is a client of the API; no business logic on the UI side.
- HomeTube's product features (SponsorBlock, cut, notifications, Jellyfin integrations) are not part of the V1 domain; they will come back, if needed, as typed output options or plan steps.
