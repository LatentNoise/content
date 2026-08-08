# HomeTube — the Streamlit frontend

A **client** of the Content API (`/api/v1`), not the backend. It takes up the
HomeTube use case (a URL as input) but with Content's versatile outputs (video,
audio, subtitles, thumbnail, metadata, transcript, summary): paste a URL →
analyze → choose what you want to extract → launch → follow the job live →
download the artifacts. No business logic here: the backend validates, plans and
executes; the UI is **capability-driven** (it renders what `/capabilities`
resolves) and speaks HTTP only through the SDK (`content_sdk`).

## Running locally (dev)

The backend must be running (see the repository root). Then:

```bash
cd apps/web-hometube
pip install "streamlit>=1.40" -e ../../packages/python-sdk
CONTENT_API_URL=http://localhost:8010 streamlit run app.py
```

- `CONTENT_API_URL` — the backend URL as seen by the frontend (HTTP requests).
- `CONTENT_PUBLIC_API_URL` — the backend URL as seen by the **browser** (the
  artifacts' download links). Defaults to `CONTENT_API_URL`. Under Docker they
  differ (an internal service name vs a host port).

## Running with Docker (multi-container)

From the repository root:

```bash
docker compose up --build
```

- the backend (`content`) on `http://localhost:8010`, HomeTube (`hometube`) on
  `http://localhost:8501`.
- The frontend reaches the backend through `http://content:8000` (the compose
  network); the download links point at `http://localhost:8010` (the browser).

## Language preferences

Audio tracks and subtitles are **ordered and pre-selected** according to the
server preferences (`CONTENT_LANGUAGE_PRIMARY`,
`CONTENT_LANGUAGES_SECONDARIES`, `CONTENT_VO_FIRST`,
`CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES`) intersected with what the
source actually offers — never "every track" by default.

## Cookies / authentication

Configure the credentials on the backend side (`CONTENT_CREDENTIALS`); the
frontend lists them (ids only) and offers a selector. The secret never travels
through the frontend or the request.
