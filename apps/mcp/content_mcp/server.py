"""The official Content MCP server — a thin agentic facade over the SDK.

It exposes **intention-level** Tools (not one-per-endpoint) and read-only
Resources, each delegating to ``service.py`` which speaks only the SDK. No REST,
no business logic here. stdio transport (HTTP can come later).
"""

from __future__ import annotations

from typing import Any

from content_sdk import ContentClient
from mcp.server import MCPServer

from . import service

INSTRUCTIONS = (
    "Content turns sources (a URL, a file) into artifacts (video, audio, "
    "subtitles, transcript, summary…). Typical flow: analyze_source to see what "
    "a source is and what can be produced, then generate with the returned "
    "analysis_id, then poll get_job until it is terminal and read its artifacts. "
    "The engine names every artifact after the analyzed resource; when the "
    "server's delivery policy is on (see get_config), finished files are also "
    "copied into the server's media library and each artifact reports its "
    "delivered_path there. An output spec may carry delivery "
    '{"mode": "inherit|deliver|none", "folder": …, "filename": …} to steer this.'
)


def build_server(client: ContentClient | None = None) -> MCPServer:
    client = client or ContentClient()
    server = MCPServer(name="content", version="0.3.0", instructions=INSTRUCTIONS)

    # --- tools (intention-level) ---------------------------------------------
    @server.tool()
    def analyze_source(url: str, credential: str | None = None) -> dict[str, Any]:
        """Analyze a source URL: report what it is and what can be produced."""
        return service.analyze_source(client, url, credential=credential)

    @server.tool()
    def list_capabilities(analysis_id: str) -> dict[str, Any]:
        """Resolve the public capabilities available for an analyzed source."""
        return service.list_capabilities(client, analysis_id)

    @server.tool()
    def generate(analysis_id: str, outputs: list[Any]) -> dict[str, Any]:
        """Start a job producing the requested outputs from an analyzed source.
        Each output is a type string ("audio") or a {type, options, ...} dict."""
        return service.generate(client, analysis_id, outputs)

    @server.tool()
    def get_job(job_id: str) -> dict[str, Any]:
        """Get a job's status and, once terminal, its produced artifacts."""
        return service.get_job(client, job_id)

    @server.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of a running job."""
        return service.cancel_job(client, job_id)

    @server.tool()
    def list_jobs(limit: int = 20) -> dict[str, Any]:
        """List recent jobs (most recent first)."""
        return service.list_jobs(client, limit)

    @server.tool()
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        """Artifact metadata; small text artifacts are inlined, larger/binary
        ones return a download reference (never raw bytes over MCP)."""
        return service.get_artifact(client, artifact_id)

    @server.tool()
    def get_config() -> dict[str, Any]:
        """Server-side context for building requests: credential ids for
        authenticated sources, whether delivery-by-default is on, and the
        existing library folders."""
        return service.get_config(client)

    # --- resources (read-only, single content:// namespace) -------------------
    @server.resource("content://analyses/{analysis_id}")
    def analysis_resource(analysis_id: str) -> dict[str, Any]:
        return service.analysis_resource(client, analysis_id)

    @server.resource("content://jobs/{job_id}")
    def job_resource(job_id: str) -> dict[str, Any]:
        return service.job_resource(client, job_id)

    @server.resource("content://artifacts/{artifact_id}")
    def artifact_resource(artifact_id: str) -> dict[str, Any]:
        return service.artifact_resource(client, artifact_id)

    return server


_HELP = """\
content-mcp — the official MCP server for the Content engine (stdio).

Run it from an MCP client, not by hand: the client spawns the process and
speaks JSON-RPC over stdin/stdout. Configuration is one environment variable:

  CONTENT_API_URL   base URL of your Content engine (default http://localhost:8010)

Example client configuration:

  {
    "mcpServers": {
      "content": {
        "command": "content-mcp",
        "env": { "CONTENT_API_URL": "http://localhost:8010" }
      }
    }
  }

Options:
  --help      show this help and exit
  --version   show the version and exit
"""


def main(argv: list[str] | None = None) -> None:
    # A stdio MCP server owns stdout for JSON-RPC framing, so the only argv
    # handling is the pair every tool owes its installer — and both exit
    # before the transport starts. Anything else is a mistake worth stopping
    # on (an MCP client passes no arguments).
    import sys

    args = sys.argv[1:] if argv is None else argv
    if "--help" in args or "-h" in args:
        print(_HELP, end="")
        return
    if "--version" in args:
        # The wheel's own metadata, so `make version-update` has a single
        # declaration to rewrite here (the MCPServer version= literal).
        from importlib.metadata import PackageNotFoundError, version

        try:
            print(f"content-mcp {version('content-mcp')}")
        except PackageNotFoundError:
            print("content-mcp (uninstalled source tree)")
        return
    if args:
        print(f"content-mcp: unexpected argument {args[0]!r} (try --help)")
        raise SystemExit(2)
    build_server().run("stdio")


if __name__ == "__main__":
    main()
