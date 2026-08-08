# ADR 0014 — Addressable analyses: `analysis_id` as a public resource

Status: accepted (2026-07-31). A prerequisite for the official SDK/CLI/MCP (the
API must be addressable before high-level clients are exposed).

## Context

`POST /analyses` already returned an `analysis_id`, but it was **not
addressable**: there was no `GET /analyses/{id}`, and neither `/capabilities`
nor `/jobs` accepted it as input. Analysis facts are cached **by
`resource_key`** (the resource identity computed by the provider), not by
`analysis_id` (`analysis/service.py`, `persistence/store.py`). The result: the
contract was **source-centric** at every step — an advanced client had to re-post
the full `sources` to `/capabilities` and then to `/jobs`, and an SDK would have
had to keep **hidden state** (the original sources) to offer an
`analyze → capabilities → generate` flow.

## Decision

Make the analysis an **addressable public resource**, without duplicating the
heavy facts.

1. **An addressable record** (`analysis_records`, keyed by `analysis_id`)
   **references** the fact cache by `resource_key` — it does not copy the facts.
   It contains at least: `analysis_id`, the normalized `sources`, the
   `resource_keys` (one per source, ordered), `analyzer_version`, `created_at`,
   `expires_at`.
2. **`GET /api/v1/analyses/{analysis_id}`** rebuilds the analysis by **joining**
   the facts from the cache by `resource_key`. It is a **safe and idempotent**
   read: it **never** re-runs the analysis.
3. **`/capabilities` and `/jobs` accept `sources` XOR `analysis_id`** — exactly
   one of the two. The `sources` mode (stateless, direct) is kept; the
   `analysis_id` mode resolves to the memorized `sources` then follows the
   **unchanged** pipeline (the analysis goes through the warm cache again →
   identical result).

## Deterministic lifecycle (reads)

Every consumption of an `analysis_id` applies the same rule, in order:

- **`analysis_not_found` → 404**: no record for that id.
- **`analysis_expired` → 410**: the record exists **but** either
  `now > expires_at`, or a referenced fact (`resource_key`) is missing/stale
  from the cache.

The record and the facts have independent TTLs; a purged fact cache is reported
as **expired**, never silently re-derived. Re-analyzing is an explicit
`POST /analyses`, never a side effect of a `GET`.

Note: `GET /analyses/{id}` requires the facts to be present (it has to return
them), and therefore checks their freshness; the **action** endpoints
(`/capabilities`, `/jobs`) in `analysis_id` mode only require the record to be
fresh — they re-derive the facts as usual.

## Exclusivity in the contract, not only in the handlers

The `sources` | `analysis_id` exclusivity is a **property of the public models**
(`CapabilitiesRequest`, `GenerationRequest`): it is declared in the JSON schema
(`json_schema_extra` → `oneOf`) and therefore **reflected in the
OpenAPI/Swagger**. The rejection with stable codes
(`sources_or_analysis_id_required`, `sources_and_analysis_id_conflict`) is
emitted at the API boundary, where the other structural errors (e.g.
`duplicate_id`) are emitted.

## Consequences

- An SDK/CLI/MCP can offer `analyze(source) → id`, `get_capabilities(id)`,
  `generate(id, outputs=…)` **without keeping hidden state** based on the
  sources; a workflow becomes resumable from any client.
- Every analysis (including one triggered by a job submission) persists an
  addressable record; they expire with the analysis TTL and are purged by
  `POST /cache/purge`.
- An additive migration: the `sources` mode stays fully supported;
  `analysis_id` is only an **alternative** input. SQLite schema migration no. 2
  (`analysis_records`).

## Alternatives rejected

- **An SDK keeping the sources client-side** (hidden state): rejected —
  fragile, not resumable across clients, leaves the contract source-centric.
- **Duplicating the facts under `analysis_id`**: rejected — double storage of
  heavy data; the record **references** the cache by `resource_key`.
- **A `GET /analyses/{id}` that re-analyzes on the fly** if the facts are gone:
  rejected — a GET must be safe/side-effect-free and deterministic; missing
  facts are a `410`, not a surprise network call.
