# ADR 0005 — Public intent vs internal operations

Status: accepted (2026-07-17)

## Context

The public contract speaks in **logical results** (`type: transcript`), never in tools (`type: whisper`). But execution needs precise technical verbs. We need a stable intermediate vocabulary that is neither the contract nor the name of a binary.

## Decision

Three levels of vocabulary, with an explicit translation between each:

1. **Public output types** (`audio`, `transcript`…) — the contract, stable.
2. **Internal operations** (`media.acquire_audio`, `speech.transcribe`…) — the abstract verbs of PlanSteps; stable, independent of the provider that carries them out.
3. **Providers/tools** (`ytdlp`, `ffmpeg`, `faster-whisper`…) — chosen by the planner, referenced by stable name in the steps, visible only in the plan and the provenance.

One deliberate exception: `preferences.providers` lets the client express a *preference* (never a structural requirement) over logical families of providers.

## Consequences

- Replacing yt-dlp with another acquirer = a new provider carrying out the same operations; plans and contract unchanged.
- Provenance stays precise (operation + provider + tool version).
- Documented edge case: `metadata.export` is carried out by the analysis provider (it only materializes the normalized metadata) — acceptable as long as no dedicated "system" provider is justified.

## Alternatives considered

- **Operations = tool names**: recreates the implementation leak at the internal level and complicates multi-provider work.
- **No intermediate level** (the planner calls functions directly): plans are not serializable, recovery after a crash is impossible.
