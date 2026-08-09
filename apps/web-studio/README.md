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
