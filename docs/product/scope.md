# Scope

The **accepted** state of the product scope. Revisable, but any change is an
explicit decision (PO/architect). The non-goals are as firm as the goals.

## In scope (today, executed)

- **Sources**: `url` (media through yt-dlp, web pages through the reader),
  `file` (media through ffmpeg, `.txt`/`.md`/`.pdf` through the document
  reader — a PDF's text layer, not a scan, which would need OCR — all under
  allowed roots), `text` (inline), `upload` (bytes a client sent, ADR 0020).
- **Outputs**: `video`, `audio`, `metadata`, `thumbnail`, `subtitles`,
  `transcript` (from existing subtitles), `summary` (local Ollama LLM, from
  subtitles, audio or extracted text), `document_text`, `markdown`.
- **Cardinality scope**: `single` and `each_item` (a collection fans out into
  one member step per entry, inside one job — ADR 0019).
- **Execution**: asynchronous jobs, a SQLite queue, an embedded worker, ordered
  events + SSE, cancellation, retry (a new, linked job), content-based reuse
  (`reuse_existing`), resumption of `running` jobs on restart.
- **Clients**: the REST API `/api/v1`, the official Streamlit applications (HomeTube, Studio, Admin), the `content` CLI, the `content-mcp` server, scripts. The backend has no UI of its own — `/` redirects to the Swagger docs.
- **Deployment**: a single container (API + worker), a non-root image.

## In scope (targeted, next milestones)

- **HomeTube "single URL" parity**: cookies/auth, quality/codec profiles with
  fallback, SponsorBlock, cut (trim), chapters/description/comments,
  multi-audio, merged/separate mode, `delivery` (folder + naming), a web UI
  dedicated to URLs.
- **Collections**: playlist **synchronization** — keeping a local folder in step
  with a playlist over time (plan/apply diff, rename detection, archiving or
  deleting removed entries, relocation). The `each_item` fan-out it was listed
  beside is **executed** (above); sync is the half still targeted, and the one
  standalone-HomeTube feature with no equivalent here.
- **Hardening**: observability, effective retention, intra-job resumption,
  finishing the contract's honesty.

## Considered later (hypotheses, not committed)

- `.docx`/`.epub`/`.odt`/`.rtf` text extraction — recognised and refused today,
  each needing its own reader. OCR for a scanned PDF, same reason. Connectors.
- `keyframes`, `ocr`, `pdf`, `embeddings`, `semantic_index`, `archive`,
  `collection` outputs.
- `each_source`, `all_sources`, `collection`, `group` scopes.
- A UI more versatile than the web one; a CLI; an SDK; MCP.
- A multi-user credential store; integrations (Jellyfin, notifications).

These items **exist in the contract's schema** (reserved types, scopes) but are
**rejected at feasibility time** (`*_not_supported`) until they are implemented —
the namespace is reserved, the behaviour is honest.

## Out of scope (accepted non-goals)

- **No** API authentication/multi-tenancy in V1 (single-user self-hosting usage;
  per-owner isolation is not a current goal).
- **No** distributed infrastructure (Redis, Celery, Kafka, RabbitMQ, K8s), no
  remote workers, no object storage — until a concrete need demands it.
- **No** plugin marketplace and no public plugin system.
- **No** billing, teams, or visual workflow builder.
- **No** video transcoding (re-encoding) in V1: only copy/remux and stream
  selection.
- **No** compatibility with HomeTube's contract/model: Content is a new domain,
  not a rename.

## Targeted / non-targeted users

- **Targeted**: technical self-hosting users, integrators through API/scripts.
- **Not targeted (for now)**: a non-technical general public; multi-user
  organizations with data partitioning.

## Targeted environments

- Linux/macOS, Docker (amd64/arm64), single-host. The SQLite database and the
  file storage **share the same volume** (an accepted constraint, see the
  invariants).

## Important external dependencies

- **yt-dlp**, **ffmpeg/ffprobe** (binaries), **Ollama** (a local HTTP service,
  optional: required only for `summary`). No cloud dependency.

## Accepted limits

- A single execution host (no horizontal scaling).
- SSRF blocking applies to the submitted URL, not to the redirects.
- Content-based reuse only covers the steps bound to an output.
