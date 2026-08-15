"""Intention-level operations over the Content SDK — the MCP server's logic.

These pure functions take a ``content_sdk`` client and translate an agent's
intent into SDK calls, returning plain JSON-able dicts. They contain **no** MCP
types and **no** HTTP — the SDK is the only door to the engine. ``server.py``
merely exposes them as MCP tools/resources.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from content_sdk import ContentClient, outputs

# get_artifact never streams large binaries over MCP (refinement 6): only small
# text artifacts are inlined; everything else returns metadata + a reference.
MAX_INLINE_BYTES = 256 * 1024
_INLINE_TEXT_TYPES = {"application/json", "application/x-subrip", "text/vtt"}


def _is_inlineable_text(media_type: str) -> bool:
    return media_type.startswith("text/") or media_type in _INLINE_TEXT_TYPES


# Every key an output spec may carry. Anything else is a mistake worth
# refusing: the engine's own models forbid unknown fields, and this layer
# used to read the keys it recognised and drop the rest — so an agent that
# flattened `{"type": "subtitles", "languages": ["es"]}` instead of nesting
# them under `options` got a *successful* job producing English subtitles.
# Silently answering a question nobody asked is the worst failure mode for a
# caller that cannot see the result, so the leniency is gone.
_OUTPUT_KEYS = frozenset(
    {
        "type",
        "id",
        "from_sources",
        "from_outputs",
        "scope",
        "required",
        "delivery",
        "options",
    }
)


def _output_from_spec(spec: Any) -> dict[str, Any]:
    """Accept a plain type string or a {type, options, from_sources, ...} dict.

    Rejects anything else with a message that shows the correct shape: an
    agent's next attempt should succeed on what it reads here, not on a
    second guess.
    """
    if isinstance(spec, str):
        return outputs.output(spec)
    if not isinstance(spec, dict):
        raise TypeError(
            f"an output must be a type string or an object, got {type(spec).__name__}. "
            'Example: {"type": "subtitles", "options": {"languages": ["es"]}}'
        )
    if "type" not in spec:
        raise ValueError(
            f"this output has no 'type' (keys: {sorted(spec)}). "
            'Example: {"type": "subtitles", "options": {"languages": ["es"]}}'
        )
    unknown = sorted(set(spec) - _OUTPUT_KEYS)
    if unknown:
        raise ValueError(
            f"unknown key(s) {unknown} on the '{spec['type']}' output. "
            "Per-output settings belong under 'options' — for example "
            '{"type": "subtitles", "options": {"languages": ["es"]}}, not '
            '{"type": "subtitles", "languages": ["es"]}. '
            f"Allowed keys: {sorted(_OUTPUT_KEYS)}."
        )
    return outputs.output(
        spec["type"],
        id=spec.get("id"),
        from_sources=spec.get("from_sources"),
        from_outputs=spec.get("from_outputs"),
        scope=spec.get("scope"),
        required=spec.get("required"),
        # {mode, folder, filename} — where/how the artifact is delivered into
        # the server library (ADR 0018); omitted = the server policy decides.
        delivery=spec.get("delivery"),
        options=spec.get("options"),
    )


# --- tools --------------------------------------------------------------------


def analyze_source(
    client: ContentClient, url: str, credential: str | None = None
) -> dict[str, Any]:
    """Analyze a URL and report what it is + what can be produced from it."""
    analysis = client.analyze(outputs.url_source(url, credential_id=credential))
    caps = client.get_capabilities(analysis.id)
    entry = analysis.sources[0]
    return {
        "analysis_id": analysis.id,
        "resource_type": entry.resource_type,
        "title": entry.title,
        "entries": len(entry.entries),
        "capabilities": [
            {"id": c.id, "status": c.status} for c in caps.sources[0].capabilities
        ],
    }


def list_capabilities(client: ContentClient, analysis_id: str) -> dict[str, Any]:
    """Resolve the public capabilities for a previously analyzed source."""
    caps = client.get_capabilities(analysis_id)
    src = caps.sources[0]
    return {
        "analysis_id": caps.analysis_id,
        "source_id": src.source_id,
        "capabilities": [
            {"id": c.id, "status": c.status, "reason": c.reason}
            for c in src.capabilities
        ],
    }


def generate(
    client: ContentClient, analysis_id: str, outputs_spec: list[Any]
) -> dict[str, Any]:
    """Start a generation job for an analyzed source. `outputs_spec` items are
    either a type string ("audio") or a dict {type, options, ...}."""
    job = client.generate(analysis_id, [_output_from_spec(s) for s in outputs_spec])
    return {"job_id": job.id, "status": job.status}


def get_job(client: ContentClient, job_id: str) -> dict[str, Any]:
    """Job status; once terminal, the produced artifacts (metadata only), and
    when something went wrong, what went wrong.

    The engine records a failure on the *step* that failed; the job's own
    ``error`` is usually empty. Reporting only that left an agent with
    ``{"status": "failed", "error": ""}`` — enough to know it should stop,
    nothing to tell the user or to decide whether a different request would
    work. Failures now travel with their reason.
    """
    job = client.get_job(job_id)
    result: dict[str, Any] = {
        "job_id": job.id,
        "status": job.status,
        "error": job.data.error,
    }
    if job.status in ("failed", "partially_succeeded"):
        failures = [
            {
                "step": step.get("step_id", ""),
                "operation": step.get("operation", ""),
                "error": step.get("error", ""),
            }
            for step in (job.data.steps or [])
            if step.get("status") == "failed"
        ]
        if failures:
            result["failures"] = failures
            # The job-level field is what a caller reads first; when the
            # engine left it empty, the first real reason belongs there.
            if not result["error"]:
                result["error"] = failures[0]["error"]
    if job.is_terminal:
        result["artifacts"] = [
            {
                "id": a.id,
                "type": a.type,
                # The name to show a user (ADR 0017); the technical job-store
                # name is an implementation detail agents don't need.
                "filename": a.display_filename or a.filename,
                # Where the file landed in the server's delivery library,
                # relative to its root ("" = no delivered copy) — ADR 0018.
                "delivered_path": a.delivered_path,
                "media_type": a.media_type,
                "size_bytes": a.size_bytes,
            }
            for a in client.artifacts(job_id)
        ]
    return result


def cancel_job(client: ContentClient, job_id: str) -> dict[str, Any]:
    """Request cooperative cancellation of a job."""
    return client.cancel(job_id)


def get_config(client: ContentClient) -> dict[str, Any]:
    """What an agent needs to parameterize requests: the credential ids for
    authenticated sources, whether artifacts are delivered into the server
    library by default, and the existing library folders to choose from."""
    config = client.config()
    return {
        "credentials": config.get("credentials", []),
        "delivery_by_default": config.get("delivery", {}).get("by_default", False),
        "folders": client.folders(),
        "language": config.get("language", {}),
    }


def list_jobs(client: ContentClient, limit: int = 20) -> dict[str, Any]:
    """Recent jobs (most recent first)."""
    return {
        "jobs": [
            {"job_id": j.job_id, "status": j.status}
            for j in client.list_jobs(limit=limit)
        ]
    }


def get_artifact(client: ContentClient, artifact_id: str) -> dict[str, Any]:
    """Artifact metadata; inline the content ONLY for small text artifacts,
    otherwise return a download reference (never large binaries over MCP)."""
    art = client.get_artifact(artifact_id)
    meta = {
        "id": art.id,
        "type": art.type,
        "filename": art.display_filename or art.filename,
        "delivered_path": art.delivered_path,
        "media_type": art.media_type,
        "size_bytes": art.size_bytes,
    }
    if _is_inlineable_text(art.media_type) and art.size_bytes <= MAX_INLINE_BYTES:
        text = client.artifact_bytes(artifact_id).decode("utf-8", errors="replace")
        return {**meta, "inlined": True, "content": text}
    return {
        **meta,
        "inlined": False,
        "download_path": f"/api/v1/artifacts/{artifact_id}/content",
        "note": (
            f"not inlined (binary or larger than {MAX_INLINE_BYTES} bytes); "
            "fetch the bytes via the API download path"
        ),
    }


# --- resources (read-only JSON) -----------------------------------------------


def analysis_resource(client: ContentClient, analysis_id: str) -> dict[str, Any]:
    return client.get_analysis(analysis_id).data.model_dump()


def job_resource(client: ContentClient, job_id: str) -> dict[str, Any]:
    return client.get_job(job_id).data.model_dump()


def artifact_resource(client: ContentClient, artifact_id: str) -> dict[str, Any]:
    return client.get_artifact(artifact_id).model_dump()


# --- bringing a file to the caller's machine ----------------------------------

DOWNLOAD_DIR_ENV = "CONTENT_MCP_DOWNLOAD_DIR"
DEFAULT_DOWNLOAD_DIR = "~/Downloads/Content"


def download_root() -> Path:
    """Where this MCP server is allowed to write, expanded and absolute."""
    raw = os.getenv(DOWNLOAD_DIR_ENV, "").strip() or DEFAULT_DOWNLOAD_DIR
    return Path(raw).expanduser().resolve()


def _resolve_destination(root: Path, destination: str | None, filename: str) -> Path:
    """A path inside *root*, or a refusal.

    The MCP server writes to the user's own filesystem on an agent's say-so, so
    the destination is confined to one directory the operator chose. Relative
    paths resolve inside it; anything that escapes — an absolute path elsewhere,
    a `..` climb, a symlink out — is refused rather than clamped, because
    silently rewriting where a file went is worse than not writing it.
    """
    candidate = Path(destination).expanduser() if destination else root / filename
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_dir():
        candidate = candidate / filename
    resolved = (candidate.parent.resolve() / candidate.name).absolute()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"destination is outside {DOWNLOAD_DIR_ENV} ({root}). Pass a path "
            f"inside it, or set {DOWNLOAD_DIR_ENV} to widen what this server "
            "may write to."
        )
    return resolved


def download_artifact(
    client: ContentClient, artifact_id: str, destination: str | None = None
) -> dict[str, Any]:
    """Copy an artifact from the engine onto the machine running this server."""
    artifact = client.get_artifact(artifact_id)
    # The engine sanitizes names, but this writes to a real filesystem on an
    # agent's request — take the basename regardless of what came back.
    filename = Path(artifact.display_filename or artifact.filename).name
    root = download_root()
    root.mkdir(parents=True, exist_ok=True)
    target = _resolve_destination(root, destination, filename)
    written = client.download_artifact(artifact_id, target)
    return {
        "path": str(written),
        "filename": written.name,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "note": (
            "Saved on the machine running this MCP server. The engine's own "
            "copy in its library (delivered_path) is unaffected."
        ),
    }
