# Repository architecture (monorepo)

Content is a monorepo: **a single business engine and a single public contract**,
consumed by several applications **through a single official client** (the Python
SDK). Decisions: ADR 0011 (monorepo), ADR 0015 (SDK/CLI/MCP layering).

## Structure

```text
content/
├── apps/
│   ├── backend/        the engine: FastAPI API, domain, planning, execution… (venv lives here)
│   ├── web-hometube/   HomeTube — the specialized YouTube UI (Streamlit)
│   ├── web-studio/     Content Studio — the general UI, the whole contract (Streamlit)
│   ├── web-admin/      Content Console — the backend's operations console (Streamlit)
│   ├── web-tests/      hermetic AppTests for the 3 UIs (fake client, make test-ui)
│   ├── cli/            the `content` command (thin argparse on top of the SDK)
│   ├── mcp/            the `content-mcp` server (MCP stdio on top of the SDK)
│   └── browser-extension/  Chrome MV3, plain ES modules — no bundler (ADR 0016)
├── packages/
│   └── python-sdk/     content_sdk — THE official client (sync + async, typed)
├── tests/              architecture guard rails (e.g. no HTTP outside the SDK)
├── docs/               contract, invariants, ADRs, architecture
├── work/               working protocol (packages, discoveries)
├── docker-compose.yml  backend + UIs (`studio` / `admin` / `all` profiles)
└── Makefile            the entry point (`make validate` = the official gate)
```

## Role of each application

| App | Audience | Role |
| --- | --- | --- |
| `apps/web-hometube` | the YouTube general public | Paste a URL/playlist, choose video/audio/subtitles/quality/sponsors, launch, follow, download. |
| `apps/web-studio` (Content Studio) | advanced | Exposes **the whole** contract: multiple sources (url/file/text), every output with its options, preferences/constraints. |
| `apps/web-admin` (Content Console) | operator / dev | **Observe & pilot** the backend: jobs/steps/events/logs, storage & cache, capabilities, environment. Does not create downloads. |
| `apps/cli` (`content`) | terminal / scripts | Analysis, `video`/`audio` shortcuts, `submit`, job tracking, download. |
| `apps/mcp` (`content-mcp`) | AI agents | Intent-level Tools (`analyze_source`, `generate`, …) + `content://` Resources over stdio. |
| `apps/browser-extension` | anyone watching a video | Chrome MV3: send the current tab to the engine, capability-driven. The **only client that does not use the SDK** — it is JavaScript, so it speaks `/api/v1` directly (ADR 0016). |
| `packages/python-sdk` (`content_sdk`) | Python developers | The official client: `analyze → get_capabilities(id) → generate(id) → job.wait()`. |
| `apps/backend` | — | The only business logic; exposes the public API + OpenAPI. |

## Dependency rules (ADR 0015)

```text
Content Backend (REST /api/v1)   ← the only business logic
        │
   packages/python-sdk           ← the only *Python* door to the API
    │        │        │
   cli      mcp    web-*         ← thin consumers of the SDK

   browser-extension             ← JavaScript: speaks /api/v1 directly (ADR 0016)
```

- The backend **never** knows about MCP; the SDK **never** depends on MCP.
- The CLI, the MCP server and the UIs **never** speak HTTP directly — a guard-rail
  test (`tests/test_layering.py`) forbids `requests`/`httpx`/`urllib` outside the
  SDK.
- **The browser extension is the one exception, and it is not one.** It is
  JavaScript, so a Python SDK is not available to it; it depends on the
  *contract* and its stability policy instead (ADR 0016). The layering test
  scans `.py` only, so it would have waved this through in silence — which is
  why the decision is written down and `tests/test_browser_extension.py` checks
  what Python can check.
- Applications **do not import each other** and **do not import** the backend's
  Python code.
- A specialized UI may restrict/preconfigure the contract, never invent a
  parallel one; feasibility always comes from the resolver (`/capabilities`,
  ADR 0013).
- A package never depends on an application.

## Commands

The root `Makefile` drives everything from the root:

```bash
make install       # backend venv + SDK + CLI + MCP (uv, editable)
make validate      # format --check + lint + hermetic tests (the official gate)
make test-ui       # Streamlit AppTests for the 3 UIs (disposable venv, fake client)
make validate-all  # validate + test-ui + the external suite (-m external)
```

Docker: `docker compose up -d --build` (backend + HomeTube);
`--profile all` for Studio (:8502) and the Console (:8503). The web images embed
`packages/python-sdk` (repo-root context).

## Three documents proposed and deliberately not created

The monorepo migration prompt (2026-07-26) listed `docs/client-architecture.md`,
`docs/api-client-generation.md` and `docs/development.md` as documents to write.
None was created, and none should be — writing them now would mean three files
restating what already exists, which is the drift this pass is correcting.
Recorded here so the promise stops dangling in an archived prompt:

| Proposed | Where it actually lives |
| --- | --- |
| `client-architecture.md` | This document, plus [ADR 0015](architecture-decisions/0015-sdk-cli-mcp-layering.md) — the SDK is the one door to the API, and the CLI, MCP server and UIs all go through it. |
| `api-client-generation.md` | Obsolete. The SDK is hand-written and typed, not generated from the OpenAPI; ADR 0015 records why. |
| `development.md` | [docs/development/validation.md](development/validation.md) — the gate, the suites and the release checks. |
