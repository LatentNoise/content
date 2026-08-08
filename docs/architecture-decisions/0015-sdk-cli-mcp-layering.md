# ADR 0015 — SDK / CLI / MCP: one single door to the engine

Status: accepted (2026-07-31). Follows ADR 0011 (monorepo) and prepares the
opening of Content as a platform (Python, terminal, MCP agents).

## Context

Content must be usable from Python, from a terminal and from any MCP-compatible
agent — **without ever duplicating the business logic**. The REST API remains the
single source of truth. Before this ADR, two HTTP clients already existed (the
UIs' `content-client` package and a private copy in the CLI), proof of exactly
the drift to avoid.

## Decision — a strict one-way stack

```
Content Backend (REST API)   ← the only business logic
        │
        ▼
   Content Python SDK (content_sdk)   ← the single official client
    │            │
    ▼            ▼
Content CLI   Content MCP    ← thin consumers of the SDK
```

Invariants:

- the **backend** never knows about MCP;
- the **SDK** never depends on MCP;
- the **CLI** and the **MCP** server never speak HTTP directly — everything goes
  through the SDK. A guard-rail test (`tests/test_layering.py`) fails if a
  consumer imports `requests`/`httpx`/`urllib.request`/`http.client`.

## Components

| Layer | Package | Role |
|---|---|---|
| Engine | `apps/backend` (`content`) | All the logic; REST `/api/v1` |
| SDK | `packages/python-sdk` (`content_sdk`) | The official client: httpx (sync + async), pydantic v2; `models.py` (pure data) vs `resources.py` (`Analysis`/`Job` objects); typed errors; conservative retries |
| CLI | `apps/cli` (`content_cli`) | Thin argparse; uses the SDK only |
| MCP | `apps/mcp` (`content_mcp`) | An `MCPServer` (stdio); intent-level Tools + `content://` Resources; uses the SDK only |
| UIs | `apps/web-hometube` / `apps/web-studio` / `apps/web-admin` | Streamlit; through `content_sdk.compat` |

The SDK is the only place that imports an HTTP client. Each layer is
independently distributable (`content-sdk` → `content-cli` / `content-mcp`).

## Naming — `content_sdk`, not `content`

The engine package is already imported as `content`
(`apps/backend/content/`). The SDK **cannot** be called `content` (a collision
in a shared installation). It is therefore imported as `content_sdk`
(distribution `content-sdk`).

## A compatibility client for the UIs

The three Streamlit UIs were written against the old `content_client` (returning
dicts). Rather than a risky rewrite to the object API, the SDK exposes
`content_sdk.compat`: **the same dict surface, on top of the SDK's transport**.
One package, one HTTP transport. New consumers should prefer the object API
(`content_sdk.ContentClient`); `compat` only exists so the UIs did not have to
be rewritten during the consolidation. `packages/content-client` is deleted.

## Consequences

- The target ergonomic flow works (built on ADR 0014):
  `analyze → get_capabilities(id) → generate(id) → job.wait() → artifacts`.
- Zero duplication: both historical HTTP clients are deleted; the guard rail
  prevents any HTTP access outside the SDK from reappearing.
- `apps/web-general` was renamed `apps/web-studio` (the product name, Content
  Studio) — a cosmetic change applied separately from the layering.

## Alternatives rejected

- **An SDK named `content`**: collides with the engine package; rejected.
- **Rewriting the UIs to the object API now**: costly and risky for little
  value; `compat` provides the consolidation without the rewrite.
- **One MCP Tool per REST endpoint**: rejected — the MCP interface is
  *intent-level* (high level), not a mirror of the API.
- **Aggressive retries in the SDK**: rejected — retries only on transport
  errors and 5xx from GETs; creations are only replayed with an idempotency key.
