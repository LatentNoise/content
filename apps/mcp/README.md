<!-- mcp-name: io.github.LatentNoise/content -->

# `content-mcp` — Content MCP server

The official **MCP server** for the Content engine: give Claude, an IDE or any
MCP-compatible agent the ability to drive your Content instance — analyze a
URL, generate video/audio/subtitles/transcripts, watch the job, land the files
in your library. It is an agentic facade over the official
[Python SDK](../../packages/python-sdk/README.md): **no REST of its own, no
business logic**.

```text
any MCP client → content-mcp (this) → content_sdk → your Content engine (/api/v1)
```

## Install

The server is an ordinary Python application — nothing to clone. There are two
shapes, and which one you want depends on who should own the lifecycle.

**Let the client fetch it (nothing installed).** `uvx` downloads the server on
first use and caches it, so there is nothing to install and nothing left behind.
This is the shortest path and the one to prefer:

```bash
uvx content-mcp --version       # 0.6.4 — fetched on the spot
```

Your MCP client then spawns `uvx content-mcp` instead of a binary (see
*Connect it to your engine* below). `pipx run content-mcp` does the same if you
use pipx.

**Or install an executable on your PATH.** Pin the version, work offline, or
just prefer a command you can run yourself:

```bash
uv tool install content-mcp     # isolated, on your PATH
content-mcp --help

# or
pipx install content-mcp
```

**Either way, updating is explicit.** This is worth knowing, because it is easy
to assume otherwise: `uvx` reuses the environment it cached and does *not*
re-check PyPI on each run, so a server started this way keeps its version until
you say otherwise. Measured, not assumed — a plain `uvx content-mcp` served
0.6.5 the day 0.6.6 was published.

```bash
uvx --refresh content-mcp        # take the newest release
uvx content-mcp@0.6.6            # or pin one, which a client config can do too
uv tool upgrade content-mcp      # for the installed form
```

Both forms run the same wheel; the difference is only where it lives.

[`content-mcp` on PyPI](https://pypi.org/project/content-mcp/) pulls
[`content-sdk`](https://pypi.org/project/content-sdk/) as an ordinary
dependency, pinned to the matching release. The wheels are also attached to
each [GitHub release](https://github.com/LatentNoise/content/releases/latest)
for air-gapped installs (`uv tool install ./content_mcp-<v>-py3-none-any.whl
--find-links .`).

## Connect it to your engine

One environment variable: `CONTENT_API_URL` (default `http://localhost:8010`).
The server speaks stdio by default — your MCP client spawns it; you never run
it by hand. A second transport, streamable-http, is available for clients
that cannot spawn a process — see *Why stdio, and not an HTTP endpoint* below.

### Why stdio, and not an HTTP endpoint

A deliberate choice rather than a missing feature, and the reason is the point
of the whole project: **Content turns resources you already have into
artifacts, and a lot of those live on your own machine.**

Over stdio the server runs where you do. That is what makes this work:

```text
"Summarize ~/Documents/rapport.pdf"
```

The file is read on your machine and uploaded to the engine, which may be a NAS
in another room. Move the server to an HTTP endpoint next to the engine and
that sentence stops meaning anything — the path would resolve on the *server's*
filesystem, so at best it fails, at worst it reads a different file with the
same name. No amount of protocol design fixes that: the bytes are where the
person is.

Two things follow from it, both worth having:

- **No network surface.** The engine has no authentication by design
  ([ADR 0024](../../docs/architecture-decisions/0024-no-authentication-is-still-the-answer.md)),
  and an HTTP MCP server would extend that to "anyone who can reach the port
  can drive it — download, write files, spend your CPU". stdio has no port.
- **No service to run.** The client starts and stops the process. Nothing to
  supervise, nothing to restart, nothing left listening after you close the
  laptop.

**What stdio cannot do**, plainly: serve a client that cannot spawn a process on
your machine — Open WebUI in a container, LibreChat, a hosted web UI. For
those, set `CONTENT_MCP_TRANSPORT=streamable-http` (the `mcp` library's own
implementation — this server adds no HTTP code of its own) and point the
client at `http://127.0.0.1:8770/mcp` by default:

```bash
CONTENT_MCP_TRANSPORT=streamable-http CONTENT_API_URL=http://localhost:8010 content-mcp
```

| Variable | Default | What it does |
| --- | --- | --- |
| `CONTENT_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `CONTENT_MCP_HTTP_HOST` | `127.0.0.1` | streamable-http only — **loopback**. Widening it (`0.0.0.0`, a LAN address) is something you ask for explicitly; this server will not default it open |
| `CONTENT_MCP_HTTP_PORT` | `8770` | streamable-http only |

The trade named above is real, and streamable-http does not escape it: **local
file paths are refused outright** rather than resolved on whatever host
happens to be running the server. `analyze_source("~/report.pdf")` works over
stdio (the machine running the server is yours) and is rejected over
streamable-http (the server may be reachable from a machine that is not
yours, and "this machine" would silently mean the wrong one). Sources that are
already remote — a URL — are unaffected either way. [`mcpo`](https://github.com/open-webui/mcpo)
remains an option too, if you would rather bridge an stdio server than run
this mode.

### Claude Code

```bash
# nothing installed — uvx fetches it
claude mcp add content --env CONTENT_API_URL=http://localhost:8010 -- uvx content-mcp

# or, with the executable installed
claude mcp add content --env CONTENT_API_URL=http://localhost:8010 -- content-mcp
```

### Claude Desktop, Cursor, and other clients

Claude Desktop (`claude_desktop_config.json`), Cursor (`.cursor/mcp.json`) and
any other client using the standard JSON shape:

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

With the executable installed instead, drop the `args` and use
`"command": "content-mcp"`.

Then ask for something like *"analyze this YouTube URL and download the audio
into my library"* — the expected flow is `get_config` → `analyze_source` →
`generate` → `get_job`, ending with a `delivered_path` you can find under the
engine's delivery folder.

Logs go to **stderr** (stdout carries only the MCP JSON-RPC framing), so a
client's log pane shows them without corrupting the session.

## What it supports today

Everything below has been driven over stdio against a running engine, not
inferred from the code.

| You can ask for | Notes |
| --- | --- |
| **A URL** — a video, a playlist, a web page | Media through yt-dlp ([its supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)), pages through the reader |
| **A file on the machine running this server** | Read here and **uploaded** to the engine, which is how a laptop drives a homelab box. `.txt`, `.md`, `.pdf` (its text layer) and media files |
| **video · audio · subtitles** | Quality, codec, container, audio languages, SponsorBlock, clip cutting |
| **transcript · summary · translation · chapters** | The AI-backed ones need a runner — see *what needs a runner* below |
| **thumbnail · keyframes · metadata** | Published artwork, extracted frames, normalized facts |
| **markdown · document_text · pdf** | A page, a document, or a rendering of another output |
| **A whole playlist** | Ask with `scope: "each_item"`: one artifact per member, numbered in order |
| **A destination in the library** | `delivery: {folder, filename}`; each artifact reports its `delivered_path` |
| **The file on your own machine** | `download_artifact`, bounded by `CONTENT_MCP_DOWNLOAD_DIR` |
| **To take an upload back** | `delete_upload` removes bytes sent from this machine before the TTL runs out |
| **Authenticated sources** | `credential` names a cookie file configured on the *server*; the secret never travels |

**What needs a runner.** The engine reports a capability as `unavailable`
rather than failing halfway, so ask `analyze_source` first and believe it.
Summaries, translations and derived chapters need a local
[Ollama](https://ollama.com) or a configured cloud key; transcripts need
existing subtitles, or the optional Whisper runner for audio without them.

## What it does not support

Stated plainly, because finding out by trying is a bad first impression.

| Not available | Why, and what to do instead |
| --- | --- |
| **MCP prompts** | Not provided. The tool descriptions and the server instructions carry the guidance instead. |
| **Live progress** | `get_job` is a status poll (it does suggest a `poll_after` pace, but not progress within a step). The engine has an event stream, but no MCP notification carries it — a long download is opaque until it ends. |
| **Job logs** | Not exposed. `get_job` gives the failing step and its reason, which is what an agent can act on; the raw logs stay on the engine. |
| **Retrying only what failed** | `retry_job` re-runs the whole request. A decision is pending (ADR 0025). |
| **`.docx`, `.epub`, `.odt`, `.rtf`** | Recognised and refused — each needs its own reader. |
| **Scanned PDFs** | The text layer is read; a scan holds an image of words. That needs OCR, which the engine does not implement, and it says so rather than returning nothing. |
| **Video transcoding** | Stream copy and remux only. A format change that requires re-encoding is refused as `option_not_supported`. |
| **Playlist synchronization** | Content downloads a playlist; it does not keep a folder in step with one over time. |
| **Deleting anything but your own upload** | `delete_upload` takes back bytes this server sent; nothing removes an artifact, a job, or a file in the library. Retention for those is an operator concern (ADR 0023, proposed). |
| **Authentication on the engine** | The V1 API has none (ADR 0024). Keep it on a trusted network or behind a reverse proxy — this server inherits whatever reach it has. |

## What is coming

Written down so the gaps read as a plan rather than as neglect. None of it is
implemented; each links to where the decision lives.

- **Retrying only what failed** — a twenty-video playlist with one failure
  should cost one member, not twenty
  ([ADR 0025](../../docs/architecture-decisions/0025-retrying-only-what-failed.md),
  proposed).
- **Playlist synchronization** — keeping a local folder in step with a playlist
  as it changes ([M2](../../docs/roadmap/roadmap.md), the half not yet built).
- **Retention** — reclaiming disk without touching the user's library
  ([ADR 0023](../../docs/architecture-decisions/0023-retention-and-reclaiming-disk.md),
  proposed).
- **More document readers, and OCR** — the formats listed as refused above.

Something you need that is not here? The gap list is the roadmap's front door:
[open an issue](https://github.com/LatentNoise/content/issues).

## Tools (intention-level, not one-per-endpoint)

| Tool | Intent |
| --- | --- |
| `analyze_source` | Analyze a URL: what it is + what can be produced |
| `list_capabilities` | Resolve the capabilities for an analyzed source |
| `generate` | Start a job producing outputs from an `analysis_id`; an output spec may carry `delivery` (`mode`/`folder`/`filename`, ADR 0018) |
| `get_job` | Job status; once terminal, its artifacts — user-facing names (ADR 0017) and `delivered_path` in the server library. Carries `poll_after` (seconds, `null` once terminal): a heuristic for how long to wait before calling again, not a promise |
| `cancel_job` | Cooperative cancellation |
| `retry_job` | Run a finished job's request again, as a new job. The **whole** request — see *What is coming* for the finer version |
| `list_jobs` | Recent jobs |
| `get_artifact` | Artifact metadata; **small text is inlined**, larger/binary returns a download reference (never raw bytes over MCP) |
| `get_config` | Request-building context: credential ids, whether delivery-by-default is on, the existing library folders |

## Resources (read-only, `content://` namespace)

`content://analyses/{id}`, `content://jobs/{id}`, `content://artifacts/{id}` —
JSON views for a host to attach as context. Prompts are intentionally not
provided yet.

## Where downloaded files land

`download_artifact` writes to the machine running this server — the counterpart
to delivery, which writes to the engine's library. One variable bounds it:

| Variable | Default | Role |
| --- | --- | --- |
| `CONTENT_MCP_DOWNLOAD_DIR` | `~/Downloads/Content` | The only directory this server may write to. Relative destinations resolve inside it; anything pointing outside is refused, not clamped |

The refusal is deliberate. An MCP server writes to a real filesystem on an
agent's say-so, so widening that is the operator's decision, taken once, rather
than something a prompt can talk it into.

## When something goes wrong

Every tool translates the SDK's exceptions into something an agent can act on,
because the alternative is what this server used to say when the engine was not
running: `[Errno 61] Connection refused`. It names neither what failed nor what
to do, and it is the **first** thing a new user meets — the engine listens on
`8010` on the host and `8000` only inside its container, so pointing at the
wrong one is the ordinary mistake.

| Situation | What the caller is told |
| --- | --- |
| The engine is not reachable | Which URL was tried, that `docker compose up -d` starts it, that `CONTENT_API_URL` moves it, and the 8010/8000 distinction |
| An analysis has expired | That analyses are kept for a limited time, and to call `analyze_source` again |
| The engine refused the request | The stable error codes (`output_type_not_supported`, …) and the body |
| An output spec is malformed | Caught *before* the round trip, with an example of a correct one |

## Design

- `service.py` — the intention logic; takes an SDK client, returns JSON. No MCP
  imports, no HTTP. Fully unit-tested over a mock transport.
- `server.py` — thin wiring: registers the tools/resources on an `MCPServer` and
  runs stdio. `content-mcp` → `content_mcp.server:main`.
- The layering is enforced by tests: the MCP server may import `content_sdk`
  only — never an HTTP client, never backend internals
  (`tests/test_layering.py` at the repo root).

## Where an uploaded file goes, and for how long

A local path handed to `analyze_source` leaves this machine. The answer says
so, rather than leaving it to documentation nobody opens at that moment:

```json
"upload": {
  "upload_id": "upl_…",
  "filename": "report.pdf",
  "size_bytes": 1583,
  "stored_on": "http://nas.local:8010",
  "retention": "deleted 24h after last use",
  "remove_with": "delete_upload"
}
```

`stored_on` names the engine rather than a path, because the store is
engine-owned and no path here would address it. `retention` is **read from the
engine**, not assumed: the TTL is the operator's setting, and an engine too old
to report it answers `unknown` rather than a comfortable guess — claiming "no
expiry" when the default is 24h would be a falsehood in the reassuring
direction. `get_config` carries the same policy up front, before anything is
sent.

The TTL runs from an upload's **last use**, not its creation, so retrying a job
still finds its input.

## Local files, both directions

A path you give `analyze_source` is a path on the machine running **this
server**, never on the engine: the file is read here and uploaded, which is the
only way a local file becomes usable by an engine running elsewhere. Identical
path strings on two machines do not imply identical filesystems, so the path is
never passed through untouched. That only holds when "this server" is
unambiguous, which is why it is stdio-only: over streamable-http, where the
server may run on a different machine than the caller, a local path is
refused rather than read on the wrong host.

`download_artifact` is the mirror image — it brings a finished artifact back to
this machine, bounded by `CONTENT_MCP_DOWNLOAD_DIR` (see above).

## For development

From a clone:

```bash
make install    # editable installs of the engine, SDK, CLI and MCP in one venv
claude mcp add content --env CONTENT_API_URL=http://localhost:8010 \
  -- apps/backend/.venv/bin/python -m content_mcp.server
```

Build the distributions with `make wheels` (they land in `dist/`).

## Verification status

- Service logic over a mock transport: **verified** (`tests/test_service.py`).
- The MCP wiring against the real `mcp` library (tools, resource templates):
  **verified** (`tests/test_server.py`).
- The full journey — MCP service → SDK → real FastAPI engine → executor →
  delivery library, including `delivery` intent and `mode: "none"`:
  **verified in-process** (`tests/test_end_to_end.py`, in `make validate`).
- The **published wheel** (`uv tool install content-mcp`, 0.6.0 from PyPI)
  driven **over stdio by an MCP client session** against a **running 0.6.0
  engine**: **verified 2026-08-21**. What was actually run, end to end:

  | Path | Result |
  | --- | --- |
  | stdio handshake, `tools/list`, `resources/templates/list` | 9 tools, the three `content://` templates |
  | `get_config` → `analyze_source` → `list_capabilities` → `generate` → `get_job` → `get_artifact` | a web page produced a delivered `markdown` artifact, inlined as text |
  | A real YouTube download | `audio` (opus), 7.5 MB, delivered under its display name |
  | A binary artifact through `get_artifact` | **not** inlined — reference only, as designed |
  | `download_artifact` into `CONTENT_MCP_DOWNLOAD_DIR` | file written on this side |
  | `download_artifact` to a path **outside** it | refused, with the variable named |
  | A playlist with `scope: "each_item"` | 19 entries → 19 artifacts, numbered `001 - …`, one delivered file each |
  | Engine unreachable / wrong port | actionable message (see below) — this is what the run *fixed* |

  Re-run it after any transport change; the in-process suites above never reach
  a closed socket, which is exactly how the error-message defect survived.

