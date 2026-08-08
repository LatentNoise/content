# ADR 0004 — One ArtifactRequest produces 0..N Artifacts

Status: accepted (2026-07-17)

## Context

The assumption "one request = one file" (HomeTube's, with its single `output_path`) breaks immediately on: multi-language subtitles, keyframes, per-page OCR, playlists, partial failure. The contract must represent cardinality honestly from V1 on.

## Decision

- An `ArtifactRequest` produces **0..N** `Artifacts`. 0 is a legitimate result (nothing available, or failure) whose effect depends on `required` × `failure_policy`.
- Two orthogonal levels of cardinality: `scope` (the number of **logical instances**: `single`, `each_source`, `each_item`, `all_sources`) and the cardinality **inherent to the type** (e.g. subtitles → one artifact per language found, labelled in `attributes` — contract decision D7).
- Only the `artifacts` table is authoritative after the fact; every artifact is linked to its `artifact_request_id`.

## Consequences

- Clients always iterate over a list of artifacts, never over "the file".
- `partially_succeeded` and the `partial_output` warning become cleanly expressible.

## Alternatives considered

- **1 request = 1 artifact, with implicit collections** (a zip, say): simple, but destroys fine-grained provenance and forces artificial formats.
- **Cardinality carried entirely by `scope`**: impossible — the per-language cardinality of subtitles is not a scope over sources.
