"""The synchronous Content client — the official way to speak to the engine.

Wraps every `/api/v1` endpoint, returns natural Python objects, and maps errors
to typed exceptions. `analyze/get_capabilities/generate` accept **either** an
`analysis_id`/`Analysis` **or** inline sources (ADR 0014).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from ._transport import DEFAULT_TIMEOUT, RetryConfig, SyncTransport, resolve_base_url
from .models import (
    SCHEMA_VERSION,
    AnalysisData,
    ArtifactData,
    CapabilitiesData,
    Event,
    JobData,
    upload_source,
)
from .resources import Analysis, Job

# Accepts an Analysis, an analysis_id string, a single source dict, or a list of
# source dicts — resolved to the request fragment the API expects.
SourceInput = "Analysis | str | dict[str, Any] | list[dict[str, Any]]"


def _sources_list(sources: Any) -> list[dict[str, Any]]:
    return [sources] if isinstance(sources, dict) else list(sources)


def _source_body(target: Any) -> dict[str, Any]:
    """Map a target to the `sources` XOR `analysis_id` request fragment."""
    if isinstance(target, Analysis):
        return {"analysis_id": target.id}
    if isinstance(target, str):
        return {"analysis_id": target}
    return {"sources": _sources_list(target)}


class ContentClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryConfig | None = None,
        http_client=None,
    ):
        self._t = SyncTransport(
            resolve_base_url(base_url),
            timeout,
            retry or RetryConfig(),
            client=http_client,
        )

    @property
    def base_url(self) -> str:
        return self._t.base_url

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- system / observability ------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._t.get("/health")

    def system(self) -> dict[str, Any]:
        return self._t.get("/system")

    def config(self) -> dict[str, Any]:
        return self._t.get("/config")

    def catalog(self) -> dict[str, Any]:
        return self._t.get("/catalog")

    def storage(self) -> dict[str, Any]:
        return self._t.get("/storage")

    def cache(self) -> dict[str, Any]:
        return self._t.get("/cache")

    def purge_cache(self) -> dict[str, Any]:
        return self._t.post("/cache/purge")

    def notifications(self) -> list[dict[str, Any]]:
        """What the instance wants to tell its operator (a newer release, a
        stale yt-dlp). The engine decides what is worth saying; see
        ``content_sdk.notifications`` for the UI-side helpers."""
        return self._t.get("/notifications").get("notifications", [])

    def folders(self) -> list[str]:
        return self._t.get("/folders").get("folders", [])

    # --- uploads (ADR 0020) ------------------------------------------------------

    def upload(self, path: Path | str, *, media_type: str = "") -> dict[str, Any]:
        """Send a local file to the engine and return its upload record."""
        return self._t.post_file("/uploads", Path(path), media_type=media_type)

    def upload_file(
        self, path: Path | str, *, id: str = "main", media_type: str = ""
    ) -> dict[str, Any]:
        """Upload a local file and return a **source ready to use**.

        The ergonomic path, and the one callers should reach for:

            source = client.upload_file("~/report.pdf")
            analysis = client.analyze(source)

        The upload id never has to be handled by hand. Note the direction: this
        sends bytes *to* the engine, which is the only way a file on your
        machine becomes usable by an engine running somewhere else.
        """
        record = self.upload(path, media_type=media_type)
        return upload_source(record["upload_id"], id=id)

    def get_upload(self, upload_id: str) -> dict[str, Any]:
        return self._t.get(f"/uploads/{upload_id}")

    def delete_upload(self, upload_id: str) -> None:
        self._t.request("DELETE", f"/uploads/{upload_id}")

    # --- analysis / capabilities -----------------------------------------------

    def analyze(self, sources: Any) -> Analysis:
        data = self._t.post("/analyses", {"sources": _sources_list(sources)})
        return Analysis(AnalysisData.model_validate(data), self)

    def get_analysis(self, analysis_id: str) -> Analysis:
        data = self._t.get(f"/analyses/{analysis_id}")
        return Analysis(AnalysisData.model_validate(data), self)

    def get_capabilities(
        self, target: Any, constraints: dict[str, Any] | None = None
    ) -> CapabilitiesData:
        body = _source_body(target)
        if constraints:
            body["constraints"] = constraints
        data = self._t.post("/capabilities", body)
        return CapabilitiesData.model_validate(data)

    def generate(
        self,
        target: Any,
        outputs: Any,
        *,
        preferences: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Job:
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "outputs": [outputs] if isinstance(outputs, dict) else list(outputs),
            **_source_body(target),
        }
        for key, value in (
            ("preferences", preferences),
            ("constraints", constraints),
            ("execution", execution),
            ("metadata", metadata),
        ):
            if value:
                body[key] = value
        # Retry POST /jobs only when the caller made it idempotent (refinement 4).
        idempotent = bool(execution and execution.get("idempotency_key"))
        data = self._t.post("/jobs", body, idempotent=idempotent)
        return Job(JobData.model_validate(data), self)

    def submit(self, request: dict[str, Any]) -> Job:
        """Submit a pre-built GenerationRequest (raw contract dict) — the escape
        hatch for full control; `generate()` is the ergonomic path."""
        idempotent = bool((request.get("execution") or {}).get("idempotency_key"))
        data = self._t.post("/jobs", request, idempotent=idempotent)
        return Job(JobData.model_validate(data), self)

    # --- jobs -------------------------------------------------------------------

    def list_jobs(self, *, status: str | None = None, limit: int = 30) -> list[JobData]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        rows = self._t.get("/jobs", params=params)
        return [JobData.model_validate(r) for r in rows]

    def get_job(self, job_id: str) -> Job:
        return Job(JobData.model_validate(self._t.get(f"/jobs/{job_id}")), self)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._t.post(f"/jobs/{job_id}/cancel")

    def retry(self, job_id: str) -> Job:
        return Job(JobData.model_validate(self._t.post(f"/jobs/{job_id}/retry")), self)

    def events(self, job_id: str, after_sequence: int = 0) -> list[Event]:
        rows = self._t.get(
            f"/jobs/{job_id}/events", params={"after_sequence": after_sequence}
        )
        return [Event.model_validate(r) for r in rows]

    def logs(self, job_id: str, tail: int = 400) -> dict[str, Any]:
        return self._t.get(f"/jobs/{job_id}/logs", params={"tail": tail})

    # --- artifacts --------------------------------------------------------------

    def artifacts(self, job_id: str) -> list[ArtifactData]:
        rows = self._t.get(f"/jobs/{job_id}/artifacts")
        return [ArtifactData.model_validate(r) for r in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactData:
        return ArtifactData.model_validate(self._t.get(f"/artifacts/{artifact_id}"))

    def artifact_bytes(self, artifact_id: str) -> bytes:
        return self._t.content(f"/artifacts/{artifact_id}/content")

    def download_artifact(self, artifact_id: str, destination: Path | str) -> Path:
        """Save an artifact to a local file, streaming it.

        ``destination`` may be a directory — the artifact's display name is
        used inside it — or an explicit file path. Returns the path written.

        This is the counterpart to delivery: delivery puts a copy in the
        *engine's* library, this puts one on the *caller's* machine, which for
        a remote engine is otherwise a manual scp.
        """
        target = Path(destination)
        if target.is_dir():
            artifact = self.get_artifact(artifact_id)
            name = artifact.display_filename or artifact.filename
            target = target / Path(name).name
        self._t.stream_to(f"/artifacts/{artifact_id}/content", target)
        return target
