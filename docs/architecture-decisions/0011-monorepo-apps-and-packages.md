# ADR 0011 — `apps/` and `packages/` monorepo

Status: accepted (2026-07-26) — migration complete (backend included)

## Context

Content is an API-first platform: a single business engine and a single public
contract, consumed by several applications with different audiences (the
specialized HomeTube UI, a general-purpose UI, a CLI, later a browser extension
and an admin console). At the root, the backend (`backend/`), two frontends
(`frontend/`, `frontend-general/`) and a CLI (`cli/`) coexisted with no explicit
boundary. We needed to make those boundaries **explicit and testable** without
breaking the working project or adding premature complexity.

## Decision

Adopt a monorepo structure:

```text
apps/
├── backend/        (was backend/ — the engine: API, domain, execution…)
├── web-hometube/   (was frontend/ — HomeTube, the specialized YouTube UI)
├── web-general/    (was frontend-general/ — Content Studio, the general UI)
└── cli/            (was cli/ — the `content` command)
packages/           (none for now)
```

> **Later renames.** The names above are the ones decided here and are left as
> written. Since then `web-general/` became **`apps/web-studio/`**, and
> `apps/web-admin/` (Content Admin, the operations console) was added. See
> [../repository-architecture.md](../repository-architecture.md) for the current
> layout.

Dependency rules (verifiable):

- an application may depend on a package; a package never depends on an
  application;
- applications do not import each other and do not import the backend's Python
  code; they are clients of the public API;
- a specialized UI may restrict/preconfigure the contract, but **does not invent
  a parallel contract** (no `POST /youtube/download`);
- the contract is shared through the API/OpenAPI, not by manually copying the
  models.

Tooling: no Nx/Turborepo/Bazel (oversized). The Python backend keeps its
`pyproject`/venv; the client apps are self-contained. The root `Makefile` stays
the entry point (`make validate` lints and tests the backend **and**
`apps/cli`).

## Migration (incremental and safe)

Done: `frontend → apps/web-hometube`, `frontend-general → apps/web-general`,
`cli → apps/cli`, then **`backend → apps/backend`**. Updated
`docker-compose.yml` (build contexts), the `Makefile` (VENV, SRC, `cd
apps/backend`, CLI tests), the `.gitignore` allowlist and the docs.

The backend venv used an **absolute-path** editable install
(`__editable___content_backend…finder.py`) which breaks `import content` after a
move; it was **recreated** at the new location:

```bash
cd apps/backend && rm -rf .venv && uv venv .venv \
  && uv pip install -e ".[test,dev]" --python .venv/bin/python
```

Trap encountered: an IDE "move refactor" had rewritten every import as
`apps.backend.content.*` / `apps.backend.tests.*` (not importable — the package
is still `content`); they were restored to `content.*` / `tests.*`.
`make validate` green (backend 243 + CLI 6), `docker compose config` valid, and
a Docker build + real smoke test (analysis + download + delivery) OK from the
new layout.

## Consequences

- A readable structure expressing "several applications, a single
  engine/contract".
- Boundaries documented for contributors and enforced by a guard-rail test
  (`tests/test_layering.py`).
- `packages/` stays empty until real sharing exists; first concrete candidate:
  the HTTP `client.py` duplicated across the three apps → a small shared Python
  package (deferred at the time; **delivered** as `packages/python-sdk` — see
  ADR 0015, which makes it the single door to the API).
- Accepted debt at the time of writing: the backend was not yet under `apps/`
  (a temporary, documented asymmetry). **Settled** — the move completed with
  this ADR's own migration, which is what the status line records. The
  contradiction between the two is corrected here rather than by rewriting the
  original text.

## Alternatives considered

1. **Leaving everything at the root** — no boundary, does not scale to the
   future apps.
2. **`clients/` instead of `apps/`** — `apps/` includes the deployable backend,
   not just clients; more accurate.
3. **A single multi-mode web app** (`if mode === …`) — the journeys are too
   different, pointless coupling; we prefer distinct apps + shared packages.
4. **Multi-repo** — loses contract↔clients atomicity, tooling overhead.
5. **Nx/Turborepo** — over-engineering for a Python + Streamlit stack.
