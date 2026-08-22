"""The official Content MCP server — a thin agentic facade over the SDK.

It exposes **intention-level** Tools (not one-per-endpoint) and read-only
Resources, each delegating to ``service.py`` which speaks only the SDK. No REST,
no business logic here. stdio transport (HTTP can come later).
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from content_sdk import ContentClient
from content_sdk.errors import (
    APIError,
    ContentError,
    Gone,
    NotFound,
    TransportError,
    ValidationError,
)
from mcp.server import MCPServer

from . import service

INSTRUCTIONS = (
    "Content turns sources (a URL, a local file, text) into artifacts (video, "
    "audio, "
    "subtitles, transcript, summary…). Typical flow: analyze_source to see what "
    "a source is and what can be produced, then generate with the returned "
    "analysis_id, then poll get_job until it is terminal and read its artifacts. "
    "The engine names every artifact after the analyzed resource; when the "
    "server's delivery policy is on (see get_config), finished files are also "
    "copied into the server's media library and each artifact reports its "
    "delivered_path there. An output spec may carry delivery "
    '{"mode": "inherit|deliver|none", "folder": …, "filename": …} to steer this. '
    "Do steer it: get_config lists the library's existing folders, so choose "
    "one that fits what the user asked for (or ask them which) rather than "
    "letting everything pile up in the library root, and tell them the "
    "delivered_path in your answer — 'saved to Tech/…' is the useful reply, an "
    "artifact id is not. "
    "Note that the engine's library lives on the *engine's* machine. When the "
    "user wants the file on the machine you are running on — often the case "
    "when the engine is a homelab or NAS — use download_artifact, which is the "
    "only way bytes reach this side. "
    "A source whose resource_type is 'collection' (a playlist) is a special "
    "case worth knowing: its capabilities describe the collection itself, so "
    "media outputs read as unavailable there. To produce something for every "
    "member, ask for the output with scope 'each_item' — the engine analyzes "
    "and plans each member on its own, and the job returns one artifact per "
    "member, numbered in order. "
    "A path you give analyze_source is a path on the machine running THIS "
    "server: the file is read here and uploaded to the engine, which is the "
    "only way a local file becomes usable by an engine running elsewhere. "
    "Never assume the engine can see your paths."
)


def _actionable(exc: ContentError, base_url: str) -> str:
    """Turn an SDK exception into a sentence the agent — and the person reading
    over its shoulder — can act on.

    Without this, an engine that is not running answers `[Errno 61] Connection
    refused`. That is the first thing a new user meets when they get the port
    wrong (the container listens on 8000 inside and 8010 on the host), and it
    names neither what failed nor what to do. The failure is not the engine's;
    the unusable message is ours.
    """
    if isinstance(exc, TransportError):
        return (
            f"The Content engine is not reachable at {base_url}. Start it "
            "(`docker compose up -d`), or set CONTENT_API_URL to where it runs "
            "— note the engine listens on port 8010 on the host, 8000 only "
            f"inside its container. ({exc})"
        )
    if isinstance(exc, Gone):
        return (
            f"That reference has expired ({base_url}): analyses are kept for a "
            "limited time. Call analyze_source again to get a fresh "
            f"analysis_id. ({'; '.join(exc.codes) or exc})"
        )
    if isinstance(exc, NotFound):
        return f"The engine has no such record. ({'; '.join(exc.codes) or exc})"
    if isinstance(exc, ValidationError):
        codes = "; ".join(exc.codes)
        return (
            "The engine refused this request"
            + (f" ({codes})" if codes else "")
            + f". {exc.body}"
        )
    if isinstance(exc, APIError):
        return f"The engine answered HTTP {exc.status}. {exc.body}"
    return str(exc)


def _friendly(fn, client: ContentClient):
    """Wrap one tool so SDK errors surface as guidance rather than errno."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ContentError as exc:
            raise RuntimeError(_actionable(exc, client.base_url)) from exc

    return wrapper


def build_server(client: ContentClient | None = None) -> MCPServer:
    client = client or ContentClient()
    server = MCPServer(name="content", version="0.6.5", instructions=INSTRUCTIONS)

    def tool():
        """`server.tool()`, with SDK errors translated on the way out."""

        def register(fn):
            return server.tool()(_friendly(fn, client))

        return register

    def resource(uri: str):
        def register(fn):
            return server.resource(uri)(_friendly(fn, client))

        return register

    # --- tools (intention-level) ---------------------------------------------
    @tool()
    def analyze_source(url: str, credential: str | None = None) -> dict[str, Any]:
        """Analyze a source and report what can be produced from it.

        Accepts a URL, or **a path to a file on this machine** — the one this
        MCP server runs on. A local file is read and uploaded to the engine,
        which is the only way a file here becomes usable by an engine running
        elsewhere; the path is never assumed to exist on the engine's side.
        """
        return service.analyze_source(client, url, credential=credential)

    @tool()
    def list_capabilities(analysis_id: str) -> dict[str, Any]:
        """Resolve the public capabilities available for an analyzed source."""
        return service.list_capabilities(client, analysis_id)

    @tool()
    def generate(analysis_id: str, outputs: list[Any]) -> dict[str, Any]:
        """Start a job producing the requested outputs from an analyzed source.

        Each output is either a type string — ["video", "subtitles"] — or an
        object. Per-output settings go under "options", never beside "type":

            {"type": "subtitles", "options": {"languages": ["es"]}}
            {"type": "video", "options": {"selection": {"max_height": 1080}},
             "delivery": {"mode": "deliver", "folder": "Talks"}}

        Other keys of an output object: id, from_sources, from_outputs, scope
        ("single" or "each_item" for a collection), required. An unknown key
        is refused rather than ignored.
        """
        return service.generate(client, analysis_id, outputs)

    @tool()
    def get_job(job_id: str) -> dict[str, Any]:
        """Get a job's status and, once terminal, its produced artifacts."""
        return service.get_job(client, job_id)

    @tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of a running job."""
        return service.cancel_job(client, job_id)

    @tool()
    def list_jobs(limit: int = 20) -> dict[str, Any]:
        """List recent jobs (most recent first)."""
        return service.list_jobs(client, limit)

    @tool()
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        """Artifact metadata; small text artifacts are inlined, larger/binary
        ones return a download reference (never raw bytes over MCP)."""
        return service.get_artifact(client, artifact_id)

    @tool()
    def download_artifact(
        artifact_id: str, destination: str | None = None
    ) -> dict[str, Any]:
        """Save an artifact onto the machine running this MCP server.

        Use this when the user wants the file *here* rather than only in the
        engine's library — typically when the engine runs on another machine.
        `destination` is optional: omitted, the artifact keeps its own name in
        the server's download directory (CONTENT_MCP_DOWNLOAD_DIR, default
        ~/Downloads/Content). A path outside that directory is refused.
        """
        return service.download_artifact(client, artifact_id, destination)

    @tool()
    def retry_job(job_id: str) -> dict[str, Any]:
        """Run a finished job's request again, as a new job.

        Re-runs the *whole* request: a playlist where one member failed
        re-downloads every member. Worth it for a transient failure, wasteful
        for a large collection — read the failure in `get_job` first.
        """
        return service.retry_job(client, job_id)

    @tool()
    def delete_upload(upload_id: str) -> dict[str, Any]:
        """Delete bytes uploaded from this machine, before the TTL expires.

        `analyze_source` uploads a local file to the engine; the id it returns
        under `upload` is what this takes. Removes only that upload — never an
        artifact, never a file in the library.
        """
        return service.delete_upload(client, upload_id)

    @tool()
    def get_config() -> dict[str, Any]:
        """Server-side context for building requests: credential ids for
        authenticated sources, whether delivery-by-default is on, and the
        existing library folders."""
        return service.get_config(client)

    # --- resources (read-only, single content:// namespace) -------------------
    @resource("content://analyses/{analysis_id}")
    def analysis_resource(analysis_id: str) -> dict[str, Any]:
        return service.analysis_resource(client, analysis_id)

    @resource("content://jobs/{job_id}")
    def job_resource(job_id: str) -> dict[str, Any]:
        return service.job_resource(client, job_id)

    @resource("content://artifacts/{artifact_id}")
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
