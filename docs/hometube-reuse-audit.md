# HomeTube → Content reuse audit

Date: 2026-07-17.
Scope audited: the full copy of HomeTube (`hometube/`, not versioned in this repository) — the `app/engine`, `app/api`, `app/ui` backend, the shared modules, Docker, tests, CI.

## 1. Overview of what exists

HomeTube 3.x is already split into three clean layers:

| Layer | Content | State |
| --- | --- | --- |
| `app/engine/` | A **pure** download engine (dataclasses, zero Streamlit/FastAPI): `spec.py` (JobSpec v2), `context.py` (callbacks), `runner.py` (subprocess), `progress.py` (yt-dlp parsing), `probe.py` (`yt-dlp -J` analysis + TTL cache), `profiles.py` (codec/client fallback), `jobs.py` (end-to-end execution), `content.py` (argument builders), `cache.py`, `playlist.py`, `finalize.py` | Clean, tested, import-clean (guarded by a test) |
| `app/api/` | FastAPI: `db.py` (a SQLite WAL JobStore = queue + history), `queue.py` (a pool of asyncio workers), `runner.py` (default_runner), `models.py` (a Pydantic mirror of the JobSpec), `main.py` (an injectable `create_app()`) | Clean, compact (~900 lines), tested |
| `app/ui/` | A light Streamlit client (probe → submit → monitor over HTTP) | Clean but coupled to the HomeTube API |
| Shared modules | `medias_utils` (1011 l.), `subtitles_utils` (1170 l.), `quality_profiles` (938 l.), `url_utils` (600 l.), `workspace.py`, `file_system_utils`, `process_utils`, `logs_utils`, `status_utils`, `config.py` | Variable quality; valuable business logic mixed with emoji logging and implicit file conventions |
| Legacy | `app/main.py` (4407 lines, a Streamlit monolith) | To be ignored — replaced by `ui/` + `api/` |

The central finding: **HomeTube already holds an embryo of every Content concept, but merged together**. The `JobSpec` mixes intent and execution (`advanced.custom_args`, `ytdlp_client`, `format_id`), the `default_runner` does both planning (probe → profiles) and execution, and the SQLite job has neither steps, nor events, nor multiple artifacts (a single `output_path` column). There is no equivalent of an `ExecutionPlan`, an `Artifact` or a `JobEvent`: progress is mutable state overwritten on every write (no history), and partial success does not exist.

## 2. Decision table

| Component | Decision | Current quality | Couplings | Rationale | Action |
| --- | --- | --- | --- | --- | --- |
| `engine/probe.py` (URL analysis, `url_info.json` TTL cache) | **Adapt** | Solid, with a pure testable part (`resolve_profiles`) | `medias_utils`, `url_utils`, `workspace`, emoji logs | This is exactly Content's `ResourceAnalysis` role | Extract behind `SourceProvider.analyze()`; normalize the output (public metadata ≠ raw yt-dlp JSON) |
| `engine/runner.py` (`run_cmd`) | **Adapt** | Good: cooperative cancellation, progress parsing, error capture | `logs_utils` (error heuristics), `constants` | The heart of the future *process runner* | Rewrite it light: add a timeout (missing!), separate stdout/stderr, normalized events instead of emoji logs |
| `engine/progress.py` (pure parsers + `ProgressEvent`) | **Reuse** | Very good, pure, tested | `constants` (regexes) | Directly transposable to `JobEvent.step.progress` | Copy the parsers + regexes; map to Content's event model |
| `engine/spec.py` (JobSpec v2) | **Rewrite** | Well built but the wrong abstraction for Content | API, DB (spec JSON), UI | Single-source, download-oriented, exposes provider details (`format_id`, `custom_args`, `ytdlp_client`) | Replace with `GenerationRequest` (sources[] + outputs[]); a compatibility adapter is possible later |
| `engine/jobs.py` (`run_download_job`) | **Rewrite** | Functional, resilient (cache, resumption) | workspace, status.json, file_system_utils | Implicit planning + execution + delivery in a single function; dispatch through `content.*.enabled` booleans | Break it into a Planner (builds the steps) + an Executor (runs them); keep the behaviours (cache reuse, fallback) as specifications |
| `engine/profiles.py` (codec/client fallback) | **Adapt (deferred)** | Valuable — hard-won yt-dlp know-how | `quality_profiles`, `medias_utils`, ctx | This is the video selection strategy; outside the minimal slice | Keep as a reference; wrap it in the `video` processor when the `video` output is implemented |
| `engine/content.py` (yt-dlp audio/subs/extras builders) | **Adapt** | Good: commands as argument lists, no shell | `core.build_cookies_params`, spec | A direct basis for Content's `audio`/`subtitles`/`thumbnail` steps | Take the command construction over, driving it from the plan rather than from the spec |
| `engine/cache.py` + `status.json` | **Adapt (deferred)** | A good idea (an artifact reused without network) | File naming conventions | Matches `execution.reuse_existing` | Take the principle over; the truth must come from the artifacts DB, not from a per-directory `status.json` |
| `engine/playlist.py` (fan-out) | **Adapt (deferred)** | Simple, tested | spec, store | Prefigures `scope: each_item` over a collection | A reference for the planner's fan-out; outside V1 |
| `api/db.py` (SQLite WAL JobStore, `claim_next_queued`, `requeue_running`) | **Adapt** | Solid: atomic `BEGIN IMMEDIATE` claim, WAL, connection per call, recovery on startup | Specific progress columns | The SQLite-queue pattern is exactly Content's V1 `JobQueue` | Take the pattern over; the schema is replaced (jobs + job_steps + job_events + artifacts), progress through events rather than overwritten columns |
| `api/queue.py` (asyncio pool + executor) | **Reuse** | Good, compact, tested | JobStore | Generic | Take it over almost as is (claim → run_in_executor → outcome) |
| `api/runner.py` (default_runner) | **Rewrite** | Correct | engine, settings | Merges playlist fan-out, probe, execution and persistence | Replaced by Content's Planner + JobExecutor |
| `api/models.py` (Pydantic mirror) | **Rewrite** | Clean | engine spec | The public contract changes entirely | New `GenerationRequest` Pydantic models (discriminated unions) |
| `api/main.py` (injectable `create_app()`, queue lifespan) | **Adapt** | Clean, testable | settings, store | A reusable app skeleton | Take the pattern over (DI through parameters, lifespan, a lazy module-level app) |
| `workspace.py` (a workspace per video/platform) | **Adapt** | A good principle (1 resource = 1 stable directory, never re-downloaded) | YouTube-centric URL parsing | Becomes the `/data/jobs/<job_id>/` layout + a resource cache | V1: a per-job workspace; the per-resource cache will come back with `reuse_existing` |
| `url_utils.py` (`build_url_info`, sanitize, bot/age errors) | **Adapt** | Rich but dense; valuable heuristics (bot detection, age restriction) | direct subprocess, logs | Feeds the yt-dlp provider | Extract the `yt-dlp -J` call + error classification into `YtDlpProvider` |
| `medias_utils.py` / `quality_profiles.py` | **Adapt (deferred)** | Key know-how (format/language/profile analysis) | yt-dlp JSON | Needed for the `video` output and the advanced audio options | Keep as a reference library for the future video processor |
| `subtitles_utils.py` | **Adapt (deferred)** | Bulky | yt-dlp, ffmpeg | For advanced subtitle options (embed, convert) | V1 sticks to `--write-subs/--write-auto-subs` |
| `file_system_utils.py` (sanitize_filename, safe move, cleanup) | **Adapt** | Good | logs | Backend-side file naming = a Content security requirement | Take `sanitize_filename` and the move-with-collision over; cleanup rewritten per job |
| `process_utils.py` | **Discard** | Mediocre (returns fake `CompletedProcess` objects on error) | logs | Duplicates `run_cmd`; the "fake result" anti-pattern hides errors | Replaced by Content's single process runner |
| `logs_utils.py` (yt-dlp error heuristics: auth, expired cookies, unavailable format) | **Adapt** | The 3 classifiers are valuable; the rest (Streamlit push_log) is not | Streamlit (safe_push_log) | Normalized error classification | Extract only `is_authentication_error`, `is_cookies_expired_warning`, `is_format_unavailable_error` |
| `config.py` (env settings, container detection) | **Adapt** | Correct | HomeTube variables | A reusable env + defaults pattern | Rewrite it light (pydantic-settings or a dataclass) with Content's variables |
| `status_utils.py`, `tmp_files.py`, `playlist_sync.py`, `sponsors_utils.py`, `cut_utils.py`, `notifications.py`, `integrations_utils.py` | **Discard (V1)** | Variable | HomeTube conventions | HomeTube product features (SponsorBlock, cut, Jellyfin, notifications) outside Content's V1 domain | Nothing to do; re-evaluate feature by feature later |
| `ui/` (thin Streamlit client) | **Adapt later** | Good UX, a clean HTTP client pattern | the HomeTube API | Content's UI will be a client of the v1 API | Re-wire onto `/api/v1` + JobEvents once the API is stable |
| legacy `app/main.py` (4407 l.) | **Discard** | A monolith | Everything | Already replaced by 3.x itself | None |
| Docker (`backend.Dockerfile` on a yt-dlp base, split compose, all-in-one, healthcheck, non-root user) | **Reuse** | Stable, clean (multi-arch, wheels only, non-root) | HomeTube paths | Generic for a yt-dlp/ffmpeg backend | Copy and rename the paths/env at packaging time |
| Tests (37 files; yt-dlp JSON fixtures in `tests/ytdlp-json/`) | **Adapt** | Good engine/api coverage | app structure | The **real yt-dlp fixtures** are a major asset | Reuse the fixtures to test analysis/planning without network |
| GitHub Actions CI (ci + multi-arch build) | **Adapted — done** | Solid | GitHub repo, PR-driven, Codecov | Job shape: install → lint → test, and QEMU + Buildx for multi-arch | Transposed into `.github/workflows/ci.yml`: same skeleton, but triggered on pushes and tags (no PRs — GOVERNANCE.md), driven through `make`, actions pinned by SHA, no coverage upload, and the image is built without being pushed. Forgejo Actions also reads `.github/workflows/` |

## 3. Proven behaviours to preserve (specifications, not code)

Independently of the code, these HomeTube behaviours are requirements for Content:

1. **A resource is never re-downloaded** if a complete artifact exists (a cache by resource identity, not by job).
2. **Cooperative cancellation**: the worker checks a flag between each output line of the process; `terminate` then `kill` after 5 s.
3. **Recovery on startup**: orphaned `running` jobs (a crash) are re-queued (`requeue_running`).
4. **Atomic claim** in SQLite (`BEGIN IMMEDIATE`) — no external broker.
5. **Analysis cached with a TTL** (`url_info.json`, 96 h by default) and a completeness check before reuse.
6. **Profile-based fallback** (codec then yt-dlp client) for video — to be reintroduced with the `video` output.
7. **Classification of yt-dlp errors** (auth, expired cookies, unavailable format, bot detection, age restriction) to produce actionable errors.
8. **Commands as argument lists**, never `shell=True`.
9. **Parsed progress** (download %, fragments, generic ffmpeg %) with throttling (1 % / 5 %).
10. **An injectable app** (`create_app(store=..., runner=...)`) for hermetic API tests.

## 4. Major gaps between HomeTube and Content's domain

| Gap | Consequence in HomeTube | Content's answer |
| --- | --- | --- |
| No `ExecutionPlan` | Strategy decided during execution, invisible, not testable in isolation | A separate Planner, a persisted plan (snapshot) |
| No `Artifact` | A single `output_path`; audio+video+subs indistinguishable | An `artifacts` table + provenance |
| No `JobEvent` | Progress overwritten, no history and no replayable SSE | An append-only, sequenced `job_events` table |
| No steps | Opaque global failure | `job_steps` linked to the plan |
| No partial success | A job is completed or failed | `partially_succeeded` + `failure_policy` |
| A contaminated contract | `custom_args`, `ytdlp_client`, `format_id` in the public contract | A declarative `GenerationRequest`; provider details in the internal plan |
| No timeout on `run_cmd` | A blocked process = a blocked worker | A mandatory timeout in the process runner |
| stdout/stderr merged | Limited diagnostics | Separated in the job's logs |

## 5. Summary of the actions

- **Direct reuse**: the progress parsers, the asyncio queue pattern, Docker, the test fixtures.
- **Adapt in V1**: probe → `YtDlpProvider.analyze`, `run_cmd` → the process runner (with a timeout), JobStore → Content's persistence, the `create_app` pattern, `sanitize_filename`, the error classifiers, the audio/subs/thumbnail command builders.
- **Deferred adaptation**: video profiles, the per-resource cache, playlists/collections, advanced subtitles, the UI.
- **Rewrite**: the contract (JobSpec → GenerationRequest), the runner (→ Planner + Executor), the API models, the DB schema.
- **Discard**: the legacy `main.py`, `process_utils`, HomeTube's product features (SponsorBlock, cut, notifications, Jellyfin) as long as the domain does not ask for them.

Renaming alone is explicitly forbidden: a `Download` renamed `Job` would still be a `Download`. The rewrite changes the invariants (multi-source, an explicit plan, 0..N artifacts, append-only events), not just the names.
