# ADR 0016 — The browser extension is the first non-Python client

Status: accepted (2026-08-02) · Supersedes nothing · Refines ADR 0015

## Context

ADR 0015 made `content_sdk` the single door to `/api/v1`: the CLI, the MCP
server and the three Streamlit UIs all speak through it, nothing else imports an
HTTP client, and `tests/test_layering.py` enforces it.

The browser extension (`apps/browser-extension/`) cannot obey that rule. It is
JavaScript running in a browser; there is no Python there to import a Python
SDK. Worse, the guard would not notice: it scans `.py` files only, so an
extension speaking HTTP would pass silently rather than fail — the rule would be
bypassed without anyone deciding to bypass it.

Two further constraints shaped the answer:

- **CORS.** The engine sends no CORS headers by default (`CONTENT_CORS_ORIGINS`
  empty — "curl/SDK never need it"). Measured against a running instance: a POST
  carrying an `Origin` header succeeds but comes back with no
  `Access-Control-Allow-Origin`, and a preflight `OPTIONS` answers **405**,
  because without the CORS middleware there is no handler for it. A page-context
  `fetch` is therefore blocked outright.
- **Manifest V3.** A service worker holding `host_permissions` for the target
  origin is exempt from CORS; a content script (Chrome ≥ 85) is not.

## Decision

**The extension speaks `/api/v1` directly, and the contract — not the SDK — is
the boundary it depends on.**

1. The SDK rule of ADR 0015 is a rule about **Python consumers**. It exists so
   that transport, retries and error shapes are written once, not so that HTTP
   is forbidden. A client in another language re-implements the transport
   because it must, and depends instead on the published contract and its
   stability policy (`docs/contract.md` §9).
2. **Every network call lives in the service worker** (`background/api.js` is
   the only module that calls `fetch`). This is not a preference: it is the only
   arrangement that works against a stock engine, and it means the extension
   ships without asking an operator to widen `CONTENT_CORS_ORIGINS` — a setting
   whose whole point is that browsers, not tools, need it.
3. **The contract is verified from Python** (`tests/test_browser_extension.py`):
   the request bodies the extension emits are validated against the real
   `GenerationRequest`, the manifest is checked for permission minimality, and
   the builder is checked for fields the contract does not have. `make validate`
   therefore fails on contract drift, with no JavaScript toolchain in the repo.
4. **The extension keeps no catalogue of its own.** What it offers comes from
   `POST /api/v1/capabilities` for the current source. Studio kept a private
   capability→output map and silently stopped offering three capabilities for
   three waves (D-37); an extension is a worse place for that, because nobody
   is looking at it.

## Consequences

- The layering guard stays Python-only and stays honest: it says what it checks.
  A second non-Python client would follow this ADR, not extend that test.
- `/api/v1` now has a consumer that cannot be refactored alongside it. This is
  the first time the stability policy written in prompt 18 has a real
  dependant — which is the point of having written it.
- If `host_permissions` ever stops exempting service-worker requests, the
  fallback is `CONTENT_CORS_ORIGINS` plus the CORS middleware handling
  preflight. Recorded here so the fallback is known rather than rediscovered.
- No bundler, no npm, no build step: the extension's source is what the browser
  loads. Adding a dependency would mean revisiting this ADR, because it would
  put a toolchain between the repository and the artifact.

## Alternatives rejected

- **Compile the SDK to JavaScript.** There is no Python-to-JS path worth taking
  for a client this thin, and it would couple the extension's release to the
  SDK's.
- **A local bridge process** (the extension talks to a helper that uses the
  SDK). Two moving parts and an install step, to avoid writing about 60 lines of
  `fetch`.
- **Enable CORS and call the API from the page.** It would make the API
  reachable from *any* page the user visits, which is a real widening of
  exposure for no gain over the service worker.
