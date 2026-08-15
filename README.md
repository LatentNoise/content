<!-- markdownlint-disable MD013 MD026 MD033 MD036 MD041 -->
<div align="center">

# Content

## Turn URLs, files, and text into media, knowledge, and documents.

**One self-hosted engine, with Content Studio for general workflows and HomeTube for YouTube.**

[![Latest release](https://img.shields.io/github/v/release/LatentNoise/content?sort=semver&display_name=tag)](https://github.com/LatentNoise/content/releases/latest)
[![CI](https://github.com/LatentNoise/content/actions/workflows/ci.yml/badge.svg)](https://github.com/LatentNoise/content/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%C2%B7%20arm64-2496ED.svg?logo=docker&logoColor=white)](docs/operations/deployment.md)
[![License](https://img.shields.io/badge/License-AGPL--3.0--or--later-green.svg)](LICENSE)

[Start with Content](#quick-start) ·
[Use HomeTube](#hometube-for-youtube) ·
[Connect an agent](#agents-and-mcp) ·
[Choose your interface](#choose-your-interface) ·
[Read the docs](docs/README.md)

</div>

Content is a self-hosted engine that turns supported sources into media,
transcripts, summaries, translations, images, metadata, Markdown, PDF, and
more. **Content Backend** analyzes each source, determines what it can produce,
plans and runs the job, records its history, and delivers the results.

Use **Content Studio** for general workflows with URLs, allowed server-side
files, and inline text. Use **HomeTube** for the focused YouTube video and
playlist experience. Both web apps are optional: the same backend powers the
**Chromium browser extension** and is available directly through the **REST
API, MCP server, CLI, and typed Python SDK**.

## HomeTube for YouTube

When the source is a YouTube video or playlist, HomeTube is probably the
interface you want: paste the URL, choose the media and related artifacts, and
follow the job into your library.

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-hometube-demo.gif"
       alt="HomeTube demo — paste a YouTube URL, choose the outputs, watch the job deliver into the library"
       width="75%">
  <br>
  <sub><b>HomeTube in Content</b> — paste a YouTube URL, choose what you want, follow the job.</sub>
</div>

<!-- Visuals are produced in media/ (untracked) and attached to releases —
see media/README.md. -->

> [!NOTE]
> **A proven workflow, now growing into a platform.** The standalone HomeTube
> container has passed **300,000 package downloads on GitHub Container
> Registry** and remains maintained during the transition. Content carries
> that familiar workflow forward and is designed to become its successor.

The standalone HomeTube project remains separate; it has not been retrofitted
to run on Content. This repository contains a new HomeTube UI built on the
Content engine.

With HomeTube in Content, you can:

- download a video or playlist as video or audio, with quality, codec,
  container, audio-language, and subtitle choices;
- remove or mark sponsored segments with SponsorBlock, cut clips, use
  server-side cookie credentials, and embed subtitles, chapters, thumbnails,
  and metadata;
- give every artifact a readable name and deliver it into a filesystem library
  watched by Plex, Jellyfin, or Emby;
- ask the same source for a transcript, summary, thumbnail, or metadata—not
  just the downloaded media—when the required runners are available.

HomeTube is deliberately focused: it does not accept file or inline-text
sources, and it does not expose the broader document workflow or PDF output.
For those workflows, use Content Studio, the API, CLI, or SDK. **HomeTube is a
Content client, not a layer every Content user must pass through.**

### Configuring HomeTube

HomeTube has no settings of its own: it reads the engine's configuration, so
everything below goes in the `.env` next to your `docker-compose.yml`. These
are the ones that change what you see; the
[full table](docs/operations/deployment.md#configuration-a-root-env-not-versioned)
lists every variable.

| Variable | Default | What it changes in HomeTube |
| --- | --- | --- |
| `CONTENT_DELIVERY_DIR_HOST` | `./playground/output` | Where finished files land. Point it at your Plex/Jellyfin/Emby library and its sub-folders become the destination choices in the form |
| `CONTENT_LANGUAGE_PRIMARY` | — | The language you speak: first in the audio order and pre-selected |
| `CONTENT_LANGUAGES_SECONDARIES` | — | `en,es` — offered and pre-selected after the primary |
| `CONTENT_VO_FIRST` | `true` | Put the original voice ahead of your own language |
| `CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES` | `true` (the shipped `.env` sets `false`) | Whether your primary language is also pre-checked in the subtitle list |
| `CONTENT_CREDENTIALS` | — | `youtube=/config/cookies.txt` — a cookie file for age-restricted or members-only videos. The file never leaves the server; HomeTube only shows its id |
| `COMPOSE_PROFILES` | `hometube,studio` | Which UIs start. `hometube` alone runs HomeTube only |
| `HOMETUBE_PORT` | `8501` | The host port HomeTube listens on |

**What the language settings actually do**, since the effect is easier to read
than the rule. With:

```bash
CONTENT_LANGUAGE_PRIMARY=fr
CONTENT_LANGUAGES_SECONDARIES=en,es
CONTENT_VO_FIRST=true
CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES=false
```

on a Japanese talk that offers `ja`/`en` audio and `ja`/`fr`/`en`/`de`
subtitles, HomeTube shows **audio** ordered `ja, en` — the original voice
first because `VO_FIRST` is on, then your languages — with both pre-selected.
For **subtitles** it pre-checks `en` only: the original voice never applies to
subtitles, `de` is not one of your languages, and `fr` is left out because
`…_INCLUDED_IN_SUBTITLES=false` — someone fluent in French does not need French
subtitles but still wants the English ones. Set it to `true` and `fr, en` are
both pre-checked.

Nothing is ever pre-selected that the source does not actually offer, and every
default remains editable in the form before you launch the job.

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

## Quick start

Docker Compose is the only runtime prerequisite. Install from the published
images—nothing to clone or build:

```bash
mkdir content && cd content
curl -fsSLO https://raw.githubusercontent.com/LatentNoise/content/main/deploy/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/LatentNoise/content/main/.env.example
docker compose up -d
```

Open **Content Studio at <http://localhost:8502>** for the general URL, file,
and inline-text workflow. If your source is YouTube, open the focused
**HomeTube interface at <http://localhost:8501>**. You can also skip both web
apps and use the API, MCP server, CLI, or SDK directly.

The default `.env` starts the complete local stack:

| Service | Default URL | Purpose |
| --- | --- | --- |
| **Content Studio** | <http://localhost:8502> | General requests across URL, allowed-file, and inline-text sources |
| HomeTube | <http://localhost:8501> | Focused YouTube video and playlist workflow |
| Content Console | <http://localhost:8503> | Health, configuration, storage, jobs, events, and logs |
| Content API | <http://localhost:8010/docs> | REST API, OpenAPI, and embedded worker |

Studio's file source is a path the server can read under a configured allowed
input root. With this Compose setup, put files in `./playground/input`; direct
browser upload is not implemented yet.

Everything stays in the installation folder: `./data` holds the database,
jobs, and artifacts; finished files are delivered to `./playground/output`.
Set `CONTENT_DELIVERY_DIR_HOST` in `.env` to point at your own NAS or media
library instead. Its subfolders become destination choices in the web apps.

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

## Agents and MCP

Content gives an MCP-compatible agent a controlled way to turn URLs into real
artifacts, not just talk about them. The official **`content-mcp`** server exposes
intention-level tools to:

- analyze a URL and discover what it can produce;
- request one or several outputs and choose a library destination;
- monitor the job, cancel it, and report actionable failures;
- find the artifacts and their delivered paths;
- read small text artifacts inline while keeping large media out of the model
  context.

The agent never needs shell access, yt-dlp syntax, or backend internals. MCP,
the web apps, and the CLI all reach the same engine and the same contract.

Install the server and connect it to a running Content instance:

```bash
uv tool install content-mcp

# Claude Code
claude mcp add content \
  --env CONTENT_API_URL=http://localhost:8010 \
  -- content-mcp
```

For Claude Desktop, Cursor, and other MCP clients using the standard JSON
shape:

```json
{
  "mcpServers": {
    "content": {
      "command": "content-mcp",
      "env": { "CONTENT_API_URL": "http://localhost:8010" }
    }
  }
}
```

Then ask naturally:

> Analyze this lecture, save its audio into `Talks`, create a transcript and a
> structured summary if the available runners allow it, and tell me where
> every artifact landed.

The normal tool journey is
`get_config → analyze_source → generate → get_job → get_artifact`. See the
[MCP guide](apps/mcp/README.md) for every tool, resource, installation option,
and verified behavior.

## Choose your interface

One engine, several independent clients. Pick the one that fits the job;
HomeTube is entirely optional.

| Interface | Best for | Start here |
| --- | --- | --- |
| **[Content Studio](apps/web-studio/README.md)** | General capability-driven requests from URLs, allowed local files, and inline text | Included in Docker Compose at `:8502` |
| **[HomeTube](apps/web-hometube/README.md)** | The simplest YouTube video or playlist → library experience | Included in Docker Compose at `:8501` |
| **[MCP server](apps/mcp/README.md)** | Giving Claude, an IDE, or another MCP-compatible agent access to Content | `uv tool install content-mcp` |
| **[Chromium extension](apps/browser-extension-chromium/README.md)** | Sending the current supported Chrome, Brave, Edge, or Chromium tab to Content | Download the release zip, unzip it, then *Load unpacked* |
| **[CLI](apps/cli/README.md)** | Terminals, scripts, cron jobs, and raw request files | `uv tool install content-cli` |
| **[Python SDK](packages/python-sdk/README.md)** | Typed sync and async application integration | `pip install content-sdk` |
| **[REST API](docs/contract.md)** | Any language or integration that speaks HTTP | `/api/v1`, Swagger at `:8010/docs` |
| **[Content Console](apps/web-admin/README.md)** | Observing and operating the engine | Included in Docker Compose at `:8503` |

<div align="center">
<table>
  <tr>
    <td align="center" width="50%">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-studio.png"
           alt="Content Studio — the general-purpose request builder" width="100%"><br>
      <sub><b>Content Studio</b> — build general URL, file, and text requests</sub>
    </td>
    <td align="center" width="50%">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-console.png"
           alt="Content Console — jobs, runners, storage and configuration, live" width="100%"><br>
      <sub><b>Content Console</b> — observe and control the engine</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.2.0/2026-08-10-browser_extension.jpg"
           alt="HomeTube for Content — the Chromium extension popup on a video page" width="420"><br>
      <sub><b>Browser extension</b> — send the current tab to Content</sub>
    </td>
  </tr>
</table>
</div>

### CLI

```bash
content analyze "https://www.youtube.com/watch?v=…"
content video "https://…" --height 1080 --subs en,fr --watch
content audio "https://…" --format opus --playlist --watch
content submit request.json --watch
content jobs
```

### Python SDK

```python
from content_sdk import ContentClient, outputs

with ContentClient("http://localhost:8010") as client:
    analysis = client.analyze(outputs.url_source("https://www.youtube.com/watch?v=…"))
    job = client.generate(analysis.id, [outputs.audio_output()])
    job.wait()
    for artifact in job.artifacts:
        print(artifact.display_filename, artifact.delivered_path)
```

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
| **Markdown and plain text** | Readable web pages, text files, Markdown files, and inline text |
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
checks, data layout, authenticated sources, and production guidance.

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
