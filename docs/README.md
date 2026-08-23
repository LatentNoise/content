# Content documentation

The map of everything documented, from intent to operations. The root
[README](../README.md) is the front door; this folder is the reference.

## Product — what Content is for

- [product/vision.md](product/vision.md) — why this exists
- [product/scope.md](product/scope.md) — what V1 does and deliberately does not
- [roadmap/roadmap.md](roadmap/roadmap.md) — the milestones ·
  [roadmap/current-milestone.md](roadmap/current-milestone.md) — where we are

## The contract — what clients depend on

- [contract.md](contract.md) — the public contract: `GenerationRequest`,
  sources/outputs, `delivery` (folder, family base name, `mode`), errors, the
  §9 stability policy
- [domain.md](domain.md) — the concepts (`GenerationRequest` ≠ `ExecutionPlan`
  ≠ `Job` ≠ `Artifact`), state machines, `display_filename`

## Architecture — how the engine is built

- [architecture.md](architecture.md) — the engine's internal shape
- [architecture/analysis-to-capabilities.md](architecture/analysis-to-capabilities.md)
  — facts → capability resolution (ADR 0013)
- [architecture/invariants.md](architecture/invariants.md) — the rules that
  never move
- [storage.md](storage.md) — tmp ≠ work ≠ artifacts ≠ cache, and the delivery
  library (ADR 0009/0018)
- [repository-architecture.md](repository-architecture.md) — the monorepo: one
  engine, the SDK as the single door, every client thin (ADR 0011/0015/0016)
- [architecture-decisions/](architecture-decisions/) — ADR 0001–0027. Awaiting a
  decision: [0027 playlist synchronization](architecture-decisions/0027-playlist-synchronization.md),
  [0026 what a security label can honestly mean](architecture-decisions/0026-what-a-security-label-can-honestly-mean.md),
  [0025 retrying only what failed](architecture-decisions/0025-retrying-only-what-failed.md),
  [0023 retention and reclaiming disk](architecture-decisions/0023-retention-and-reclaiming-disk.md),
  [0024 no authentication, and what would change that](architecture-decisions/0024-no-authentication-is-still-the-answer.md).
  Recently accepted and implemented:
  [0022 `original` as a language token](architecture-decisions/0022-original-as-a-language-token.md)
- [hometube-reuse-audit.md](hometube-reuse-audit.md) — what was kept from
  HomeTube, component by component
- [playlist-synchronization-review.md](playlist-synchronization-review.md) — the
  evidence behind ADR 0027: what HomeTube's sync solved, what not to port from
  it, and the four things that must be decided before any sync code exists

## Clients

One engine, one official SDK, thin clients — each documents itself:

- [packages/python-sdk](../packages/python-sdk/README.md) — the **one** API
  client (sync + async, typed)
- [apps/cli](../apps/cli/README.md) — the `content` CLI
- [apps/mcp](../apps/mcp/README.md) — the MCP server for AI agents
- [apps/browser-extension-chromium](../apps/browser-extension-chromium/README.md) — send the
  current tab to the engine (the only non-SDK client, ADR 0016)
- [apps/web-hometube](../apps/web-hometube/README.md) — HomeTube, the YouTube
  UI · [apps/web-studio](../apps/web-studio/README.md) — the full-contract UI ·
  [apps/web-admin](../apps/web-admin/README.md) — the operations console

## Operations — running it

- [operations/deployment.md](operations/deployment.md) — docker compose, the
  full environment inventory, the delivery library, credentials
- [operations/pdf-rendering.md](operations/pdf-rendering.md) — Typst/ReportLab,
  fonts, glyph policy
- [operations/ytdlp-base-image.md](operations/ytdlp-base-image.md) — how the
  yt-dlp base image is pinned, how updates are noticed, how a bump is validated
- [operations/browser-extension-distribution.md](operations/browser-extension-distribution.md)
  — the release zip, and what publishing on the Chrome Web Store would involve
- [releases/](releases/) — the authored release notes, one `vX.Y.Z.md` per
  release; `release-draft.yml` uses the matching file as the draft's body.
- [operations/mcp-registry.md](operations/mcp-registry.md) — publishing
  `content-mcp` to the official MCP registry: `server.json`, the PyPI
  ownership marker, and what being listed does (and does not) buy
- [operations/threat-model.md](operations/threat-model.md) — what an attacker
  who can reach the API can do, what bounds it, and why the port must not be
  published (ADR 0024/0026)
- [operations/release-readiness.md](operations/release-readiness.md) — what
  "releasable" means · [operations/github-settings.md](operations/github-settings.md)
  — the hosting settings the governance model assumes

## Development — working on it

- [development/validation.md](development/validation.md) — the Definition of
  Done and `make validate`
The maintainer's own working notes (`work/`) are not versioned; documentation
here is the whole public record.
