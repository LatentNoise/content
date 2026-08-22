<!-- markdownlint-disable MD013 MD026 MD033 MD036 MD041 -->
<div align="center">

# Content

## Turn URLs, files, and text into media, knowledge, and documents.

**One self-hosted engine. HomeTube for YouTube, Content Studio for everything else — plus a browser extension, an MCP server, a CLI, a typed SDK, and the REST API underneath them all.**

[![Latest release](https://img.shields.io/github/v/release/LatentNoise/content?sort=semver&display_name=tag)](https://github.com/LatentNoise/content/releases/latest)
[![CI](https://github.com/LatentNoise/content/actions/workflows/ci.yml/badge.svg)](https://github.com/LatentNoise/content/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%C2%B7%20arm64-2496ED.svg?logo=docker&logoColor=white)](docs/operations/deployment.md)
[![License](https://img.shields.io/badge/License-AGPL--3.0--or--later-green.svg)](LICENSE)

[Quick start](#quick-start) ·
[HomeTube](#hometube--youtube-focused) ·
[Coming from HomeTube?](#coming-from-standalone-hometube) ·
[All the clients](#the-clients) ·
[Connect an agent](#mcp-server--for-agents-and-ides) ·
[Read the docs](docs/README.md)

</div>

Content is a self-hosted engine that turns supported sources into media,
transcripts, summaries, translations, images, metadata, Markdown, PDF, and
more. The **backend** analyzes each source, determines what it can actually
produce, plans and runs the job, records its history, and delivers the results.

Everything else is a **client of one public contract**: **HomeTube** for the
focused YouTube experience, **Content Studio** for general workflows, a
**Chromium extension** for the tab you are already on, the **`content-mcp`**
server for agents, a **CLI** for terminals and cron, a **typed Python SDK** for
applications, the **REST API** for every other language, and **Content
Console** to watch the engine work. Pick one or several — none of them is a
layer the others have to pass through, and none of them holds business logic of
its own.

**HomeTube** is the quickest way to see what that means: paste a YouTube URL,
pick what you want out of it, and watch the files land in your library.

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-hometube-demo.gif"
       alt="HomeTube demo — paste a YouTube URL, choose the outputs, watch the job deliver into the library"
       width="80%">
  <br>
  <sub><b>HomeTube in Content</b> — paste a YouTube URL, choose what you want,
  follow the job into your library. <a href="#hometube--youtube-focused">More about HomeTube ↓</a></sub>
</div>

<!-- Visuals are produced in media/ (untracked) and attached to releases —
see media/README.md. -->

> [!NOTE]
> **A proven workflow, now growing into a platform.** The standalone
> [HomeTube](https://github.com/EgalitarianMonkey/hometube) container has passed
> **300,000 package downloads on GitHub Container Registry**. It keeps running
> and stays maintained — nothing breaks, and there is no deadline. Content is
> where that work continues, and where its users are invited to move at their
> own pace.
>
> The two are separate projects: standalone HomeTube has not been retrofitted to
> run on Content. The **HomeTube app in this repository is a new UI on the
> Content engine**, carrying the same workflow forward.
> [Coming from standalone HomeTube? ↓](#coming-from-standalone-hometube)

## Quick start

Docker Compose is the only runtime prerequisite. Install from the published
images — nothing to clone or build:

```bash
mkdir content && cd content
curl -fsSLO https://raw.githubusercontent.com/LatentNoise/content/main/deploy/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/LatentNoise/content/main/.env.example
docker compose up -d
```

The default `.env` starts the complete local stack:

| Service | Default URL | Purpose |
| --- | --- | --- |
| **Content Studio** | <http://localhost:8502> | General requests across URL, file, upload, and inline-text sources |
| **HomeTube** | <http://localhost:8501> | Focused YouTube video and playlist workflow |
| **Content Console** | <http://localhost:8503> | Health, configuration, storage, jobs, events, and logs |
| **Content API** | <http://localhost:8010/docs> | REST API, OpenAPI, and embedded worker |

Open **Content Studio** for the general workflow, or **HomeTube** if your
source is YouTube. You can also skip both web apps entirely and drive the same
engine from the [MCP server, CLI, SDK, extension, or REST API](#the-clients).

Everything stays in the installation folder: `./data` holds the database, jobs,
and artifacts; finished files are delivered to `./playground/output`. Set
`CONTENT_DELIVERY_DIR_HOST` in `.env` to point at your own NAS or media library
instead — its sub-folders then become the destination choices offered in the
clients.

Update later with:

```bash
docker compose pull && docker compose up -d
```

<details>
<summary><b>Build from source instead</b> — for development or to include the
optional speech-to-text runner</summary>

```bash
git clone https://github.com/LatentNoise/content.git
cd content
cp .env.example .env
docker compose up -d --build
```

The repository's `docker-compose.yml` adds local `build:` definitions beside
the same images. [`deploy/docker-compose.yml`](deploy/docker-compose.yml) is
the build-free deployment file used above; a test keeps the two aligned.

</details>

## One source, many artifacts

The same engine serves media and document workflows:

```text
                             ┌─ My Conference.mp4
                             ├─ My Conference - audio.opus
YouTube URL ──→ Content ─────┼─ My Conference - subtitles - en.srt
                             ├─ My Conference - subtitles - fr.srt
                             ├─ My Conference - transcript.txt
                             ├─ My Conference - summary.md
                             └─ My Conference - summary.pdf

Web page / text / .md file ──→ Content ──┬─ Article.txt
                                         ├─ Article - summary.md
                                         ├─ Article - translation.md
                                         └─ Article.pdf
```

Each branch starts with a declarative request. You describe the results;
Content resolves what is possible, plans the work, runs the available tools,
and records where every artifact came from. No yt-dlp flags, ffmpeg pipelines,
transcription glue, or LLM orchestration in the client.

## The clients

One engine, one contract, several independent front doors. Every client below
speaks the same `GenerationRequest`, sees the same resolved capabilities, and
produces the same artifacts — they differ in ergonomics, not in what they can
ask for. HomeTube is entirely optional; so is every other row.

| Client | Best for | Start here |
| --- | --- | --- |
| **[HomeTube](#hometube--youtube-focused)** | The simplest YouTube video or playlist → library experience | Included in Docker Compose at `:8501` |
| **[Content Studio](#content-studio--the-whole-contract-in-a-form)** | General capability-driven requests from URLs, uploads, server files, and inline text | Included in Docker Compose at `:8502` |
| **[Chromium extension](#chromium-extension--the-tab-you-are-already-on)** | Sending the current Chrome, Brave, Edge, or Vivaldi tab to Content | Download the release zip, unzip, *Load unpacked* |
| **[MCP server](#mcp-server--for-agents-and-ides)** | Giving Claude, an IDE, or another MCP agent real artifacts instead of talk | `uv tool install content-mcp` |
| **[CLI](#cli--terminals-scripts-and-cron)** | Terminals, scripts, cron jobs, and raw request files | `uv tool install content-cli` |
| **[Python SDK](#python-sdk--typed-sync-and-async)** | Typed sync and async application integration | `pip install content-sdk` |
| **[REST API](#rest-api--every-other-language)** | Any language or integration that speaks HTTP | `/api/v1`, Swagger at `:8010/docs` |
| **[Content Console](#content-console--observe-and-pilot-the-engine)** | Observing and operating the engine | Included in Docker Compose at `:8503` |

### HomeTube — YouTube, focused

<http://localhost:8501> · [README](apps/web-hometube/README.md)

Paste a URL, choose the media and related artifacts, and follow the job into
your library — the workflow demonstrated at the top of this page.

With HomeTube in Content, you can:

- download a video or playlist as video or audio, with quality, codec,
  container, audio-language, and subtitle choices;
- remove or mark sponsored segments with SponsorBlock, cut clips, use
  server-side cookie credentials, and embed subtitles, chapters, thumbnails,
  and metadata;
- give every artifact a readable name and deliver it into a filesystem library
  watched by Plex, Jellyfin, or Emby;
- ask the same source for a transcript, summary, thumbnail, or metadata — not
  just the downloaded media — when the required runners are available.

HomeTube is deliberately focused: it does not accept file, upload, or
inline-text sources, and it does not expose the broader document workflow or
PDF output. For those, use Content Studio, the API, CLI, or SDK. **HomeTube is
a Content client, not a layer every Content user must pass through.**

**It has no settings of its own.** It reads the engine's configuration, so what
changes HomeTube lives in the `.env` beside your `docker-compose.yml`: the
delivery library whose sub-folders become the destination choices
(`CONTENT_DELIVERY_DIR_HOST`), the languages pre-selected for audio and
subtitles (`CONTENT_LANGUAGE_PRIMARY`, `CONTENT_LANGUAGES_SECONDARIES`,
`CONTENT_VO_FIRST`, `CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES`), and the
server-side cookie file that unlocks age-restricted or members-only videos
(`CONTENT_CREDENTIALS` — HomeTube only ever shows its id; the file never leaves
the server). Nothing is ever pre-selected that the source does not offer, and
no default is final.

[HomeTube's README](apps/web-hometube/README.md#configuration) documents each
variable and works the language rules through a concrete example — a Japanese
talk, a French speaker, and exactly what ends up pre-checked. The
[full variable table](docs/operations/deployment.md#configuration-a-root-env-not-versioned)
lists everything the engine accepts.

### Coming from standalone HomeTube?

[HomeTube](https://github.com/EgalitarianMonkey/hometube) — the standalone
Streamlit app — **keeps running and stays maintained**. Nothing breaks, and there
is no deadline. Content is where the work continues, so this is a move you make
when you are ready, not one you are forced into.

**What comes across.** The workflow you know: paste a URL, choose quality, codec,
container, audio languages and subtitles; remove or mark sponsored segments with
SponsorBlock; cut clips; unlock restricted videos with a server-side cookie file;
embed subtitles, chapters, thumbnails and metadata; and deliver readable file
names into a library watched by Plex, Jellyfin, or Emby. Playlists come across
too — each entry is analyzed and planned on its own, named per item, and
delivered as its own artifact.

**What is new.** The same source can also produce a transcript, a summary, a
translation, generated thumbnails, metadata, Markdown, or PDF. And the engine is
no longer reachable only from a web form: Studio, the Chromium extension, an MCP
agent, the CLI, the SDK, and plain HTTP all ask for the same things.

**What has not made the trip yet.** HomeTube's playlist **synchronization** — the
plan/apply diff that keeps a local folder in step with a playlist over time
(rename detection, archiving or deleting removed videos, relocation after a
naming or location change) — does not exist here. Content downloads a playlist;
it does not yet re-sync one. It is [roadmapped as part of
M2](docs/roadmap/roadmap.md). If playlist sync is your main use, keep standalone
HomeTube for that job.

**One practical note.** Both HomeTube apps default to port **8501**. Change one of
them in your `.env` if you want to run the two side by side.

### Content Studio — the whole contract, in a form

<http://localhost:8502> · [README](apps/web-studio/README.md)

The general-purpose web app: several sources in one request, every output type
the engine can resolve, with their options, preferences, and constraints. The
form is **capability-driven** — it renders what `/capabilities` answers for
your source, so a capability the engine gains appears without a UI release, and
what cannot be produced is shown with the server's reason instead of silently
disappearing.

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-studio.png"
       alt="Content Studio — the general-purpose request builder" width="80%">
  <br>
  <sub><b>Content Studio</b> — build general URL, file, upload and text requests</sub>
</div>

Studio takes a file two ways, and the distinction matters when the engine runs
on another machine:

- **From this device** — the bytes are sent to the engine and become an
  `upload` source ([ADR 0020](docs/architecture-decisions/0020-client-uploaded-sources.md)).
  Several files at once become several sources. Streamlit buffers the upload in
  the UI container, so the ceiling here is deliberately lower than the API's
  (200 MB by default, and the picker states the current value rather than
  letting you discover it by failing).
- **On the server** — a path the *engine* can already read, under a configured
  allowed input root. With this Compose setup that root is `./playground/input`.

For genuinely large files, skip the browser: the SDK streams from disk with
`client.upload_file(path)`.

### Chromium extension — the tab you are already on

[README](apps/browser-extension-chromium/README.md) ·
[latest release](https://github.com/LatentNoise/content/releases/latest)

Send the page you are watching to your engine without opening a UI. Manifest
V3, Chromium only (Chrome, Brave, Edge, Vivaldi, Opera, Arc…), no build step —
what the browser loads is exactly the files in the zip:

1. download `content-browser-extension-chromium-v<version>.zip` from the latest
   release and **unzip it** (Chromium loads a folder, not a zip);
2. open `chrome://extensions`, turn on **Developer mode**, **Load unpacked**,
   select the folder;
3. open a video and click the extension.

It normalizes the tab's URL (`youtu.be`, Shorts, and embeds become the
canonical watch URL; a `list=` on a watch URL means *that video*, not the
playlist), asks the engine what the source can produce and offers only that,
prefills the name with the naming engine's own proposal, offers the library's
existing folders plus *new folder…*, then follows the job and shows where each
file landed. Every network call happens in the service worker — the engine
sends no CORS headers by default, and that is what lets the extension work
against a stock instance with nothing to configure
([ADR 0016](docs/architecture-decisions/0016-first-non-python-client.md)).

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.2.0/2026-08-10-browser_extension.jpg"
       alt="HomeTube for Content — the Chromium extension popup on a video page" width="380">
  <br>
  <sub><b>Browser extension</b> — send the current tab to Content, from the tab itself</sub>
</div>

The popup's footer always names the engine it is talking to, so "where is this
sending my video?" is answered on screen. Its README also carries an honest
path-by-path **verification table** — what has actually been driven in a
browser, and what has not.

### MCP server — for agents and IDEs

[README](apps/mcp/README.md) ·
[`content-mcp` on PyPI](https://pypi.org/project/content-mcp/)

Content gives an MCP-compatible agent a controlled way to turn URLs into real
artifacts, not just talk about them. The official **`content-mcp`** server
exposes intention-level tools — not one per endpoint — to:

- analyze a URL and discover what it can produce;
- request one or several outputs and choose a library destination;
- monitor the job, cancel it, and report actionable failures;
- find the artifacts and their delivered paths;
- read small text artifacts inline while keeping large media out of the model
  context.

The agent never needs shell access, yt-dlp syntax, or backend internals.

**Nothing to install.** `uvx` fetches the server on first use and caches it, so
the client owns the whole lifecycle (updating stays explicit — `uvx --refresh`,
or pin `content-mcp@x.y.z`):

```bash
# Claude Code
claude mcp add content \
  --env CONTENT_API_URL=http://localhost:8010 \
  -- uvx content-mcp
```

For Claude Desktop, Cursor, and other MCP clients using the standard JSON
shape:

```json
{
  "mcpServers": {
    "content": {
      "command": "uvx",
      "args": ["content-mcp"],
      "env": { "CONTENT_API_URL": "http://localhost:8010" }
    }
  }
}
```

Prefer a pinned executable on your PATH — for an offline machine, or to control
when the version changes? Install it and name it directly
(`"command": "content-mcp"`, or `-- content-mcp` for Claude Code):

```bash
uv tool install content-mcp      # or: pipx install content-mcp
```

Then ask naturally:

> Analyze this lecture, save its audio into `Talks`, create a transcript and a
> structured summary if the available runners allow it, and tell me where
> every artifact landed.

The normal tool journey is
`get_config → analyze_source → generate → get_job → get_artifact`. Downloads to
the agent's own machine are bounded by a single variable
(`CONTENT_MCP_DOWNLOAD_DIR`, `~/Downloads/Content` by default): a destination
pointing outside it is refused, not clamped — widening that is the operator's
decision, not something a prompt can talk the server into.

**What it can and cannot do, briefly** — the
[MCP guide](apps/mcp/README.md#what-it-supports-today) has the full three
tables, and each row there was driven over stdio against a running engine
rather than inferred:

|  |  |
| --- | --- |
| **Works** | URLs and playlists · local files (uploaded to the engine, so a laptop can drive a NAS) · PDFs, read for their text layer · video, audio, subtitles, transcripts, summaries, translations, chapters, thumbnails, metadata, Markdown, PDF · delivery into your library · cookie-authenticated sources |
| **Does not** | no live progress (`get_job` is a poll) · no `.docx`/`.epub`/`.odt`/`.rtf`, no OCR for scans · no transcoding · no playlist sync · nothing deletes anything · [the API has no authentication](docs/architecture-decisions/0024-no-authentication-is-still-the-answer.md) |
| **Needs a runner** | Summaries, translations and derived chapters want a local [Ollama](https://ollama.com) or a cloud key; transcripts want subtitles, or the optional Whisper runner. The engine reports these as `unavailable` up front rather than failing halfway |
| **By design** | **stdio, not an HTTP endpoint** — the server runs where *you* do, which is what lets an agent hand it `~/Documents/report.pdf` and have the bytes uploaded to an engine on your NAS. It also means no open port on a system that has [no authentication](docs/architecture-decisions/0024-no-authentication-is-still-the-answer.md). For a client that cannot spawn a process — Open WebUI, hosted UIs — [`mcpo`](https://github.com/open-webui/mcpo) bridges it today |
| **Coming** | [Retrying only what failed](docs/architecture-decisions/0025-retrying-only-what-failed.md) · [playlist sync](docs/roadmap/roadmap.md) · [retention](docs/architecture-decisions/0023-retention-and-reclaiming-disk.md) · more document readers |

### CLI — terminals, scripts, and cron

[README](apps/cli/README.md) ·
[`content-cli` on PyPI](https://pypi.org/project/content-cli/)

```bash
uv tool install content-cli
export CONTENT_API_URL=http://nas.local:8010

content analyze "https://www.youtube.com/watch?v=…"
content video "https://…" --height 1080 --subs en,fr --watch
content audio "https://…" --format opus --playlist --watch
content submit request.json --watch          # a raw GenerationRequest, or -
content jobs ; content job <id> ; content artifacts <id>
content download <artifact_id> -o out.mkv
```

`--watch` follows the job's **event stream** rather than re-asking for its
status, and the exit code carries the outcome so a script can chain on it
([ADR 0021](docs/architecture-decisions/0021-a-job-does-not-succeed-with-a-failed-step.md)):
`0` succeeded, `2` partially succeeded, `1` failed or cancelled. `2` is
deliberately distinct — a playlist that yielded five videos of six is not a
failure, but a script that treats it as a success will quietly move on with
missing files.

### Python SDK — typed, sync and async

[README](packages/python-sdk/README.md) ·
[`content-sdk` on PyPI](https://pypi.org/project/content-sdk/)

The **one** official API client: the CLI, the MCP server, and the web apps all
speak through it, so the engine's rules are never duplicated. Python 3.11+,
`httpx` and `pydantic` only.

```python
from content_sdk import ContentClient, outputs

with ContentClient("http://localhost:8010") as client:
    analysis = client.analyze(outputs.url_source("https://www.youtube.com/watch?v=…"))
    job = client.generate(analysis.id, [outputs.audio_output()])
    job.wait()
    for artifact in job.artifacts:
        print(artifact.display_filename, artifact.delivered_path)
```

`AsyncContentClient` mirrors the whole surface. Analyses are addressable, so
every call accepts either an `analysis_id` or inline sources
([ADR 0014](docs/architecture-decisions/0014-addressable-analyses.md));
`client.upload_file(path)` streams a local file to the engine and hands back a
ready-to-use source; and every non-2xx becomes a typed exception carrying the
stable error codes (`NotFound`, `Gone`, `ValidationError`).

### REST API — every other language

[Public contract](docs/contract.md) · Swagger at <http://localhost:8010/docs>

`/api/v1` is the contract every client above is built on, and it is stable
enough to build a ninth client against: requests describe desired outputs
rather than technical operations, error codes are machine identifiers that do
not change (only their human messages do), and *"valid but not implemented"* is
a different, explicit answer from *"invalid"*. Reserved fields are refused
rather than silently ignored. The V1 API has **no built-in authentication** —
see [deployment and security](#deployment-and-security).

### Content Console — observe and pilot the engine

<http://localhost:8503> · [README](apps/web-admin/README.md)

The operations console: strictly observability and control, and deliberately
**not** a way to create downloads (that is Studio and HomeTube).

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-console.png"
       alt="Content Console — jobs, runners, storage and configuration, live" width="80%">
  <br>
  <sub><b>Content Console</b> — observe and control the engine</sub>
</div>

- **Overview** — version, cache, concurrency, analysis TTL, a live jobs pulse,
  the installed runners with their availability, storage paths.
- **Environment** — every `CONTENT_*` variable with its *effective* value,
  whether it was set or fell back to a default, and a one-line description.
  Secrets are never shown — only their presence and length.
- **Jobs** — a filterable list plus full detail: steps, the submitted
  `GenerationRequest`, artifacts with their provenance, ordered events,
  per-step logs, cancel and retry, with an opt-in 5-second auto-refresh that
  polls only while jobs are in flight.
- **Storage & cache**, and a **contract & API** tab with the schemas, Swagger,
  ReDoc, and a raw API tester.

## What Content can produce

Content resolves availability for each analyzed resource and installed runner.
Clients show what can actually be produced instead of presenting a static list
and failing halfway through a job.

| Output | Typical inputs and behavior |
| --- | --- |
| **Video** | Media URLs and local media files; stream selection, remux, fast or frame-accurate cutting, and SponsorBlock handling |
| **Audio** | Media URLs as source audio, Opus, MP3, or M4A; local files keep their native audio stream |
| **Subtitles** | Manual or automatic tracks selected by language |
| **Transcript** | Existing subtitles, or audio with the optional Whisper runner |
| **Summary** | Transcript or readable text through a local or opt-in cloud LLM |
| **Translation** | Subtitles or transcripts through an LLM; subtitle timings stay aligned |
| **Chapters** | Chapters declared by the source, or chapters derived from a transcript through an LLM |
| **Thumbnail and keyframes** | Published artwork or frames extracted from video |
| **Metadata** | Normalized, provider-independent resource information |
| **Markdown and plain text** | Readable web pages, text and Markdown files, **PDFs** (their text layer), and inline text |
| **PDF** | Readable sources or outputs such as summaries, transcripts, and translations |

Media acquisition works with YouTube and the
[sites supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
Playlists can produce artifacts for each member in a single traceable job.
Finished files can be delivered directly into any mounted filesystem library,
including folders watched by Plex, Jellyfin, or Emby.

### Local and optional AI runners

Content has no mandatory cloud dependency. AI-backed outputs activate when a
compatible runner is available:

- **Summaries, translations, and derived chapters:** connect a local
  [Ollama](https://ollama.com) instance, or explicitly configure an Anthropic
  or OpenAI API key. Cloud runners can be excluded per request.
- **Transcription from audio:** set `CONTENT_INSTALL_STT=true` and rebuild to
  install the local Whisper runner. Transcripts from existing subtitles do not
  need it.

Unavailable optional runners do not make Content unhealthy. The affected
capability is reported as unavailable, and an impossible request is refused
before the job starts. See
[deployment and configuration](docs/operations/deployment.md) for the full
inventory.

## Why build on Content?

- **Declare intent, not tooling.** Requests describe outputs; yt-dlp, ffmpeg,
  Whisper, LLMs, and PDF renderers remain replaceable implementation details.
- **Self-hosted and local-first.** Run it on a workstation, NAS, or homelab.
  There is no account, telemetry, or mandatory cloud service.
- **One public contract.** Studio, HomeTube, MCP, the CLI, SDK, extension, and
  REST API converge on the same domain instead of drifting into parallel
  feature sets.
- **Human files, not pipeline debris.** The engine names every artifact and can
  place it directly in the library you already use.
- **Observable work.** Jobs expose states, ordered events, progress, logs,
  artifacts, provenance, cancellation, and retry.
- **Honest capabilities.** “Valid but unsupported,” “unavailable on this
  source,” and “broken” are different answers, reported before or during the
  correct stage.

## How it works

The public request describes desired outputs. It does not prescribe technical
operations:

```text
Sources → Analysis → Capabilities → Execution plan → Job → Artifacts
```

```text
Content Backend (REST /api/v1)   ← domain, planning, execution, persistence
        │
   Content Python SDK            ← official Python API client
    │        │        │
   CLI      MCP     Web apps     ← thin clients of the public contract

   Browser extension             ← JavaScript client of /api/v1
```

The engine analyzes source facts, resolves feasible capabilities against the
installed implementations, builds a deterministic execution plan, and runs it
as an observable job. The database is the source of truth; yt-dlp, ffmpeg,
transcription, LLM, and PDF implementations stay behind dedicated boundaries.

The domain keeps user intent (`GenerationRequest`), resolved strategy
(`ExecutionPlan`), execution (`Job`), and concrete results (`Artifact`)
separate. For the full design, see the
[architecture overview](docs/architecture.md),
[public contract](docs/contract.md), [domain model](docs/domain.md), and
[architecture decisions](docs/architecture-decisions/).

## Deployment and security

Content targets single-host Linux/macOS deployments on amd64 or arm64. Docker
Compose runs the API and embedded worker together, with the web apps as
separate API clients. SQLite and artifact storage persist locally; no Redis,
Celery, Kubernetes, or cloud infrastructure is required.

The commented [`.env.example`](.env.example) covers UI selection, image
versions, ports, storage and delivery, language preferences, server-side
cookie credentials, optional LLM/STT runners, CORS, and notifications. No
release check makes an outbound request unless you configure one.

The V1 API has no built-in authentication. Keep it on a trusted network or put
an authenticating reverse proxy in front of any externally reachable instance.
See [deployment](docs/operations/deployment.md) for configuration, health
checks, data layout, authenticated sources, and production guidance, and
[ADR 0024](docs/architecture-decisions/0024-no-authentication-is-still-the-answer.md)
for why that is the V1 answer.

## Development

The repository is a monorepo containing the backend, web applications, CLI,
MCP server, Chromium extension, and Python SDK. The root `Makefile` is the main
entry point:

```bash
make install       # create the development environment and install packages
make validate      # formatting, linting, and hermetic tests: the official gate
make test-ui       # hermetic Streamlit application tests
make validate-all  # official gate plus UI and opt-in external-tool tests
```

The main test suite does not require the Internet. Read the
[repository architecture](docs/repository-architecture.md) and
[validation guide](docs/development/validation.md) before changing the code.
Releases follow the [release runbook](docs/operations/releasing.md).

## Documentation

[docs/README.md](docs/README.md) is the complete documentation map. Useful
starting points:

- [Product vision](docs/product/vision.md)
- [Public contract](docs/contract.md) and [domain model](docs/domain.md)
- [Architecture](docs/architecture.md) and
  [invariants](docs/architecture/invariants.md)
- [Storage and artifact delivery](docs/storage.md)
- [Deployment and operations](docs/operations/deployment.md)

## Licence

Content is free and open-source software licensed under
**[AGPL-3.0-or-later](LICENSE)**. You may run, study, modify, and redistribute
it, including commercially, under the terms of the licence.

The AGPL contains source-sharing requirements for certain modified,
network-accessible versions. See [COMMERCIAL.md](COMMERCIAL.md) for the
project's factual licensing summary; no separate commercial offering is
currently available.

## Governance

Content is developed and maintained by Yann Orieult under a single-maintainer
model. Bug reports, ideas, and design feedback are welcome through issues. Code
contributions are not accepted—unsolicited pull requests are closed without
review; forking is the intended path for independent changes. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [GOVERNANCE.md](GOVERNANCE.md).

Report security issues privately as described in [SECURITY.md](SECURITY.md).

© 2026 Yann Orieult.
