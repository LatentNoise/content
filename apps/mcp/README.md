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

## Where downloaded files land

`download_artifact` writes to the machine running this server — the counterpart
to delivery, which writes to the engine's library. One variable bounds it:

| Variable | Default | Role |
| --- | --- | --- |
| `CONTENT_MCP_DOWNLOAD_DIR` | `~/Downloads/Content` | The only directory this server may write to. Relative destinations resolve inside it; anything pointing outside is refused, not clamped |

The refusal is deliberate. An MCP server writes to a real filesystem on an
agent's say-so, so widening that is the operator's decision, taken once, rather
than something a prompt can talk it into.

## Install

The server is an ordinary Python application — nothing to clone. There are two
shapes, and which one you want depends on who should own the lifecycle.

**Let the client fetch it (nothing installed).** `uvx` downloads the server on
first use, caches it and keeps it current, so there is no step to forget and no
version to upgrade by hand. This is the shortest path and the one to prefer:

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

The trade-off is only who updates it: `uvx` follows PyPI, an installed tool
stays where you put it until `uv tool upgrade content-mcp`. Both run the same
wheel.

[`content-mcp` on PyPI](https://pypi.org/project/content-mcp/) pulls
[`content-sdk`](https://pypi.org/project/content-sdk/) as an ordinary
dependency, pinned to the matching release. The wheels are also attached to
each [GitHub release](https://github.com/LatentNoise/content/releases/latest)
for air-gapped installs (`uv tool install ./content_mcp-<v>-py3-none-any.whl
--find-links .`).

## Connect it to your engine

One environment variable: `CONTENT_API_URL` (default `http://localhost:8010`).
The server speaks stdio — your MCP client spawns it; you never run it by hand.

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

## Tools (intention-level, not one-per-endpoint)

| Tool | Intent |
| --- | --- |
| `analyze_source` | Analyze a URL: what it is + what can be produced |
| `list_capabilities` | Resolve the capabilities for an analyzed source |
| `generate` | Start a job producing outputs from an `analysis_id`; an output spec may carry `delivery` (`mode`/`folder`/`filename`, ADR 0018) |
| `get_job` | Job status; once terminal, its artifacts — user-facing names (ADR 0017) and `delivered_path` in the server library |
| `cancel_job` | Cooperative cancellation |
| `list_jobs` | Recent jobs |
| `get_artifact` | Artifact metadata; **small text is inlined**, larger/binary returns a download reference (never raw bytes over MCP) |
| `get_config` | Request-building context: credential ids, whether delivery-by-default is on, the existing library folders |

## Resources (read-only, `content://` namespace)

`content://analyses/{id}`, `content://jobs/{id}`, `content://artifacts/{id}` —
JSON views for a host to attach as context. Prompts are intentionally not
provided yet.

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

## Local files, both directions

A path you give `analyze_source` is a path on the machine running **this
server**, never on the engine: the file is read here and uploaded, which is the
only way a local file becomes usable by an engine running elsewhere. Identical
path strings on two machines do not imply identical filesystems, so the path is
never passed through untouched.

`download_artifact` is the mirror image — it brings a finished artifact back to
this machine, bounded by `CONTENT_MCP_DOWNLOAD_DIR` (see above).
