"""Behavioural objects bound to a client — the ergonomic layer.

`Analysis` and `Job` wrap a pure data model (``models.py``) plus a client handle
and add behaviour (`.capabilities()`, `.generate()`, `.wait()`, `.cancel()`).
The data layer never imports this module, so the two stay decoupled.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .models import AnalysisData, AnalyzedSource, ArtifactData, Event, JobData

if TYPE_CHECKING:  # avoid an import cycle (client imports resources)
    from .client import ContentClient

TERMINAL_STATUSES = frozenset(
    {"succeeded", "partially_succeeded", "failed", "cancelled"}
)


class Analysis:
    """A resolved analysis. `.id` is addressable (ADR 0014), so capabilities and
    generation reference it server-side rather than re-sending sources."""

    def __init__(self, data: AnalysisData, client: ContentClient):
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

    def capabilities(self, constraints: dict[str, Any] | None = None):
        return self._client.get_capabilities(self.id, constraints=constraints)

    def generate(self, outputs, **kwargs) -> Job:
        return self._client.generate(self.id, outputs, **kwargs)

    def __repr__(self) -> str:
        return f"Analysis(id={self.id!r}, sources={len(self.sources)})"


class Job:
    """A generation job. Async by design on the backend, so `.wait()` polls the
    job status until it reaches a terminal state (`partially_succeeded` counts
    as done — some outputs succeeded)."""

    def __init__(self, data: JobData, client: ContentClient):
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

    def refresh(self) -> Job:
        self.data = self._client.get_job(self.id).data
        return self

    def wait(self, *, timeout: float = 600.0, poll_interval: float = 1.0) -> Job:
        """Block until the job is terminal. Raises TimeoutError past `timeout`."""
        deadline = time.monotonic() + timeout
        while True:
            self.refresh()
            if self.is_terminal:
                return self
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"job {self.id} still {self.status!r} after {timeout}s"
                )
            time.sleep(poll_interval)

    @property
    def artifacts(self) -> list[ArtifactData]:
        return self._client.artifacts(self.id)

    def events(self, after_sequence: int = 0) -> list[Event]:
        return self._client.events(self.id, after_sequence=after_sequence)

    def logs(self, tail: int = 400) -> dict[str, Any]:
        return self._client.logs(self.id, tail=tail)

    def cancel(self) -> dict[str, Any]:
        return self._client.cancel(self.id)

    def retry(self) -> Job:
        return self._client.retry(self.id)

    def __repr__(self) -> str:
        return f"Job(id={self.id!r}, status={self.status!r})"
