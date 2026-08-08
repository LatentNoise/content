# ADR 0009 — Separate `tmp`, `work`, `artifacts` and `cache`

Status: accepted (2026-07-26)

## Context

ADR 0008 established per-job isolation (`jobs/<id>/{sources,work,artifacts,logs,snapshots}`), but two boundaries remained blurry:

1. **`tmp` vs `work`** — yt-dlp and ffmpeg wrote their *in-flight* files (`.part`, partial outputs, a copy of `cookies.txt`) directly into `work/`, mixing disposable technical incompleteness with the job's valid intermediates.
2. **A "cache" that was not one** — the analysis probe scratch lived in `data/analysis-cache/<key>/` (never cleaned up, badly named), while the real analysis cache is in the database (`load_fresh_analysis`).

On top of that, cross-job reuse (`execution.reuse_existing`, promoted by ADR 0008) was **on by default**, which amounts to enabling a cross-job cache before its boundaries and governance had been laid down.

## Decision

Four roots, four distinct lifecycles. `tmp != work != artifact != cache`.

```text
$CONTENT_DATA_DIR/
├── jobs/<job_id>/{sources,work,artifacts,logs,snapshots}/
├── tmp/            # incomplete, technical, disposable — never an artifact
└── cache/          # validated, reusable across jobs — DISABLED in V1
```

- **`tmp`**: incomplete/technical writes (download in progress, staging before an atomic move, probe scratch). Cleanable by age; a crash may leave orphans behind; a file in `tmp` is never deemed valid.
- **`work`**: *valid* intermediates specific to one job, shareable between steps of that job, never shared across jobs; purged according to `retention.working_files`.
- **`artifacts`**: persistent business results (an `Artifact` row, a provenance, exposed by the API); independent retention; never removed by the `tmp`/`work` cleanup.
- **`cache`**: reserved for validated results reusable across jobs. **Disabled by default** (`CONTENT_CACHE_ENABLED=false`). The directory is not created while the cache is off.

Consequence for reuse: `reuse_existing` stays in the contract but becomes **inert when the cache is disabled**. The deterministic rule adopted: `reuse_existing=true` + cache off ⇒ a `reuse_unavailable` warning at submission (an honest contract rather than silent ignorance). The database analysis cache (TTL) is a pre-existing lightweight piece of infrastructure, distinct from the cross-job filesystem cache; it is kept.

Path resolution is centralized in `content/storage/paths.py` (`StoragePaths`, `safe_segment`, `publish_file`); `JobStorage` derives from it.

## Consequences

- Clean, testable boundaries (`test_storage.py`); the `tmp` cleanup can no longer touch an artifact.
- **Atomic** publication (`os.replace`, cross-FS fallback via temp+rename): a partial file never shows up as an artifact.
- V1 is fully functional **without a cross-job cache**; two identical jobs redo their work.
- Accepted cost: no cross-job sharing in V1 (re-downloading). The cache will come back behind the same boundary (`cache/`) without touching the callers.
- Deliberately not implemented (later slices): routing *all* in-flight yt-dlp/ffmpeg writes to a per-operation `tmp/<job>/<step>/` (today they stay in `work/` but are never promoted to artifacts); a maintenance command for purging by age; the cache itself (LRU/TTL/CAS).

## Alternatives considered

- **Keeping `reuse_existing` on by default**: faster, but enables a cross-job cache with no clear boundary or governance — against the "lay the boundaries first" goal.
- **A `reuse_existing=false` default rather than a `CONTENT_CACHE_ENABLED`**: moves an infrastructure decision (does the cache exist?) into the per-request public contract; we prefer an installation switch plus an honest warning.
- **Cleaning up `data/analysis-cache/` immediately**: that is existing dev data; we stop writing to it (scratch relocated under `tmp/analysis/`) without deleting the old directory.
