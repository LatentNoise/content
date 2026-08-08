# Content's architecture

Follows from the [domain](domain.md) and the [contract](contract.md); the reuse choices come from the [HomeTube audit](hometube-reuse-audit.md).

## 1. Code structure

```text
apps/backend/                 # the engine — the only business logic (ADR 0011)
├── pyproject.toml
├── content/
│   ├── domain/        # pure business concepts: contract (Pydantic), plan, job, artifact,
│   │                  #   state machine, normalized errors — no I/O dependency
│   ├── application/   # use cases: submit_generation (validation → plan → job), analyses
│   ├── analysis/      # resource analysis (provider orchestration + TTL cache)
│   ├── planning/      # feasibility validation + building the ExecutionPlan
│   ├── execution/     # running the plan: JobExecutor, process runner, worker queue
│   ├── providers/     # access to resources/services (ytdlp, ffmpeg, webpage,
│   │                  #   documents, ollama, cloud LLMs, whisper)
│   ├── processors/    # transformations (transcript, summarize, translate, chapters, pdf)
│   ├── persistence/   # SQLite: store for jobs/steps/events/artifacts (source of truth)
│   ├── storage/       # per-job file layout (sources/ work/ artifacts/ logs/ snapshots/)
│   ├── events/        # publication of JobEvents (persisted; SSE later)
│   ├── api/           # FastAPI: v1 routes, response models, HTTP error mapping
│   ├── documents/     # renderer-neutral document model + Markdown parser
│   ├── capabilities/  # the public catalog and the resolver (ADR 0013)
│   └── config.py      # settings (env), allowed roots, data paths
└── tests/
```

The other members of the monorepo — `apps/web-hometube`, `apps/web-studio`,
`apps/web-admin`, `apps/cli`, `apps/mcp` and `packages/python-sdk` — are clients
of this engine through the public API and never import its code
(ADR 0011, ADR 0015).

Rationale for the boundaries: `domain` is substitutable/testable without I/O; `providers` and `processors` are external dependencies with their own lifecycle; `planning` and `execution` are the two halves HomeTube merged (the central architectural decision); `persistence`/`storage` isolate SQLite and the filesystem so that S3/remote workers become possible later without touching the domain.

## 2. The nominal pipeline

```text
POST /api/v1/jobs
  → structural validation (domain, Pydantic + D3 rules)         [422 on error]
  → source analysis (analysis, per-resource TTL cache)          [422 analysis_failed]
  → feasibility validation (planning)                            [422 + codes]
  → ExecutionPlan (planning, deterministic, shared, snapshot)
  → Job created (persistence) + snapshots (raw/normalized request, analysis, plan)
  → queued → worker claim → JobExecutor runs the steps (topological order)
  → artifacts registered (checksum, provenance) + append-only events
  → aggregated final status (failure_policy × required)
```

V1 runs the DAG **sequentially in topological order**; the model (steps + depends_on) does not depend on that — parallelism is an optimization of the executor, not a change of model. Planning is deterministic: given identical request, analysis and environment, the same plan (step ids derive from the output ids, no randomness).

Sharing is a duty of the planner: asking for `audio` + `metadata` + `thumbnail` from the same source produces **one** analysis and distinct but never duplicated acquisitions (one step per material, multiple `output_bindings` if a material serves several outputs).

## 3. API and Worker

A single repository, a single image, **two logical processes**:

- **API** (uvicorn): validates, plans, persists, exposes, publishes events, serves the artifacts. Never launches a long external process.
- **Worker**: a claim → execute → outcome loop. V1: an asyncio pool in the same process as the API (the HomeTube `JobQueue` pattern, bounded by `CONTENT_MAX_CONCURRENT_JOBS`), also launchable separately. No Redis/Celery/Kafka/Temporal — the internal `JobQueue` abstraction (enqueue / claim_next / complete / fail / release, heartbeat later) is satisfied by SQLite (atomic `BEGIN IMMEDIATE` claim, WAL, recovery of `running` jobs on startup — a pattern taken from HomeTube).

## 4. Persistence (SQLite, the source of truth)

v1 tables:

| Table | Content | Choice |
| --- | --- | --- |
| `jobs` | id, status, normalized request (versioned JSON), plan_id, failure_policy, idempotency_key (unique, nullable), timestamps, error | status/timestamps as columns (queried); request as JSON (immutable) |
| `job_steps` | id, job_id, plan_step_id, status, operation, provider, timestamps, error | the execution reflection of the plan |
| `job_events` | id, job_id, **sequence** (unique per job), type, timestamp, data JSON | append-only, never UPDATEd |
| `artifacts` | id, job_id, artifact_request_id, type, status, relative path, media_type, size, checksum, provenance JSON | promoted atomically (write-then-register) |
| `analyses` | id, resource key, normalized JSON payload, created_at | TTL cache |

No table per domain class: `ExecutionPlan` and `ResourceAnalysis` live as versioned JSON snapshots (immutable, not queried); only the frequently queried facts (statuses, sequences, checksums) are indexed columns.

Schema evolution: sequential migrations tracked by `PRAGMA user_version` (applied **before** the base schema at open time — its indexes may reference migrated columns); a fresh database starts directly at the latest version.

`reuse_existing`: every step carries a **content-addressed signature** (operation + provider + `resource_key` + params + the dependencies' signatures), recorded on its artifacts. At execution time, a bound step whose signature matches an existing artifact group (checksum-verified) is satisfied by **copy** — with `reused_from_artifact_id` provenance + the original producer. The same signature serves both intra-plan sharing and the cross-job cache.

## 5. Storage (per-job filesystem)

```text
$CONTENT_DATA_DIR/jobs/<job_id>/
├── sources/     # materialized input materials
├── work/        # intermediate files (purged at the end of the job, retention.working_files)
├── artifacts/   # final results, names generated by the backend
├── logs/        # stdout/stderr per step (never in the events)
└── snapshots/   # request.json, request_normalized.json, analysis.json, plan.json, result.json
```

The DB remains the truth; a file missing from disk with a valid `artifacts` row is a detectable corruption (checksum). The `storage` interface is the only module that manipulates these paths — a future S3/MinIO boundary.

## 6. Running external processes

A **single process runner** (`execution/process.py`), inherited from HomeTube's `run_cmd` with the identified fixes: argument lists (never `shell=True`), a mandatory timeout, cooperative cancellation (terminate → kill after 5 s), stdout/stderr captured separately into `logs/`, progress parsed → throttled `step.progress`, classified errors (auth, expired cookies, unavailable format, bot detection) → normalized codes, guaranteed cleanup of temporary files (finally), and it never blocks the API (executed on the worker side only).

## 7. Providers and processors

Internal interfaces (not frozen until plan serialization, crash recovery and injection have been proven by ≥ 2 implementations; no public plugins in V1):

- `SourceProvider`: `supports(source)`, `analyze(source, ctx) -> ResourceAnalysis`, plus the acquisition operations it knows how to plan/execute.
- `ArtifactProcessor`: `supports(request, inputs)`, `plan(...) -> [PlanStep]`, `execute(step, ctx) -> [ProducedArtifact]`.

V1: `YtDlpProvider` (`url` sources: `-J` analysis + acquisitions; cookies through `credential_id`; SponsorBlock; **ordered codec profiles av1→vp9→h264 with a multi-client default/ios/web fallback** on YouTube, adapted from `hometube/engine/profiles.py` — internal to the provider, never a `format_id` in the contract) and `FfmpegProvider` (`file` sources: ffprobe analysis + stream-copy extractions) carry out the **same abstract operations** (`media.acquire_*`, ADR 0005) — the planner picks the provider per source. The first real processor: `TranscriptProcessor` (`content.transcript`, pure Python) executes `subtitles.to_transcript` by consuming the **materials** produced by its dependency (`ExecutionContext.input_materials`). The registry unifies providers and processors under the `StepRunner` interface (a stable name + `execute`) — that is what makes the plan serializable and execution resumable. The planner **shares** identical steps (dedup by operation+provider+source+params; one step may carry several bindings) and **propagates requiredness** to the transitive dependencies.

## 8. Security (V1)

- No `shell=True`; arguments built by the backend, never interpolated from the contract (no equivalent of HomeTube's `custom_args` in the public contract — that was an implementation leak and an injection vector).
- Filenames generated by the backend (`sanitize_filename`, taken from HomeTube), never supplied by the client.
- `file` sources: an allowlist of roots (`CONTENT_ALLOWED_INPUT_ROOTS`), path normalization, refused by default.
- SSRF: `http(s)` schemes only in V1; blocking private/link-local IPs is configurable (`CONTENT_ALLOW_PRIVATE_NETWORKS=false` by default) — yt-dlp makes the requests, validation happens on the submitted URL, a documented limit (redirects are not re-validated in V1).
- Configurable limits: per-step timeout, `max_runtime_seconds` per job, maximum artifact size; exceeding one = a normalized `failed` step, never a silently saturated disk.
- Secrets: only references (`credential_id`) in the contract; never in logs, events, snapshots or responses.
- Per-job isolation: every write goes through `storage` under `jobs/<job_id>/`.

## 9. V1 boundaries and what comes next

The engine executes `url`, `file` and `text` sources; the fifteen capabilities in the catalog — `video.download`, `video.clip`, `audio.download`, `subtitles.download`, `thumbnail.download`, `thumbnail.generate`, `keyframes.extract`, `metadata.export`, `transcript.generate`, `summary.generate`, `translation.generate`, `chapters.generate`, `text.extract`, `markdown.export`, `pdf.render` — across the `video`, `audio`, `subtitles`, `thumbnail`, `keyframes`, `metadata`, `transcript`, `summary`, `translation`, `chapters`, `document_text`, `markdown` and `pdf` output types; `single` scope, `async` mode, the three failure policies, persisted events (polling and SSE), artifacts served by the API. The catalog in `apps/backend/content/capabilities/catalog.py` is the only authoritative list (ADR 0013 R6); this paragraph is a summary of it, not a second source.

The next slices, in order of value: the `video` output (adapting `engine/profiles.py` — HomeTube's most valuable know-how), `reuse_existing` (a per-resource-identity cache, the `status.json` pattern → the artifacts table), SSE on `/jobs/{id}/events`, `each_item` over playlists (adapting `engine/playlist.py`), `transcript` (the first non-yt-dlp processor), `file`/`upload` sources, the web UI (a client of the v1 API, reusing HomeTube's `ui/` patterns).
