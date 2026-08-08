"""Intention-level operations over the Content SDK — the MCP server's logic.

These pure functions take a ``content_sdk`` client and translate an agent's
intent into SDK calls, returning plain JSON-able dicts. They contain **no** MCP
types and **no** HTTP — the SDK is the only door to the engine. ``server.py``
merely exposes them as MCP tools/resources.
"""

from __future__ import annotations

from typing import Any

from content_sdk import ContentClient, outputs

# get_artifact never streams large binaries over MCP (refinement 6): only small
# text artifacts are inlined; everything else returns metadata + a reference.
MAX_INLINE_BYTES = 256 * 1024
_INLINE_TEXT_TYPES = {"application/json", "application/x-subrip", "text/vtt"}


def _is_inlineable_text(media_type: str) -> bool:
    return media_type.startswith("text/") or media_type in _INLINE_TEXT_TYPES


def _output_from_spec(spec: Any) -> dict[str, Any]:
    """Accept a plain type string or a {type, options, from_sources, ...} dict."""
    if isinstance(spec, str):
        return outputs.output(spec)
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
    """Job status; once terminal, the produced artifacts (metadata only)."""
    job = client.get_job(job_id)
    result: dict[str, Any] = {
        "job_id": job.id,
        "status": job.status,
        "error": job.data.error,
    }
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
