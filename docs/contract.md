# Content's public contract — v1

The public contract is the most stable foundation of the project: the internals (DB, queue, providers, processors) may change, the contract may not. It is declarative, versioned, strongly validated, and independent of providers and infrastructure.

Status: **v1 draft**. This document is the reference; the Pydantic models in `apps/backend/content/domain/` are its executable implementation (machine source of truth: the OpenAPI at `/api/v1/openapi.json`).

## 1. Root schema of the GenerationRequest

```json
{
  "schema_version": "1.0",
  "sources": [],
  "outputs": [],
  "preferences": {},
  "constraints": {},
  "execution": {},
  "metadata": {}
}
```

- `schema_version` (required): the contract version, `major.minor`. An unknown major version is rejected (`unsupported_schema_version`); a higher minor version known to the server is accepted.
- `sources` (required, 1..N): always an array, even for a single source. There is **no** `"source": {}` shape — SDKs/CLIs may offer shorthands but normalize to the canonical shape (decision D1).
- `outputs` (required, 1..N): the `ArtifactRequest` objects.
- `metadata`: free-form client annotations (tags, correlation), **never interpreted** by the engine, bounded in size.

## 2. Sources — `SourceDescriptor`

A union discriminated by `type` (the supply mode, not the nature of the content):

| `type` | V1 | Own fields |
| --- | --- | --- |
| `url` | ✅ executed | `uri` |
| `file` | ✅ executed (allowed roots required) | `path` |
| `upload` | ⏳ validated, refused at feasibility (`source_type_not_supported`) | `upload_id` |
| `text` | ✅ executed for the text outputs (`markdown`, `document_text`, `summary`, `translation`); the media outputs and `metadata` refuse with `capability_unavailable` | `content`, `mime_type` |
| `collection` | 🔒 reserved | `item_refs` (see D2) |
| `connector` | 🔒 reserved | `connector_id`, `resource_reference` |

Common fields: `id` (required, unique within the request, `[a-z0-9_-]{1,64}`), `role` (optional: `primary`, `context`, `reference`, `instruction`, `attachment`, `alternative` — a semantic hint, never a replacement for explicit dependencies), `hints` (optional, not guaranteed: `resource_type`, `language`… — may be wrong, ignored or corrected), `auth` (optional: `credential_id` **or** `session_id` — never a raw secret).

```json
{ "id": "main", "type": "url", "role": "primary", "uri": "https://example.com/video" }
```

**`file` security**: a remote API does not read the filesystem arbitrarily. `source.type=file` is only accepted if the server is configured with allowed roots (`CONTENT_ALLOWED_INPUT_ROOTS`); the path is normalized (resolving `..` and symlinks) then checked against those roots. Outside a root → `path_not_allowed`. Without configuration, the type is refused (`source_type_not_supported`).

**`provider_args` (`url` source, advanced escape hatch)**: a list of raw arguments passed to the acquisition provider (yt-dlp), e.g. `["--proxy", "http://host:8080", "--limit-rate", "2M"]`. An **explicit, documented exception** to the "no provider leakage into the contract" principle, reserved for advanced users. Security guard rail: command-execution flags and output/config/cookie redirection flags (`--exec`, `-o`/`--output`/`--paths`, `--config-location`, `--cookies`, `--load-info-json`, `--batch-file`, `--cache-dir`…) are **rejected** (`ValidationError`). An operator-side default also exists through `CONTENT_YTDLP_EXTRA_ARGS` (trusted, unfiltered), applied to every yt-dlp invocation.

**Authentication (`auth`)**: the secret **never** travels through the request (INV-009). `auth.credential_id` names a cookie set **configured on the server** (`CONTENT_CREDENTIALS="id=path,…"`); the provider passes `--cookies <file>` to both analysis and acquisition. An unknown `credential_id` → `credential_not_available`. `auth.session_id` is reserved but **not implemented** → `auth_method_not_supported` (honour it or reject it). The configured ids are listed by `GET /api/v1/config` (ids only, never the paths). Authentication varies the analysis cache key (an authenticated probe may reveal different material).

## 3. Outputs — `ArtifactRequest`

A union discriminated by `type` (a logical result, never a tool):

```json
{
  "id": "audio_main",
  "type": "audio",
  "scope": "single",
  "from_sources": ["main"],
  "from_outputs": [],
  "required": true,
  "options": {},
  "delivery": {},
  "metadata": {}
}
```

### V1 types (executed)

| `type` | Typed options (all optional) | Cardinality per instance |
| --- | --- | --- |
| `video` | `selection` (`max_height` a strict ceiling; `video_codec`/`audio_codec` in `prefer`\|`require` mode, D4; `audio_languages`: an **ordered** list of audio languages to include as tracks — >1 = embedded multi-audio, intersected with what the source offers, unavailable ones dropped with a `partial_output` warning), `container` (`source`\|`mkv`\|`mp4`), `processing` (`mode`: `auto`\|`copy`\|`remux`\|`transcode` — `transcode` valid but not implemented; `embed_metadata`, `embed_thumbnail`, `embed_chapters`, `embed_subtitles`: a list of languages embedded into the container, same rules; embedding ignored on file sources), `sponsorblock` (`remove`/`mark`: category lists; `cut_mode`: `precise`\|`keyframes`, default `precise`. `precise` is yt-dlp's `--force-keyframes-at-cuts`: a keyframe is forced at each removed segment, which costs a re-encode and is what makes the stream play to the end. `keyframes` is `--no-force-keyframes-at-cuts`: a stream copy, faster, but the discarded frames come back with backwards timestamps and the tail stutters. Note the inversion — the flag that *forces* keyframes is the `precise` value, because the cut lands exactly where asked rather than on the nearest existing keyframe) | 1 |
| `audio` | `format` (`source`\|`opus`\|`mp3`\|`m4a`, default `source` = the best native stream) — an explicit value means extraction/transcoding at acquisition time (URL/yt-dlp); on a **file** source it is not implemented → `option_not_supported`. `languages` (an ordered preference list: the first available one wins). `sponsorblock` (`remove`/`mark`/`cut_mode`, as for `video`) | 1 |
| `metadata` | `include_raw_provider_data` (bool, default `false` — the raw yt-dlp JSON is **not** the public model, see D5) | 1 |
| `thumbnail` | `strategy` (`best_available` only in V1), `format` (`source`\|`jpeg`), `max_width` (int), `source` (`auto`\|`download`\|`generate`), `at` (`HH:MM:SS` or seconds). Two paths: the image the source **published** (`download`) or a frame cut out of the **video** (`generate`). `auto` prefers the published image — it is the one the author chose. Naming an `at` implies generation; `at` with `source: download` is rejected, and `max_width` on the download path is rejected too (it cannot be scaled — see D-11) | 0..1 |
| `keyframes` | `every` (seconds) **xor** `count` (≤ 200), `format` (`jpg`\|`png`\|`webp`, bounded by what the installed ffmpeg can encode), `width` (int), `start`/`end` to bound the range. Input: a video source. Produces **one artifact per frame** (0..N, see D7), each named by the instant it shows and carrying it in provenance | 0..N |
| `subtitles` | `languages` (list, required, non-empty), `source` (`prefer_manual`\|`manual_only`\|`automatic_only`\|`any`), `format` (`srt`\|`vtt`) | 0..N (one artifact **per language found**, see D7) |
| `transcript` | `language` (`auto` = deterministic resolution from the analysis), `source` (`auto`\|`prefer_existing_subtitles`\|`existing_subtitles_only`\|`speech_to_text` — served by the **optional** `[stt]` Whisper runner; without it: `option_not_supported`), `timestamps` (`segment`\|`none`; `word` not implemented), `format` (canonical `json` D8\|derived `text`). Input: a source **or** a `subtitles`/`audio` output through `from_outputs` (audio ⇒ an STT runner is required) | 1 |
| `summary` | `language` (`auto` = the transcript's language), `length` (`short`\|`medium`\|`long`), `style` (`structured`\|`plain`\|`bullet_points`), `format` (`markdown`\|`text`). Input: a source (a synthesized transcript chain) **or** a `transcript` output. The runner is chosen through `preferences.providers.llm` under `constraints.privacy.allow_cloud_providers`; the model used appears in the provenance, never in the contract | 1 |
| `translation` | `target_language` (**required**), `source_language` (`auto` = detected). Input: a source (subtitles → translated subtitles, timings preserved) **or** a `subtitles`/`transcript` output through `from_outputs` (transcript → translated text). Executed by the LLM runners (local Ollama / cloud, excluded by `privacy.allow_cloud_providers:false`) | 1 |
| `chapters` | `format` (canonical `json`\|`ffmetadata`, ingestible by ffmpeg). Two explicit variants: `chapters.from_source` (facts declared by the source, deterministic), otherwise `chapters.from_transcript` (derived by an LLM, output **strictly validated**: increasing bounds ≤ duration). Input: a source **or** a `transcript` output through `from_outputs` | 1 |
| `document_text` | `format` (`text` flattens the reading\|`markdown` keeps its structure). Input: a readable source (a web page, a `.txt`/`.md` file, an inline `text` source) | 1 |
| `markdown` | *(none yet)* — Markdown is the extractor's canonical form. Input: a readable source | 1 |
| `pdf` | `page_size` (`a4`\|`letter`), `title` (empty = taken from the rendered material). Input: a readable source **or**, through `from_outputs`, any readable output (`summary`, `transcript`, `translation`, `chapters`, `markdown`, `document_text`). Rendered by an operator-selected implementation (Typst or ReportLab) — the renderer, its template and its fonts are **operator configuration, never contract**; the chosen backend appears in the artifact's provenance. See [operations/pdf-rendering.md](operations/pdf-rendering.md) | 1 |

### Types declared but not executed in V1

`ocr`, `embeddings`, `semantic_index`, `archive`, `collection` — recognized by the schema (hence reserved, a client cannot redefine them), rejected at **feasibility** time with `output_type_not_supported` (valid ≠ implemented, see §6).

### Input resolution rules (deterministic, D3)

1. `from_sources` and `from_outputs` list existing ids; otherwise `unknown_source_reference` / `unknown_output_reference`.
2. The `from_outputs` graph must be acyclic (`dependency_cycle`).
3. If both are empty: the output consumes **the single source** of the request if `sources` holds only one; otherwise the `role: primary` source if it is unique; otherwise the error `ambiguous_inputs` ("precise from_sources"). No other inference.
4. An output type imposes input requirements (e.g. `audio` requires exactly one audio-material input per instance in `single` scope: `too_many_inputs` otherwise).
5. A dependency that is declared but technically useless is not an error: it constrains ordering (the step waits) and the provenance. The planner never silently "corrects" the client's declarations.

### `scope`

The full enum is validated from V1 on: `single`, `each_source`, `each_item`, `all_sources`, `collection`, `group`. Any scope that fans one instruction out over several items is bound by [INV-018](architecture/invariants.md): the orchestration chooses the items and their order, and delegates each one to the canonical single-item pipeline — it never re-implements planning for the multi-item case, and never invents facts a listing does not carry (ADR 0019). Only `single` and `each_item` are executable in V1; the others produce:

```json
{ "code": "scope_not_supported", "path": "outputs[0].scope",
  "message": "The scope 'each_source' is valid but not supported by the current execution engine." }
```

### `required` (default `true`)

Indicates whether the absence of this output forbids overall success — the precise effect depends on `execution.failure_policy` (see [domain.md](domain.md) §4, the table of cases). "Requiredness" propagates to the transitive dependencies of a required output.

### `delivery`

Where and under which name to deposit the artifact *in addition*. Artifacts always live in the job's artifacts directory (the source of truth) and are downloadable through the API. A delivered copy lands in the server's delivery root (`CONTENT_DELIVERY_DIR`, default `<data>/delivery`) under `<folder>/<display_filename>` — the equivalent of dropping it into a mounted media library — and its path, relative to that root, is recorded on the artifact (`delivered_path`) so a client can say where the file is.

```json
"delivery": { "mode": "inherit", "folder": "talks/2026", "filename": "keynote" }
```

- `mode` (ADR 0018) makes the delivery decision explicit instead of encoding it in field presence:
  - `inherit` (default, = the field absent): the server's delivery policy decides. With `CONTENT_DELIVERY_DEFAULT` **on** (the packaged deployment), every artifact is delivered; with the policy **off**, the historical rule applies — deliver only when `folder` or `filename` carries intent.
  - `deliver`: always deliver, whatever the policy.
  - `none`: never deliver; combining it with `folder`/`filename` is contradictory intent, rejected (`schema_violation`).
- `folder`: a **relative** path (`/`), rejected on traversal (`.`/`..`), if absolute (normalized to relative) or containing a backslash. Every segment is re-sanitized by the backend before any disk access.
- `filename`: a base name **without an extension** — and precisely that: the **base name of the artifact family**, not necessarily the literal final filename. The sanitized base replaces the engine's resolved base (`display_filename`, ADR 0017), but qualifiers, language suffixes and cardinality numbering still apply, so `"filename": "My Talk"` yields `My Talk.mp4` for the primary artifact and `My Talk - subtitles - en.srt` for a sidecar. Only a single-artifact output gets the literal `<filename>.<ext>`. *(A future major contract version may rename this field `base_name`, which is the more precise word; the field is not renamed today.)* Path separators are **sanitized, not rejected** (`/` and `\` become `" - "` — an ordinary video title contains slashes; D-51): the client sends *intent*, the server owns the cleanup. A name that sanitizes to nothing is treated as absent; in its absence the engine names artifacts itself from the analyzed resource — a delivered file never needs a client-invented name.
- `{}` stays valid and now means "**no client intent — the server's policy decides**". Collisions in the library resolve with a deterministic `-1`, `-2`… suffix, reflected in `delivered_path`. `GET /api/v1/config` exposes whether the policy is on (`delivery.by_default`).
- The client expresses an *intent*; the backend keeps control over path safety. External destinations (S3…), templates and retention remain reserved for later.

## 4. `preferences` (wishes, fallback allowed) and `constraints` (imperatives)

```json
{
  "preferences": {
    "optimize_for": "balanced",
    "providers": { "media": ["ytdlp"], "transcription": ["faster-whisper", "openai"] }
  },
  "constraints": {
    "privacy": { "allow_cloud_providers": true },
    "resources": { "max_runtime_seconds": 7200, "max_output_bytes": null },
    "content": { "allowed_languages": [] }
  }
}
```

- A preference the engine *can* weigh but does not honour produces an explanatory **warning** (`preferred_provider_unavailable`…), never a failure.
- A constraint that cannot be satisfied produces a feasibility **error**; a constraint that cannot be decided before execution produces a warning plus a check at execution time (e.g. `max_output_bytes` checked after the step, the artifact refused and the step marked `failed` if exceeded).
- `optimize_for`: `speed` | `quality` | `cost` | `storage` | `balanced` (default). **Reserved** — no planner rule consults it yet, so the plan is identical whichever value is sent. Setting it produces a warning; the artifact you asked for is still produced.
- **`preferences.language` and `preferences.execution_location` are refused**, not ignored. Neither is read, and quietly accepting them would misrepresent what the engine did — `execution_location` in particular reads as a guarantee about where data goes. Ask per output for a language (`subtitles.languages`, `audio.languages`, a `translation` target); use `constraints.privacy.allow_cloud_providers: false`, which **is** enforced, to keep work off cloud runners. Same for `constraints.network.allow_remote_processing`.
- `preferences.providers` references **logical families** (`media`, `transcription`, `llm`…) with documented provider names; it is the only place in the contract where a provider name may appear, by construction as a preference.

## 5. `execution`

```json
{
  "execution": {
    "mode": "async",
    "failure_policy": "required_only",
    "priority": "normal",
    "reuse_existing": true,
    "idempotency_key": null,
    "retention": { "outputs": "30d", "working_files": "24h", "logs": "7d" }
  }
}
```

- `mode`: `async` (V1) | `sync` (**reserved and refused**: a future sync endpoint would wait on small jobs without changing the architecture, but until it exists, accepting `sync` and queueing anyway would be a lie. Poll `GET /api/v1/jobs/{job_id}` or read its events).
- `priority`: **reserved and refused** for any value but `normal` — the queue is strictly first-in, first-out.
- `failure_policy`: `fail_fast` | `required_only` (default) | `best_effort` — semantics in [domain.md](domain.md) §4.
- `idempotency_key` (D6): the scope is (key, canonical normalized body). Replaying the same key with the **same** body returns the existing job (`201` initially, `200` afterwards, same `job_id`) as long as the job exists and has not ended in failure; after a `failed`/`cancelled` job, a re-submission creates a new job (the key is released). Same key + different body → `409 idempotency_conflict`. Validity: the job's retention period. Distinct from `reuse_existing` (a result cache) and from internal deduplication.
- `reuse_existing` (default `true`): allows the engine to reuse the product of an **identical** piece of work already done — a *content-addressed* identity (operation + provider + resource identity + options + dependency chain, independent of the ids the client chose), checksum-verified at execution time; the provenance carries `reused_from_artifact_id` and the original producer. Distinct from idempotence (D6): idempotence deduplicates the *submission*, `reuse_existing` deduplicates the *work*.
- `retention`: `outputs`, `working_files` and `logs` are designed with **independent** clocks. **Reserved and refused in V1 for any value but the defaults**: nothing expires, no clock runs, and the only cleanup that happens is the working files removed at the end of a job — which is unconditional, not driven by this block. The defaults shown above are inert placeholders, not a promise that outputs disappear after 30 days; manage the data directory yourself ([storage.md](storage.md)).

## 6. Two-phase validation

**Phase 1 — structural** (no network, inside `POST /api/v1/jobs` and `POST /api/v1/plans`): fields, formats, enums, version, id uniqueness, references, cycles, input rules (D3), options of the right subtype.

**Phase 2 — feasibility** (may require the analysis): supported output type, executable scope, resource capabilities, available providers/processors, satisfiable constraints, buildable plan.

A single error format (HTTP 422 for structure, 422 with `phase: "feasibility"` for feasibility):

```json
{
  "valid": false,
  "errors": [ { "code": "unknown_source_reference", "path": "outputs[0].from_sources[0]",
                "message": "Source 'missing' does not exist.", "details": {"source_id": "missing"} } ],
  "warnings": [ { "code": "transcode_required", "path": "outputs[0]", "message": "...", "details": {} } ]
}
```

`code` is stable and machine-readable; `message` is human-facing and may change or be translated; `path` points at the offending element using a simplified JSONPath syntax.

v1 codes: `unsupported_schema_version`, `duplicate_id`, `unknown_source_reference`, `unknown_output_reference`, `dependency_cycle`, `ambiguous_inputs`, `too_many_inputs`, `invalid_option`, `source_type_not_supported`, `output_type_not_supported`, `option_not_supported` (a valid but unimplemented option), `scope_not_supported`, `path_not_allowed`, `url_not_allowed` (a non-http(s) scheme or a blocked private host), `capability_unavailable`, `constraint_unsatisfiable`, `credential_not_available` (auth: credential_id not configured), `auth_method_not_supported` (auth: session_id not implemented), `analysis_failed`, `analysis_stale`, `idempotency_conflict`; warnings: `preferred_provider_unavailable`, `preference_unavailable` (a D4 preference unavailable, fallback applied), `capability_unknown` (feasibility undecidable before execution — the step is attempted), `transcode_required`, `partial_output`, `constraint_check_deferred`.

## 7. Decisions taken (and their reasons)

- **D1 — `sources[]` always plural.** A single canonical contract; no competing singular `source` shape. Helpers normalize.
- **D2 — collections by reference, not by recursion.** `type: collection` (reserved) will carry `item_refs` pointing at sources **declared flat** in `sources[]`, rather than nested descriptors: uniform ids at a single level, no nested collections in V1, sources reusable by several collections, bounded request size, no cycles by construction. *Detected composite* resources (a playlist, a multi-page PDF) are not `collection`s: they are simple sources whose analysis reveals `items` (addressed by the provider's stable `item_id`, with assumptions documented when `each_item` is implemented).
- **D3 — deterministic input resolution** (§3): a single inference rule (a unique source or a unique primary), otherwise an explicit error. No magic.
- **D4 — limit semantics named in the option.** Future video options will distinguish `mode: prefer|require` per field (e.g. codec) rather than ambiguous booleans (`remux: true`) or vague names (`best`, `fast`). `max_*` is always a strict ceiling; a preferred target is called `target_*`.
- **D5 — normalized public metadata.** The public model (`title`, `description`, `author`, `channel`, `published_at`, `duration_seconds`, `languages`, dimensions, codecs, `size_bytes`, `thumbnail_url`, `canonical_url`, `provider_id`, collection info) is stable; the provider's raw JSON is only available through `include_raw_provider_data` and lives in the debug snapshots.
- **D6 — idempotence ≠ cache** (§5).
- **D7 — subtitle cardinality.** An `ArtifactRequest` of type `subtitles` produces **one artifact per language actually found** (0..N), each labelled (`language`, `origin: manual|automatic`, `translated_from` reserved). No multi-track file, no implicit collection; `scope` does not express this cardinality (it is inherent to the type). If none of the requested languages exists: 0 artifacts + a `partial_output` warning (a failure if `required` and the policy demands it).
- **D8 — the (future) transcript will be a structured artifact** (canonical JSON with segments/timestamps) from which SRT/VTT/text are derived — never the other way round.
- **D9 — versioning.** A major `schema_version` = breaking changes; a minor one = backward-compatible additions (new types, new optional options). The contract's enums never have a value removed in a minor version. Responses also carry the version of the schema that produced them.
- **D10 — no executable code in the contract.** Templates (`delivery`, future markdown templates) = references to templates known to the server, never arbitrary expressions.

## 8. REST API v1

Prefix `/api/v1`. Initial slice:

| Method | Path | Role |
| --- | --- | --- |
| `GET` | `/config` | Client configuration: the ids of available credentials (never the paths/secrets) |
| `POST` | `/analyses` | Analyze sources → `ResourceAnalysis` (TTL cache); the `analysis_id` returned is **addressable** (ADR 0014) |
| `GET` | `/analyses/{id}` | Fetch an analysis by id — a **safe** read, it **never** re-runs the analysis; `404 analysis_not_found`, `410 analysis_expired` |
| `POST` | `/jobs` | Submit a `GenerationRequest` (validates, plans, enqueues) |
| `GET` | `/jobs` / `/jobs/{id}` | List / inspect (status, steps, warnings) |
| `POST` | `/jobs/{id}/cancel` | Cooperative cancellation |
| `GET` | `/jobs/{id}/events` | Ordered events (`?after_sequence=` to resume) |
| `GET` | `/jobs/{id}/events/stream` | SSE over the same journal (resume through `Last-Event-ID`, heartbeat, an explicit `stream.end` at the end of the job) |
| `POST` | `/jobs/{id}/retry` | A **new** job replaying the same normalized request (`retry_of` links it to the original; never a resurrection; the idempotency key is not carried over) — 409 if the job is not terminal |
| `GET` | `/jobs/{id}/artifacts` | The artifacts produced + provenance |
| `GET` | `/artifacts/{id}` / `/artifacts/{id}/content` | Metadata / binary content |

Artifact views carry both names (ADR 0017): `filename` is the technical name
inside the job store (an implementation detail, kept stable for addressing);
`display_filename` is the user-facing name the engine computed from the
analyzed resource ("`My Conference - subtitles - en.srt`"), also served as the
download filename by `/artifacts/{id}/content`. `delivered_path` (ADR 0018) is
where the delivered copy landed, relative to the delivery root — `""` when no
copy was made. Artifacts registered before the naming engine have an empty
`display_filename` and download under their technical name.

**Inputs: `sources` XOR `analysis_id` (ADR 0014).** `POST /capabilities` and `POST /jobs` accept **exactly one** of the two: either inline `sources` (stateless, direct usage), or an addressable `analysis_id` (resuming a workflow from any client). The exclusivity is declared in the public models (`oneOf` in the OpenAPI) and rejected with stable codes: `sources_or_analysis_id_required` (neither), `sources_and_analysis_id_conflict` (both). The `analysis_id` mode resolves to the memorized `sources` then follows the unchanged pipeline.

Reserved (declared, not implemented): `POST /plans` (a planning dry-run), `GET /plans/{id}`. A simple client posts a `GenerationRequest` directly; an advanced client analyzes then submits (`analysis_id`) — both converge on the same internal pipeline. The backend has no UI of its own (`/` redirects to `/docs`); the official UIs are separate applications (HomeTube, Studio, Console).

## 9. What "stable" means for v1

Publishing makes this document someone else's dependency. So it has to say which
parts a client may build on and which parts may move under it.

### Will not change without a new major path (`/api/v2`)

- **Endpoint paths, methods and status codes** listed in §8.
- **Error codes.** They are machine identifiers (`output_type_not_supported`,
  `idempotency_conflict`, …). The human `message` attached to one may be
  reworded at any time; the code may not. New codes may be *added* — a client
  must therefore treat an unknown code as "some error", never crash on it.
- **The shape of an error body**: `{"detail": {"valid": false, "phase": …,
  "errors": [{"code", "path", "message", "details"}]}}`. There is exactly one
  422 shape, including for malformed JSON bodies (`schema_violation`).
- **Field names and meanings** in `GenerationRequest`, and the meaning of the
  job states in [domain.md](domain.md).
- **The four core concepts** and their separation (request, plan, job,
  artifact). A v2 would be a different decomposition, not a renamed field.

### May change in a minor release

- **New optional fields, new output types, new capabilities, new warnings.**
  Additive only: a request valid today stays valid. A client must ignore
  response fields it does not recognise.
- **Anything documented as *reserved*.** A reserved field is refused today
  (`option_not_supported`) and may start being honoured tomorrow — which can
  only turn a rejection into a success, never the reverse. This is deliberate:
  refusing first is what makes implementing later a non-breaking change.
  Reserved surface is declared in one place,
  `apps/backend/content/domain/reserved.py`, and a test fails if a public field
  is added that is neither read nor declared there.
- **`resource_key`** (D-12). Published because operators need it to reason about
  reuse, but its shape (`ytdlp:url:<sha256>`) is an internal cache key that
  depends on the provider *and its version*. **Opaque**: compare it for
  equality, never parse it, never store it as an identifier.
- **Plan internals**: step ids, the number of steps for a given request, which
  provider was chosen. Provenance records what *did* happen; it does not promise
  the same route next time.
- **Which capabilities resolve on a given installation.** That depends on the
  tools installed, and `/capabilities` is the only correct way to ask.

### What a v2 would be

Not a rename and not a new field: those fit above. `/api/v2` would mean the
decomposition itself changed — a request that can no longer be expressed as
"sources → outputs", or a different identity model for reuse. Nothing on the
roadmap requires one, and this section exists so that stays true by choice
rather than by luck.
