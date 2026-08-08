# ADR 0002 — Canonical `sources[]`, always plural

Status: accepted (2026-07-17)

## Context

Most V1 requests carry a single source; the temptation is to offer both `"source": {}` and `"sources": []`. Two competing shapes create two validation paths, two snapshot shapes, ambiguous SDKs, and a painful migration the day multi-source becomes central (aggregation, fan-in — an intended product goal).

## Decision

The canonical contract has exactly one shape: `sources` is **always** an array (1..N), even for a single source. Every source carries a unique local `id`; outputs reference those ids. SDKs and CLIs may accept shorthands but normalize to the canonical shape before the API.

## Consequences

- A single validation/planning path; multi-source is not a special case.
- Minimal verbosity for the simple case (rule D3 waives `from_sources` when there is a single source).

## Alternatives considered

- **Singular `source` + optional `sources`**: marginally better ergonomics, permanent structural cost (two shapes to support forever).
- **Inference by position** (outputs consume the source at the same index): implicit magic, fragile under reordering — rejected in favour of explicit ids.
