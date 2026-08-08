# Content Admin (`apps/web-admin`)

The operations console for the Content back-end — the backend is the heart of
the project, this is its cockpit. It is strictly an **observability + control**
client of the public API: it does **not** create downloads (that is HomeTube /
Content Studio). It replaces the old, incoherent `/ui` page.

## What it shows

- **Overview**: version, cache on/off, concurrency, analysis TTL; a live jobs
  *pulse* (active count + breakdown by status); the installed runners
  (providers/processors, operations, availability); language prefs, credentials
  (ids only), storage paths.
- **Environment**: every `CONTENT_*` variable the engine reads, with its
  **effective** value, whether it was **set in the environment** or fell back to
  a default, its category, and a one-line description. Secrets (API keys,
  credentials) are never shown — only presence and length. This is the fix for
  "env vars not showing well" and doubles as living configuration docs; it is
  backed by `/api/v1/system` → `environment` (`config.describe_environment`).
- **Jobs**: filterable list (with relative timestamps) + full detail (steps with
  a done counter, submitted GenerationRequest, artifacts + provenance, events,
  per-step logs) with cancel / retry. An opt-in sidebar **auto-refresh (5s)**
  polls only while jobs are in flight.
- **Storage & Cache**: bytes + counts per family (jobs / delivery / tmp / cache),
  delivery folders, cached analyses.
- **Contract & API**: links to Swagger / ReDoc / openapi.json, the
  GenerationRequest schema, and a raw API tester with quick-endpoint buttons.

The palette is aligned with HomeTube (the shared `#8B5CF6 → #D946EF` gradient,
the same dark cards and pills) so the suite reads as one product.

## Run

```bash
docker compose --profile admin up -d --build web-admin   # port 8503
# or locally:
CONTENT_API_URL=http://localhost:8010 streamlit run app.py --server.port 8503
```
