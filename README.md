<!-- markdownlint-disable MD013 MD033 MD041 -->
<div align="center">

# Content

### From sources to artifacts

[![CI](https://github.com/LatentNoise/content/actions/workflows/ci.yml/badge.svg)](https://github.com/LatentNoise/content/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%C2%B7%20arm64-2496ED.svg?logo=docker&logoColor=white)](docs/operations/deployment.md)
[![License](https://img.shields.io/badge/License-AGPL--3.0--or--later-green.svg)](LICENSE)

**Self-hosted · API-first · local-first**

</div>

Paste a link. Say what you want — the video, the audio, subtitles, a
transcript, a summary, a PDF. Content analyzes the source, works out what it
can honestly produce, does the work, names the files like a human would, and
delivers them into your library.

```text
                             ┌─ My Conference.mp4
                             ├─ My Conference - audio.opus
YouTube URL ──→ Content ─────┼─ My Conference - subtitles - en.srt
                             ├─ My Conference - subtitles - fr.srt
                             ├─ My Conference - transcript.txt
                             ├─ My Conference - summary.md
                             └─ My Conference - summary.pdf
```

That is one request. Everything above comes from it — no yt-dlp flags, no
ffmpeg incantations, no gluing five tools together. Content runs them for you,
behind one API, on your own machine.

<div align="center">
  <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-hometube-demo.gif"
       alt="HomeTube demo — paste a YouTube URL, choose the outputs, watch the job deliver into the library"
       width="75%">
  <br>
  <sub><b>HomeTube</b>, Content's YouTube UI — paste, choose, done.</sub>
</div>

<!-- Visuals are produced in media/ (untracked) and attached to releases —
see media/README.md. -->

## Coming from HomeTube?

Start exactly where you are. Content ships a **HomeTube UI**: the classic
flow — paste a URL, pick video/audio, subtitles, SponsorBlock, a library
folder — rebuilt on a real engine. Same reflexes, nothing to relearn. (The
original HomeTube remains its own project; this is its workflow, grown up.)

The difference appears the day you want one step more. Every HomeTube action
is a plain API request underneath — and the API answers requests HomeTube
never could:

- the talk you just downloaded → **a transcript and a summary PDF** next to it;
- a playlist → **numbered files**, each with its own subtitles and metadata;
- a web article → **clean Markdown** for your notes vault;
- your AI assistant → *"grab the audio of this lecture and summarize it"* —
  Content is an **MCP server** Claude or any agent can drive;
- a script, a cron job, a NAS hook → the same one request, through the CLI or
  the Python SDK.

**HomeTube downloads media. Content decides what a source can become.**

## Why Content?

- **More than a downloader.** Plenty of excellent tools fetch videos. Content
  is about what happens *after* — transcripts, summaries, translations,
  chapters, documents — produced by the same engine, from the same source, in
  the same request.
- **Declare intent, not tooling.** Ask for outputs; the engine analyzes the
  source, tells you honestly what it can produce, and plans the rest. yt-dlp,
  ffmpeg, Whisper, LLMs, and PDF renderers stay behind its boundaries.
- **Self-hosted and local-first.** A workstation, NAS, or homelab is the
  target, not an afterthought. No telemetry, no phone-home, no account.
- **Local AI by default.** Ollama for transcripts-to-summaries on your own
  hardware; cloud LLMs are optional, opt-in, and excludable per request.
- **Files worth keeping.** Artifacts get human names — `Talk - summary.pdf`,
  not `dQw4w9WgXcQ.f251.webm` — and land in your Plex/Jellyfin/NAS library.
- **One engine, many doors.** Web UIs, a browser extension, a CLI, a Python
  SDK, an MCP server for agents — all clients of one versioned API.
- **Traceable execution.** Every job exposes status, ordered events, logs,
  provenance, cancellation, and retry. When something fails, you can see why.

## Quick start

Docker is the only prerequisite — nothing to clone, nothing to build:

```bash
mkdir content && cd content
curl -fsSLO https://raw.githubusercontent.com/LatentNoise/content/main/deploy/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/LatentNoise/content/main/.env.example
docker compose up -d
```

Four services start from the published images:

| Service | Default URL | Purpose |
| --- | --- | --- |
| HomeTube | <http://localhost:8501> | Focused YouTube workflow |
| Content Studio | <http://localhost:8502> | General-purpose request builder |
| Content Console | <http://localhost:8503> | Operations and job control; does not create downloads |
| Content API | <http://localhost:8010/docs> | REST API, OpenAPI, and embedded worker |

The engine and Content Console always run; the `COMPOSE_PROFILES` line in
`.env` chooses the download UIs — keep the default `hometube,studio`, or set
it to just one of them.

**Where your files land.** Everything stays in the folder you created:
`./data` holds the database, the jobs and the artifacts, and finished files
are delivered to `./playground/output` — both created on first start. To
deliver straight into your own library instead — a NAS share, a Plex or
Jellyfin folder — set `CONTENT_DELIVERY_DIR_HOST` in `.env`; its sub-folders
become the destination choices offered in the web apps.

**Staying up to date.** `docker compose pull && docker compose up -d`.

<details>
<summary><b>Build from source instead</b> — for development, or to include the
optional speech-to-text runner</summary>

```bash
git clone https://github.com/LatentNoise/content.git
cd content
cp .env.example .env
docker compose up -d --build
```

The repository's `docker-compose.yml` adds `build:` blocks beside the same
images; [`deploy/docker-compose.yml`](deploy/docker-compose.yml) is its
build-free twin for the install above, kept identical by a test.

</details>

## What Content can produce

Availability is resolved for each analyzed resource, so clients can show what
the current source and installed runners can actually produce.

| Output | Typical inputs and behavior |
| --- | --- |
| **Video** | Media URLs and local media files; stream selection, remux, cutting, and SponsorBlock handling |
| **Audio** | Media URLs and files; source audio or Opus, MP3, and M4A |
| **Subtitles** | Manual or automatic tracks, selected by language |
| **Transcript** | Existing subtitles, or audio with the optional Whisper runner |
| **Summary** | Transcript or readable text through a local or opt-in cloud LLM |
| **Translation** | Subtitles or transcripts through an LLM; subtitle timings are preserved |
| **Chapters** | Chapters declared by the source, or chapters derived from a transcript through an LLM |
| **Thumbnail and keyframes** | Published artwork or frames extracted from video |
| **Metadata** | Normalized, provider-independent resource information |
| **Markdown and plain text** | Readable web pages, text files, Markdown files, and inline text |
| **PDF** | Readable sources or outputs such as summaries, transcripts, and translations |

Media acquisition works with YouTube and the
[sites supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
Playlist analysis and per-item video/audio jobs are supported. Content gives
artifacts readable filenames and can deliver them directly into a configured
Plex, Jellyfin, Emby, NAS, or filesystem library.

### Optional capabilities

The standard container includes yt-dlp, ffmpeg, and PDF rendering. Some derived
outputs need an additional runner:

- **Summaries, translations, and derived chapters:** connect a local
  [Ollama](https://ollama.com) instance, or explicitly configure an Anthropic
  or OpenAI API key.
- **Transcription from audio:** set `CONTENT_INSTALL_STT=true` and rebuild to
  install the Whisper runner. Transcripts from existing subtitles work without
  it.

Unavailable optional runners do not make the service unhealthy. Content
reports the affected capability as unavailable and rejects an impossible
request before starting the job. See
[deployment and configuration](docs/operations/deployment.md) for the full
inventory.

## One engine, multiple interfaces

- **[HomeTube](apps/web-hometube/README.md)** — a streamlined,
  capability-driven UI for YouTube and similar media workflows.
- **[Content Studio](apps/web-studio/README.md)** — the general UI for composing
  requests across URL, file, and text sources.
- **[Content Console](apps/web-admin/README.md)** — inspect health, runners,
  configuration, storage, jobs, events, and logs; cancel or retry jobs.
- **[Python SDK](packages/python-sdk/README.md)** — the typed sync and async
  client used by the Python applications: `pip install content-sdk`.
- **[CLI](apps/cli/README.md)** — ergonomic commands for analysis, generation,
  job tracking, scripts, and raw requests: `uv tool install content-cli`
  (or `pipx install content-cli`).
- **[Browser extension](apps/browser-extension-chromium/README.md)** — send the current
  tab to Content, in one click. Chromium (Chrome, Brave, Edge…); download the
  zip from a release, unzip, *Load unpacked*.
- **[REST API](docs/contract.md)** — the versioned `/api/v1` contract, with
  OpenAPI and Swagger documentation.
- **[MCP server](apps/mcp/README.md)** — intention-level tools and resources for
  MCP-compatible agents: `uv tool install content-mcp`, one config block in
  Claude, Cursor, or any MCP client.

<div align="center">
<table>
  <tr>
    <td align="center" width="50%">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-studio.png"
           alt="Content Studio — the general-purpose request builder" width="100%"><br>
      <sub><b>Content Studio</b> — compose any request the contract allows</sub>
    </td>
    <td align="center" width="50%">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.1.0/2026-08-09-console.png"
           alt="Content Console — jobs, runners, storage and configuration, live" width="100%"><br>
      <sub><b>Content Console</b> — observe and pilot the engine</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <img src="https://github.com/LatentNoise/content/releases/download/v0.2.0/2026-08-10-browser_extension.jpg"
           alt="HomeTube for Content — the Chromium extension popup on a video page" width="420"><br>
      <sub><b>Browser extension</b> — the current tab to your library, in one click</sub>
    </td>
  </tr>
</table>
</div>

### Python SDK example

```python
from content_sdk import ContentClient, outputs

with ContentClient("http://localhost:8010") as client:
    analysis = client.analyze(outputs.url_source("https://www.youtube.com/watch?v=…"))
    job = client.generate(analysis.id, [outputs.audio_output()])
    job.wait()
    for artifact in job.artifacts:
        print(artifact.display_filename, artifact.delivered_path)
```

### CLI example

```bash
content analyze "https://www.youtube.com/watch?v=…"
content video "https://…" --height 1080 --subs en,fr --watch
content audio "https://…" --format opus --playlist --watch
content jobs
```

### Agent example

`uv tool install content-mcp`, add one block to Claude, Cursor, or any MCP
client, and your assistant can drive the engine — *"analyze this talk, grab
the audio, and summarize it into my library"*:

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
separate. For the full design, see the [architecture overview](docs/architecture.md),
[public contract](docs/contract.md), [domain model](docs/domain.md), and
[architecture decisions](docs/architecture-decisions/).

## Deployment and configuration

Content targets single-host Linux/macOS deployments on amd64 or arm64. Docker
Compose runs the API and embedded worker in one container, with the web apps as
separate API clients. SQLite and artifact storage persist locally; no Redis,
Celery, Kubernetes, or cloud service is required.

The commented [`.env.example`](.env.example) covers ports, storage and delivery,
language preferences, cookie credentials, optional LLM/STT runners, CORS, and
notifications. See [deployment](docs/operations/deployment.md) for configuration,
health checks, authenticated sources, data layout, and production guidance.

The web apps surface operational notices as dismissible banners: UI and
backend images running different versions of Content, a newer Content release
being available (if you opt in to the release check), and — optionally — an
installed yt-dlp older than a threshold you choose. None of these involve an
outbound call unless you configure one, and a fresh install shows none of
them.

The V1 API has no built-in authentication. Keep it on a trusted network or put
an authenticating reverse proxy in front of any externally reachable instance.

## Development

The repository is a monorepo containing the backend, web applications, CLI,
MCP server, browser extension, and Python SDK. The root `Makefile` is the main
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

- [Product vision](docs/product/vision.md) and [scope](docs/product/scope.md)
- [Public contract](docs/contract.md) and [domain model](docs/domain.md)
- [Architecture](docs/architecture.md) and [invariants](docs/architecture/invariants.md)
- [Storage and delivery](docs/storage.md)
- [Deployment and operations](docs/operations/deployment.md)
- [Roadmap](docs/roadmap/roadmap.md)

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
contributions are not accepted — unsolicited pull requests are closed without
review; forking is the intended path for independent changes. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [GOVERNANCE.md](GOVERNANCE.md).

Report security issues privately as described in [SECURITY.md](SECURITY.md).

© 2026 Yann Orieult.
