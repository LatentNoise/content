"""Pure contract models + input builders — data only, no client, no behaviour.

These mirror the public contract (SDK-owned; the SDK never imports the engine).
They are deliberately **permissive** (`extra="allow"`) so a backend that adds a
field does not break older SDKs. The behavioural layer lives in ``resources.py``
and must never be imported here — models stay decoupled from the client.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- analysis / capabilities (responses) --------------------------------------


class AnalyzedSource(_Model):
    source_id: str = ""
    resource_key: str = ""
    resource: dict[str, Any] = Field(default_factory=dict)
    media: dict[str, Any] = Field(default_factory=dict)
    streams: list[dict[str, Any]] = Field(default_factory=list)
    subtitles: list[dict[str, Any]] = Field(default_factory=list)
    entries: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def resource_type(self) -> str:
        return str(self.resource.get("resource_type", ""))

    @property
    def title(self) -> str:
        return str(self.resource.get("title", ""))


class AnalysisData(_Model):
    analysis_id: str
    created_at: str = ""
    expires_at: str | None = None
    sources: list[AnalyzedSource] = Field(default_factory=list)


class Capability(_Model):
    id: str
    status: str = ""
    selected_variant: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    reason: dict[str, Any] | None = None

    @property
    def is_offered(self) -> bool:
        """Available or derivable — something the engine can actually produce."""
        return self.status in ("available", "derivable")


class SourceCapabilities(_Model):
    source_id: str = ""
    resource_type: str = ""
    title: str = ""
    capabilities: list[Capability] = Field(default_factory=list)


class CapabilitiesData(_Model):
    analysis_id: str = ""
    sources: list[SourceCapabilities] = Field(default_factory=list)


# --- jobs / artifacts (responses) ---------------------------------------------


class ArtifactData(_Model):
    id: str
    job_id: str = ""
    type: str = ""
    # Technical name inside the job store (stable, id-based).
    filename: str = ""
    # User-facing name computed by the engine (ADR 0017); "" for artifacts
    # registered before the naming engine.
    display_filename: str = ""
    # Where the delivered copy landed, relative to the server's delivery root
    # (ADR 0018); "" when no copy was made.
    delivered_path: str = ""
    media_type: str = ""
    size_bytes: int = 0
    checksum: str = ""
    # Where the artifact came from, and any caveat attached to it: `warnings`
    # holds what a step reported while still succeeding — a summary built from
    # a truncated transcript, for instance. Declared rather than left to
    # `extra="allow"` because callers act on it; the CLI and MCP surface it.
    provenance: dict[str, Any] = Field(default_factory=dict)


class Event(_Model):
    sequence: int = 0
    type: str = ""
    timestamp: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class JobData(_Model):
    job_id: str
    status: str = ""
    error: str = ""
    plan_id: str = ""
    cancel_requested: bool = False
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    # The job detail carries its steps with their status and error; the list
    # endpoint does not, hence the default. Declared rather than left to
    # `extra="allow"` because callers depend on it to explain a failure — the
    # engine records the reason on the step that failed, not on the job.
    steps: list[dict[str, Any]] = Field(default_factory=list)


# --- input builders (requests) ------------------------------------------------
#
# Return canonical contract dicts. Kept generic on purpose: typed options are
# validated by the engine, so the SDK does not re-encode every option schema.


# --- contract vocabulary --------------------------------------------------------

# The one reserved word inside an audio language list (ADR 0022): "the source's
# own audio language", resolved by the engine per resource at plan time. A
# client sends it instead of resolving it, which is the only way to say "each
# video in its own language" about a playlist — members are not analyzed at
# submission, so the answer does not exist client-side.
#
#     outputs.video_output(options={"selection": {"audio_languages": [ORIGINAL, "fr"]}})
#
# Audio language lists accept it; subtitle lists refuse it (422), because "the
# original" has no defined meaning for a translated track.
ORIGINAL = "original"


def url_source(
    uri: str,
    *,
    id: str = "main",
    role: str | None = None,
    credential_id: str | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src: dict[str, Any] = {"id": id, "type": "url", "uri": uri.strip()}
    if role:
        src["role"] = role
    if credential_id:
        src["auth"] = {"credential_id": credential_id}
    if hints:
        src["hints"] = hints
    return src


def file_source(
    path: str, *, id: str = "main", role: str | None = None
) -> dict[str, Any]:
    src: dict[str, Any] = {"id": id, "type": "file", "path": path}
    if role:
        src["role"] = role
    return src


def text_source(
    content: str, *, id: str = "main", mime_type: str = "text/plain"
) -> dict[str, Any]:
    return {"id": id, "type": "text", "content": content, "mime_type": mime_type}


def upload_source(upload_id: str, *, id: str = "main") -> dict[str, Any]:
    return {"id": id, "type": "upload", "upload_id": upload_id}


def output(
    otype: str,
    *,
    id: str | None = None,
    from_sources: list[str] | None = None,
    from_outputs: list[str] | None = None,
    scope: str | None = None,
    required: bool | None = None,
    delivery: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic ArtifactRequest builder. Type-specific helpers below wrap this."""
    out: dict[str, Any] = {"id": id or f"{otype}_main", "type": otype}
    if from_sources:
        out["from_sources"] = from_sources
    if from_outputs:
        out["from_outputs"] = from_outputs
    if scope:
        out["scope"] = scope
    if required is not None:
        out["required"] = required
    if delivery:
        out["delivery"] = delivery
    if options:
        out["options"] = options
    return out


def video_output(**kw: Any) -> dict[str, Any]:
    return output("video", **kw)


def audio_output(**kw: Any) -> dict[str, Any]:
    return output("audio", **kw)


def subtitles_output(**kw: Any) -> dict[str, Any]:
    return output("subtitles", **kw)


def transcript_output(**kw: Any) -> dict[str, Any]:
    return output("transcript", **kw)


def summary_output(**kw: Any) -> dict[str, Any]:
    return output("summary", **kw)


def metadata_output(**kw: Any) -> dict[str, Any]:
    return output("metadata", **kw)


def thumbnail_output(**kw: Any) -> dict[str, Any]:
    return output("thumbnail", **kw)
