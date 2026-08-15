"""The asynchronous Content client — the async mirror of ContentClient.

Same surface, awaitable. `AsyncAnalysis`/`AsyncJob` are the behavioural objects;
`AsyncJob.wait()` polls until the job is terminal. Use as an async context
manager so the underlying httpx client is closed cleanly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Self

from ._transport import DEFAULT_TIMEOUT, AsyncTransport, RetryConfig, resolve_base_url
from .client import _source_body, _sources_list
from .models import (
    SCHEMA_VERSION,
    AnalysisData,
    AnalyzedSource,
    ArtifactData,
    CapabilitiesData,
    Event,
    JobData,
    upload_source,
)
from .resources import TERMINAL_STATUSES


class AsyncAnalysis:
    def __init__(self, data: AnalysisData, client: AsyncContentClient):
        self.data = data
        self._client = client

    @property
    def id(self) -> str:
        return self.data.analysis_id

    @property
    def sources(self) -> list[AnalyzedSource]:
        return self.data.sources

    @property
    def expires_at(self) -> str | None:
        return self.data.expires_at

    async def capabilities(self, constraints: dict[str, Any] | None = None):
        return await self._client.get_capabilities(self.id, constraints=constraints)

    async def generate(self, outputs, **kwargs) -> AsyncJob:
        return await self._client.generate(self.id, outputs, **kwargs)


class AsyncJob:
    def __init__(self, data: JobData, client: AsyncContentClient):
        self.data = data
        self._client = client

    @property
    def id(self) -> str:
        return self.data.job_id

    @property
    def status(self) -> str:
        return self.data.status

    @property
    def is_terminal(self) -> bool:
        return self.data.status in TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.data.status in ("succeeded", "partially_succeeded")

    async def refresh(self) -> AsyncJob:
        self.data = (await self._client.get_job(self.id)).data
        return self

    async def wait(
        self, *, timeout: float = 600.0, poll_interval: float = 1.0
    ) -> AsyncJob:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            await self.refresh()
            if self.is_terminal:
                return self
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"job {self.id} still {self.status!r} after {timeout}s"
                )
            await asyncio.sleep(poll_interval)

    async def artifacts(self) -> list[ArtifactData]:
        return await self._client.artifacts(self.id)

    async def events(self, after_sequence: int = 0) -> list[Event]:
        return await self._client.events(self.id, after_sequence=after_sequence)

    async def cancel(self) -> dict[str, Any]:
        return await self._client.cancel(self.id)

    async def retry(self) -> AsyncJob:
        return await self._client.retry(self.id)


class AsyncContentClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryConfig | None = None,
        http_client=None,
    ):
        self._t = AsyncTransport(
            resolve_base_url(base_url),
            timeout,
            retry or RetryConfig(),
            client=http_client,
        )

    @property
    def base_url(self) -> str:
        return self._t.base_url

    async def aclose(self) -> None:
        await self._t.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # --- system / observability ------------------------------------------------

    async def health(self) -> dict[str, Any]:
        return await self._t.get("/health")

    async def system(self) -> dict[str, Any]:
        return await self._t.get("/system")

    async def config(self) -> dict[str, Any]:
        return await self._t.get("/config")

    async def catalog(self) -> dict[str, Any]:
        return await self._t.get("/catalog")

    async def storage(self) -> dict[str, Any]:
        return await self._t.get("/storage")

    async def cache(self) -> dict[str, Any]:
        return await self._t.get("/cache")

    async def purge_cache(self) -> dict[str, Any]:
        return await self._t.post("/cache/purge")

    async def folders(self) -> list[str]:
        return (await self._t.get("/folders")).get("folders", [])

    # --- uploads (ADR 0020) ------------------------------------------------------

    async def upload(self, path: Path | str, *, media_type: str = "") -> dict[str, Any]:
        """Send a local file to the engine and return its upload record."""
        return await self._t.post_file("/uploads", Path(path), media_type=media_type)

    async def upload_file(
        self, path: Path | str, *, id: str = "main", media_type: str = ""
    ) -> dict[str, Any]:
        """Upload a local file and return a source ready to use — the async
        twin of the sync client's helper."""
        record = await self.upload(path, media_type=media_type)
        return upload_source(record["upload_id"], id=id)

    async def get_upload(self, upload_id: str) -> dict[str, Any]:
        return await self._t.get(f"/uploads/{upload_id}")

    async def delete_upload(self, upload_id: str) -> None:
        await self._t.request("DELETE", f"/uploads/{upload_id}")

    # --- analysis / capabilities -----------------------------------------------

    async def analyze(self, sources: Any) -> AsyncAnalysis:
        data = await self._t.post("/analyses", {"sources": _sources_list(sources)})
        return AsyncAnalysis(AnalysisData.model_validate(data), self)

    async def get_analysis(self, analysis_id: str) -> AsyncAnalysis:
        data = await self._t.get(f"/analyses/{analysis_id}")
        return AsyncAnalysis(AnalysisData.model_validate(data), self)

    async def get_capabilities(
        self, target: Any, constraints: dict[str, Any] | None = None
    ) -> CapabilitiesData:
        body = _source_body(
            target if not isinstance(target, AsyncAnalysis) else target.id
        )
        if constraints:
            body["constraints"] = constraints
        data = await self._t.post("/capabilities", body)
        return CapabilitiesData.model_validate(data)

    async def generate(
        self,
        target: Any,
        outputs: Any,
        *,
        preferences: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncJob:
        resolved = target.id if isinstance(target, AsyncAnalysis) else target
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "outputs": [outputs] if isinstance(outputs, dict) else list(outputs),
            **_source_body(resolved),
        }
        for key, value in (
            ("preferences", preferences),
            ("constraints", constraints),
            ("execution", execution),
            ("metadata", metadata),
        ):
            if value:
                body[key] = value
        idempotent = bool(execution and execution.get("idempotency_key"))
        data = await self._t.post("/jobs", body, idempotent=idempotent)
        return AsyncJob(JobData.model_validate(data), self)

    async def submit(self, request: dict[str, Any]) -> AsyncJob:
        """Submit a pre-built GenerationRequest (raw contract dict)."""
        idempotent = bool((request.get("execution") or {}).get("idempotency_key"))
        data = await self._t.post("/jobs", request, idempotent=idempotent)
        return AsyncJob(JobData.model_validate(data), self)

    # --- jobs / artifacts -------------------------------------------------------

    async def list_jobs(
        self, *, status: str | None = None, limit: int = 30
    ) -> list[JobData]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        rows = await self._t.get("/jobs", params=params)
        return [JobData.model_validate(r) for r in rows]

    async def get_job(self, job_id: str) -> AsyncJob:
        data = await self._t.get(f"/jobs/{job_id}")
        return AsyncJob(JobData.model_validate(data), self)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        return await self._t.post(f"/jobs/{job_id}/cancel")

    async def retry(self, job_id: str) -> AsyncJob:
        data = await self._t.post(f"/jobs/{job_id}/retry")
        return AsyncJob(JobData.model_validate(data), self)

    async def events(self, job_id: str, after_sequence: int = 0) -> list[Event]:
        rows = await self._t.get(
            f"/jobs/{job_id}/events", params={"after_sequence": after_sequence}
        )
        return [Event.model_validate(r) for r in rows]

    async def logs(self, job_id: str, tail: int = 400) -> dict[str, Any]:
        return await self._t.get(f"/jobs/{job_id}/logs", params={"tail": tail})

    async def artifacts(self, job_id: str) -> list[ArtifactData]:
        rows = await self._t.get(f"/jobs/{job_id}/artifacts")
        return [ArtifactData.model_validate(r) for r in rows]

    async def get_artifact(self, artifact_id: str) -> ArtifactData:
        return ArtifactData.model_validate(
            await self._t.get(f"/artifacts/{artifact_id}")
        )

    async def artifact_bytes(self, artifact_id: str) -> bytes:
        return await self._t.content(f"/artifacts/{artifact_id}/content")
