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

The server is an ordinary Python application — nothing to clone:

```bash
uv tool install content-mcp     # isolated, on your PATH — recommended
content-mcp --help

# or
pipx install content-mcp
```

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
claude mcp add content --env CONTENT_API_URL=http://localhost:8010 -- content-mcp
```

### Claude Desktop, Cursor, and other clients

Claude Desktop (`claude_desktop_config.json`), Cursor (`.cursor/mcp.json`) and
any other client using the standard JSON shape:

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
- The installed wheel driven over stdio by an MCP client session against a
  running engine: **verified** at packaging time (see the repository's
  release notes); re-run it after any transport change.

## Design

- `service.py` — the intention logic; takes an SDK client, returns JSON. No MCP
  imports, no HTTP. Fully unit-tested over a mock transport.
- `server.py` — thin wiring: registers the tools/resources on an `MCPServer` and
  runs stdio. `content-mcp` → `content_mcp.server:main`.
- The layering is enforced by tests: the MCP server may import `content_sdk`
  only — never an HTTP client, never backend internals
  (`tests/test_layering.py` at the repo root).
