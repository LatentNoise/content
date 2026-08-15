# Deployment

The accepted state of the deployment. Single-host, Docker Compose, four
services: the engine and the console always run, the download UIs are selected
by `COMPOSE_PROFILES` in `.env` (both by default).

Two equivalent compose files describe it: the repository root's
`docker-compose.yml` (with `build:` blocks, for clones) and
[`deploy/docker-compose.yml`](../../deploy/docker-compose.yml) — its
build-free twin for installs from the published images, no source tree
needed (the README's quick start). `tests/test_deploy_compose.py` keeps
them in lockstep.

## Container user and mounted state

The engine image starts as root, adopts the directories mounted into it, then
drops to the unprivileged `content` user (uid 1000) before running anything —
`docker compose exec content ps` shows the engine as `content`.

That startup step exists because Docker creates a missing bind-mount source
directory as `root:root`, which made a first `docker compose up -d` in an
empty folder fail with *"container content is unhealthy"* on Linux (v0.3.1).
`/data` is always adopted. The delivery library is adopted **only when it is
empty**: an existing library keeps its ownership, since rewriting the
ownership of an operator's media collection would be worse than the failure it
would avoid.

If delivery fails on a library owned by another user, the simple remedy is to
point `CONTENT_DELIVERY_DIR_HOST` at a directory you own.

Setting `user:` on the `content` service to match the library's uid/gid also
works, but **not on its own**: the startup step above only runs as root, so a
container started with an explicit `user:` never adopts `/data` either. On a
fresh install that leaves the engine unable to create its database — it exits
with `sqlite3.OperationalError: attempt to write a readonly database` and the
container never becomes healthy. If you take that route, pre-create the data
directory with the same ownership:

```bash
mkdir -p data && sudo chown 1026:1026 data     # the uid/gid you set in `user:`
```

Which is the manual step the adoption logic exists to spare you, so prefer the
first remedy unless the library's ownership is genuinely immovable.

## Topology

| Service | Role | Host port | Runs | Base |
| --- | --- | --- | --- | --- |
| `content` | The `/api/v1` API + the embedded worker (ADR 0007) | `${CONTENT_PORT:-8010}` → 8000 | always | `jauderho/yt-dlp` (yt-dlp + ffmpeg, + deno for the YouTube challenge) |
| `hometube` | HomeTube — the YouTube UI (a pure API client) | `${HOMETUBE_PORT:-8501}` → 8501 | `hometube` in `COMPOSE_PROFILES` | `python:3.12-slim` + `content-sdk` |
| `studio` | Content Studio — the general UI | `${STUDIO_PORT:-8502}` → 8501 | `studio` in `COMPOSE_PROFILES` | same |
| `console` | Content Console — the operations console | `${CONSOLE_PORT:-8503}` → 8501 | always | same |

The UIs reach the backend over the compose network (`http://content:8000`,
`CONTENT_API_URL`) through the SDK (`content_sdk`); the artifacts' **download
links** point at the host port
(`CONTENT_PUBLIC_API_URL=http://localhost:${CONTENT_PORT}`) because it is the
browser that follows them. Each UI waits for `content` to be *healthy*.

```bash
docker compose up -d --build              # everything in COMPOSE_PROFILES (.env):
                                          # engine + console + both UIs by default
docker compose up -d --build content      # the backend alone
```

### Image sizes, and why they are what they are

| Image | Size | What dominates it |
| --- | --- | --- |
| `content` (engine) | ~640 MB | the `jauderho/yt-dlp` base (yt-dlp, ffmpeg, deno and their Python deps: `curl_cffi` 36 MB, Pillow 20 MB, `Cryptodome` 7 MB) plus Typst at 46 MB. Content's own additions — FastAPI, uvicorn, pydantic, ReportLab — are about 12 MB. |
| the three UIs | ~700 MB each | Streamlit's dependency tree: **pyarrow 140 MB, pandas 75 MB, numpy 66 MB, pydeck 23 MB**. The app itself is one file. |

The counter-intuitive part is real: a thin Streamlit client is *bigger* than the
engine that carries yt-dlp, ffmpeg and a typesetter.

What is deliberately not done: pyarrow, pandas and numpy are **hard
dependencies of Streamlit**, not choices. HomeTube and Studio never touch a
dataframe and could in principle run without them, but Content Console does
(`st.dataframe`), Streamlit imports pandas on paths beyond the dataframe API,
and uninstalling a framework's own dependencies to save space is the kind of
cleverness that fails at runtime rather than at build time. The ~215 MB stays.

What is done, because it is free: the UI images install with `--no-compile` and
set `PYTHONDONTWRITEBYTECODE`, which keeps ~98 MB of `.pyc` out of the layers we
own (the interpreter compiles on first import instead — once, in a server that
runs for days). The backend image already did this. Note that deleting files
that come from a *base* layer — pip, for instance — reclaims nothing, because
the base layer still carries them.

### Prebuilt images, or a local build

Every version tag publishes four multi-arch images (amd64 + arm64) to GHCR:

```text
ghcr.io/latentnoise/content            the engine
ghcr.io/latentnoise/content-hometube   HomeTube
ghcr.io/latentnoise/content-studio     Content Studio
ghcr.io/latentnoise/content-console    Content Console
```

Compose names them, so nothing has to be built:

```bash
docker compose pull        # fetch the published images
docker compose up -d       # start, no build step
```

Tags follow the release: `0.1.0`, `0.1`, `0` and `latest` (a pre-release gets
its exact tag only). Pin one with `CONTENT_VERSION=0.1.0` in `.env`. Keeping
`--build` instead is always valid — the `build:` sections stay, so a clone
builds from source without touching the registry.

The same command is also the update path: after changing code or pulling a new
version, it rebuilds images from the tree and recreates only the containers
whose image changed (`make docker-update` wraps it, with orphan cleanup, and
prints what is running). Data in `./data` is a bind mount and survives.

Which download UIs start is **one line in `.env`**, no compose flags to learn:
`COMPOSE_PROFILES=hometube,studio` (the `.env.example` default),
`COMPOSE_PROFILES=hometube`, or `COMPOSE_PROFILES=studio`. The engine and the
console are not listed there because they always run.

## Configuration (a root `.env`, not versioned)

| Variable | Default | Role |
| --- | --- | --- |
| **🔌 Services & ports** | | |
| `COMPOSE_PROFILES` | `hometube,studio` | Which download UIs start (`hometube`, `studio`, or both); the engine and the console always run |
| `CONTENT_PORT` / `HOMETUBE_PORT` / `STUDIO_PORT` / `CONSOLE_PORT` | 8010 / 8501 / 8502 / 8503 | Host ports |
| `CONTENT_MAX_CONCURRENT_JOBS` | 2 | Worker concurrency |
| `CONTENT_COLLECTION_MEMBER_CONCURRENCY` | 2 | Playlist members downloading at once **within one job** — a politeness bound toward the provider, not a throughput dial; 1 = strictly sequential. It multiplies with `CONTENT_MAX_CONCURRENT_JOBS`: the defaults allow up to 4 simultaneous downloads from one host |
| `CONTENT_STEP_TIMEOUT_SECONDS` | 3600 | Per-step timeout |
| **🌐 Network & access** | | |
| `CONTENT_ALLOW_PRIVATE_NETWORKS` | false | SSRF guard (URLs pointing at private IPs) |
| `CONTENT_CORS_ORIGINS` | — | Allowed browser origins (CORS). Empty = no CORS header; curl/SDK/scripts are **never** affected |
| `CONTENT_CREDENTIALS` | — | `id=path` (cookies), see below |
| **📦 Storage & delivery** | | |
| `CONTENT_DELIVERY_DIR` | `<data>/delivery` | The root of the delivery media library (see below) |
| `CONTENT_DELIVERY_DEFAULT` | `false` (compose: `true`) | Deliver **every** artifact into the library by default (ADR 0018); a request can refuse with `delivery.mode: "none"` |
| `CONTENT_TMP_ROOT` | `<data>/tmp` | The root of disposable files ([storage.md](../storage.md)) |
| `CONTENT_CACHE_ROOT` | `<data>/cache` | The cache root (reserved) |
| `CONTENT_CACHE_ENABLED` | `false` (compose: `true`) | URL JSON cache + video reuse ([storage.md](../storage.md), ADR 0010) |
| `CONTENT_ANALYSIS_TTL_HOURS` | `72` | How long a URL analysis is cached (3 days) |
| **🗣️ Language preferences** | | |
| `CONTENT_LANGUAGE_PRIMARY` | — | The language this installation speaks. Ordered first (after the original voice) and pre-selected in the UIs, for **audio and subtitles** |
| `CONTENT_LANGUAGES_SECONDARIES` | — | Secondary languages `en,es` — offered and pre-selected after the primary (audio + subtitles) |
| `CONTENT_VO_FIRST` | `true` | Original voice ahead of the primary language in the track order |
| `CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES` | `true` (`.env.example`: `false`) | Whether the primary language is pre-checked among subtitles too. `false` excludes **only** the primary — the secondaries keep pre-filling, because someone fluent in `fr` does not need `fr` subtitles but wants the `en` ones |

A worked example of the four together — what a given source ends up showing —
is in [HomeTube's README](../../apps/web-hometube/README.md#language-preferences-by-example).
| **🧠 Providers & LLM runners** | | |
| `CONTENT_YTDLP_EXTRA_ARGS` | — | The operator's yt-dlp args (trusted), added to every call |
| `CONTENT_OLLAMA_URL` / `_MODEL` | host / auto | The **local** LLM for `summary`, `translation` and derived `chapters`. Empty model = the first installed one, resolved deterministically and recorded in provenance |
| `CONTENT_WHISPER_MODEL` | `small` | The speech-to-text model (requires the `[stt]` extra / an image built with `INSTALL_STT=true`) — activates transcript/summary **from audio** |
| `CONTENT_ANTHROPIC_API_KEY` / `_MODEL` | — / `claude-sonnet-5` | The Anthropic **cloud** LLM for `summary`, `translation` and `chapters` (a key = active; excluded if `privacy.allow_cloud_providers:false`) |
| `CONTENT_OPENAI_API_KEY` / `_MODEL` | — / `gpt-4o-mini` | The OpenAI **cloud** LLM, same three operations |
| **📕 PDF rendering** | | |
| `CONTENT_PDF_RENDERER` | `auto` | Which PDF implementation runs: `auto` (Typst when healthy, else ReportLab), `typst`, or `reportlab`. A pinned renderer that is unusable fails the request rather than downgrading silently |
| `CONTENT_TYPST_BINARY` | `typst` | The Typst executable |
| `CONTENT_PDF_TEMPLATE` | `default` | Server-side template **name** (never a path or template source) |
| `CONTENT_PDF_MISSING_GLYPHS` | `replace` | What to do about characters no available font can draw: `replace` (substitute a visible placeholder), `error` (refuse the step), `warn` (render unchanged). Never silent — see [pdf-rendering.md](pdf-rendering.md) |
| `CONTENT_PDF_FONT` | *(empty)* | A TrueType font file or directory for `pdf` output, consulted when the text needs characters the built-in faces cannot draw. The image ships DejaVu (Latin, Greek, Cyrillic — **not** CJK). Characters no available font covers are handled per `CONTENT_PDF_MISSING_GLYPHS` |
| **⚖️ Licence & notifications** | | |
| `CONTENT_SOURCE_URL` | upstream repo | Corresponding Source offered to this deployment's users (AGPL §13) |
| `CONTENT_RELEASE_CHECK_URL` | — | Release API polled for a newer version (empty = the banner is off) |
| `CONTENT_RELEASE_PAGE_URL` | — | Page the release notification links to |
| `CONTENT_RELEASE_CHECK_TTL_HOURS` | `6` | How long a release lookup is cached |
| `CONTENT_YTDLP_MAX_AGE_DAYS` | `0` (off) | Opt-in: age at which yt-dlp is flagged stale in the UIs |
| `CONTENT_UI_STATE_DIR` | *(temp dir)* | Where a UI remembers dismissed notifications |

## Delivery (the destination folder)

The delivery library is **where finished files end up for the user**. In the
compose deployment (`CONTENT_DELIVERY_DEFAULT=true`, ADR 0018) every artifact
is copied there automatically, under the name the engine computed from the
video/page itself (`display_filename`, ADR 0017) — no request field needed. A
request can refine (`delivery.folder`, `delivery.filename` as the family base
name) or refuse (`delivery.mode: "none"`); each artifact records where its
copy landed (`delivered_path`, relative to the root). With the policy off (the
bare-engine default), only explicit `delivery` intent is honored.

The host mount is **configurable**: `CONTENT_DELIVERY_DIR_HOST` (default
`./playground/output`, formerly HomeTube's `VIDEOS_FOLDER_DOCKER_HOST`) →
`/output` inside the container. Point it at your media library (NAS, homelab):
`GET /api/v1/folders` lists its existing subdirectories and the UIs offer them as
destinations.

## Direct API access

The REST API is **open by design** (no authentication in V1): any HTTP client —
curl, the SDK, a script, another service — always reaches the backend directly,
with no mandatory detour through the SDK or a UI. The existing guards never
block access itself: SSRF (`ALLOW_PRIVATE_NETWORKS`) only concerns *source*
URLs, `ALLOWED_INPUT_ROOTS` only `file` sources. The one special case: a
**JavaScript client in a browser** on another origin needs CORS — opt-in through
`CONTENT_CORS_ORIGINS` (disabled by default, which also blocks drive-by requests
from arbitrary sites). To expose the instance outside the local network, put an
authenticating reverse proxy in front (the API provides no auth of its own).

## Cookies / authentication

The repository ships a **`config/` folder mounted read-only at `/config`** in
the backend container — the one place for external runtime files (cookies
today; another site's cookies or a custom PDF font tomorrow). Nothing in it is
ever committed or baked into an image. Full walkthrough:
[`config/README.md`](../../config/README.md).

The `youtube` credential is **declared by default** (`CONTENT_CREDENTIALS` in
`.env.example` and the compose fallback): cookies are all but required for
reliable YouTube in Docker, so instead of hiding the feature, the UIs flag
the missing file — with instructions — until it exists. The whole recipe:

1. Export a Netscape `cookies.txt` from a signed-in browser and save it as
   `config/youtube_cookies.txt` (the engine reads it at `/config/…`, the
   container side of the mount).
2. `make docker-update`.

HomeTube's "🍪 Cookie Management" then shows "✅ ready" with the file's path
and last-refresh time; the Console's credentials card does the same. A
declared-but-missing file shows a "file not found" state instead of failing
silently at job time. `GET /api/v1/config` reports each credential's id,
path, presence and last-modified time — never the file's contents. Several
credentials: comma-separated `id=path` pairs; set the variable **empty** to
turn the feature off (the compose default only applies when it is unset).

The backend **copies** the cookie file to a writable location before passing it
to yt-dlp: yt-dlp rewrites the cookie jar on exit, so a `:ro` mount would cause
an error. The mount therefore stays read-only and your export is never
modified.

## yt-dlp freshness (important)

YouTube quickly breaks old versions of yt-dlp (the "n" challenge, signature
updates). The `content` image **auto-updates yt-dlp at build time** (`yt-dlp -U`),
and the base provides a JS runtime (deno) to solve the challenge. **Rebuild
regularly** (`docker compose build content`) to keep up with YouTube's changes. A
stale yt-dlp shows up as `analysis_failed` / `No video formats found`.

## Speech-to-text (optional)

The Whisper runner (`faster-whisper`) implements `audio.transcribe`: once
installed, it **automatically** activates the `transcript.from_audio` /
`summary.from_audio` variants (sources without subtitles, podcasts) — the catalog
does not change, only the inventory of implementations does (ADR 0013 R2/R7).
Opt-in, because the wheels are heavy: `CONTENT_INSTALL_STT=true` in `.env` then
`docker compose build content` (or `pip install -e ".[stt]"` locally). The model
is set through `CONTENT_WHISPER_MODEL` (default `small`).

## Licence & source visibility (AGPL §13)

Content is AGPL-3.0-or-later. Section 13 adds one obligation beyond the usual
copyleft: if you **modify** Content and let users interact with your modified
version **over a network**, those users must be able to obtain its Corresponding
Source.

Running Content unmodified — at home or inside a company — triggers nothing.

To make that practical, the instance publishes its own source link:

- `GET /api/v1/system` returns `license` and `source_url`;
- each UI shows `AGPL-3.0-or-later · Source code` in its sidebar, using whatever
  the backend reports.

**If you deploy a modified Content, set `CONTENT_SOURCE_URL` to your own
repository.** It defaults to upstream, which is correct only for an unmodified
deployment — leaving it unchanged on a fork would point your users at source
that is not the software they are using, which does not discharge your
obligation and misinforms them. The UIs never hard-code the link precisely so
this stays under the operator's control.

## Notifications

The UIs show a dismissible banner at the top, driven by
`GET /api/v1/notifications`. The backend decides what is worth saying — it is
the only side that knows its own version and the version of the tools it runs —
and the UIs only render it.

There is one exception to "the backend decides", because it is the one fact
only a client can observe:

- **UI and backend versions differ.** At launch (once per browser session) each
  UI compares its own version against the backend's (`GET /api/v1/system`). A
  difference means a torn deployment — one image was updated and the other was
  not — and the banner says which side is behind and how to fix it
  (`docker compose pull`, then `up -d`). No variable, no outbound call; silent
  when the backend does not report a version.

The two things the backend can say:

- **A newer release is available.** Opt-in: set `CONTENT_RELEASE_CHECK_URL` to a
  release API (GitHub's `/releases/latest` and Forgejo's
  `/api/v1/repos/{owner}/{repo}/releases/latest` both answer the `tag_name`
  shape this expects) and `CONTENT_RELEASE_PAGE_URL` to the page the banner
  links to. Empty URL = no outbound call at all. Only **major/minor** releases
  notify; a patch bump stays silent so the banner keeps its meaning. The lookup
  is cached for `CONTENT_RELEASE_CHECK_TTL_HOURS`, so rendering a page never
  turns into an HTTP call.
- **yt-dlp is out of date.** Opt-in: set `CONTENT_YTDLP_MAX_AGE_DAYS` to a
  number of days (0, the default, keeps it silent). YouTube breaks old builds
  quickly and the symptom is an opaque `analysis_failed` / "No video formats
  found" — but age alone cannot tell "stale" from "newest available": the
  image pins the latest upstream release, which may itself be weeks old, so a
  default-on check would greet every fresh install with an unactionable
  warning. Upstream freshness is tracked on the maintainer's side (a weekly
  check files an issue — see
  [ytdlp-base-image.md](ytdlp-base-image.md)); users hear about it through
  Content releases. Enable the age check if you rebuild rarely and want the
  local reminder (see *yt-dlp freshness*).

The check is **failure-silent by contract**: an unreachable, slow, rate-limited
or malformed release endpoint produces no notification and no error — the page
renders exactly as it would without the feature.

Dismissals are remembered per installation in `CONTENT_UI_STATE_DIR` (a
directory under the system temp root by default) and are keyed by the target
version, so dismissing one release does not silence the next.

## Health & data

- Healthchecks: `content` on `/api/v1/health`, each Streamlit UI on
  `/_stcore/health`.
- **What `/api/v1/health` actually proves.** It pings the database and checks
  that the data directory is writable, answering `200 {"status":"ok","checks":…}`
  or `503 {"status":"degraded", …}` naming what failed. It stays green when
  ffmpeg, yt-dlp, Typst or an LLM are missing: those decide which *capabilities*
  resolve, which `/api/v1/capabilities` reports per source. "Not installed" and
  "broken" are different answers, and only the second should take a container
  out of rotation.
- **The UI healthchecks are liveness only.** A UI whose `CONTENT_API_URL` is
  unreachable still reports healthy and renders "Back-end unreachable at …",
  which is the most useful thing it can do; restarting it would not fix the
  engine. Compose already gates their startup on `content` being healthy
  (`depends_on: condition: service_healthy`). See D-47.
- Data persisted in `./data` (SQLite + artifacts + analysis cache). The database
  and the file storage **share this volume**, because the plan lives as a file
  snapshot beside the row that references it — separating them would let the two
  drift apart.
