# content-mcp

The official **MCP server** for the Content engine — an agentic facade that lets
any MCP-compatible client (Claude, IDEs, agents) drive Content. It speaks only
through the [SDK](../../packages/python-sdk/README.md): **no REST, no business
logic** lives here.

```text
Content Backend (REST) → content_sdk → content-mcp (this) → any MCP agent
```

## Run (stdio)

```bash
pip install content-mcp
CONTENT_API_URL=http://localhost:8010 content-mcp   # stdio transport
```

Example MCP client config:

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

## Test it with an agent

With the engine running (`docker compose up`, port 8010), register the server
in Claude Code from the repo root:

```bash
claude mcp add content --env CONTENT_API_URL=http://localhost:8010 \
  -- apps/backend/.venv/bin/python -m content_mcp.server
```

Then ask for something like *"analyze this YouTube URL and download the audio
into my library"* — the expected flow is `get_config` → `analyze_source` →
`generate` → `get_job`, ending with a `delivered_path` you can find under the
mounted delivery folder.

## Verification status

- Service logic over a mock transport: **verified** (`tests/test_service.py`).
- The MCP wiring against the real `mcp` library (tools, resource templates):
  **verified** (`tests/test_server.py`).
- The full journey — MCP service → SDK → real FastAPI engine → executor →
  delivery library, including `delivery` intent and `mode: "none"`:
  **verified in-process** (`tests/test_end_to_end.py`, in `make validate`).
- An interactive MCP host (Claude) driving the stdio transport against a
  running engine: **NOT verified here** — that is the manual test above.

## Design

- `service.py` — the intention logic; takes an SDK client, returns JSON. No MCP
  imports, no HTTP. Fully unit-tested over a mock transport.
- `server.py` — thin wiring: registers the tools/resources on an `MCPServer` and
  runs stdio. `content-mcp` → `content_mcp.server:main`.
