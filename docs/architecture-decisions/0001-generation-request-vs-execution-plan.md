# ADR 0001 — GenerationRequest ≠ ExecutionPlan

Status: accepted (2026-07-17)

## Context

HomeTube merges intent and strategy into a single `JobSpec`: the `content` field expresses a wish, but `quality.format_id`, `advanced.ytdlp_client` and `advanced.custom_args` expose yt-dlp details to the client. The real strategy (probe → profiles → fallback) is decided during execution, invisible and impossible to test in isolation. Content wants a public contract that stays stable for years while the internals change.

## Decision

Two distinct objects, never merged:

- `GenerationRequest` (public): sources, outputs, preferences, constraints, execution policy. No tool, provider, command or path.
- `ExecutionPlan` (internal): resolved steps (operation + provider + params), dependencies, bindings to the outputs, warnings. Persisted as an immutable snapshot, never accepted as API input.

Planning is an explicit phase (`planning/`) between validation and execution.

## Consequences

- The planner is testable without executing; execution is replayable from the persisted plan.
- Changing provider (or its syntax) never touches the contract.
- Cost: one more layer (the planner) and an outputs→steps mapping to maintain.

## Alternatives considered

- **A single enriched spec** (the HomeTube model): simpler to start with, but every new output type leaks technical details into the contract — precisely the debt the audit identified.
- **A publicly writable plan** (the client sends a plan): turns the API into a technical orchestrator, against the product (the client declares, the engine decides).
