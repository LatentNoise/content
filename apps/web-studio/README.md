# Content Studio (`apps/web-studio`)

The general-purpose Streamlit front-end for Content: a thin, pure client of the
public API (`/api/v1`) that exposes the **whole contract** — several sources
(`url` / `file` / `text`), every output type (video, audio, subtitles,
thumbnail, metadata, transcript, summary) with their options, plus preferences
and constraints. The form is dynamic: outputs and their controls appear from
what you ask for.

It is the broad counterpart of [`../web-hometube`](../web-hometube) (HomeTube, the
specialized YouTube skin). Both speak the same `GenerationRequest` contract and
never hold business logic — the back-end validates, plans and executes.

## Run

Locally:

```bash
CONTENT_API_URL=http://localhost:8010 streamlit run app.py --server.port 8502
```

With Docker Compose (port 8502) — Studio starts whenever `studio` is in the
`COMPOSE_PROFILES` line of `.env` (it is, by default):

```bash
docker compose up -d --build
```

## Environment

- `CONTENT_API_URL` — back-end URL for the app's own requests
  (compose: `http://content:8000`).
- `CONTENT_PUBLIC_API_URL` — public back-end URL for browser download links.

## Uploading files from your device

Studio runs in a container with no volumes and speaks to the engine over HTTP,
so a path typed into the "On the server" field means a path **on the engine**.
For anything on your own machine, use "From this device": the bytes are sent to
the engine and become an `upload` source (ADR 0020). Several files at once
become several sources, which composes with `each_item` for free.

**The ceiling is lower here than in the API, on purpose.** Streamlit buffers an
upload in this app's memory before it reaches the engine, so a 2 GiB file — which
`POST /api/v1/uploads` accepts — would be held whole in a UI container.
`STREAMLIT_SERVER_MAXUPLOADSIZE` (200 MB in the image) bounds it, and the
picker's label states the current value rather than letting you discover it by
failing.

For genuinely large files, skip the browser: the SDK streams from disk
(`client.upload_file(path)`), and so does the CLI.
