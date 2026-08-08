"""Internal provider interface (V1 — deliberately not frozen, see
docs/architecture.md §7; no public plugin system).

A provider accesses a source or a service. It contributes to two phases:
analysis (``analyze``) and execution (``execute`` of the plan steps that name
it). Planning stays in content.planning — providers expose *what* they can do
through the analysis capabilities; the planner decides the steps.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from content.config import ContentSettings
from content.domain.analysis import SourceAnalysis
from content.domain.plan import PlanStep
from content.domain.request import SourceDescriptor


@dataclass
class AnalysisContext:
    settings: ContentSettings
    workdir: Path  # scratch space for the analysis run


@dataclass
class Material:
    """An input a step consumes: a file produced by a dependency step (either
    a promoted artifact path or an internal work file)."""

    path: Path
    media_type: str = "application/octet-stream"
    attributes: dict = field(default_factory=dict)
    from_step: str = ""
    artifact_id: str | None = None  # set when the material is a promoted artifact


@dataclass
class ExecutionContext:
    settings: ContentSettings
    workdir: Path  # the job's work/ directory
    stdout_log: Path
    stderr_log: Path
    timeout_seconds: float
    input_materials: list[Material] = field(default_factory=list)
    cancel_check: Callable[[], bool] = lambda: False
    on_progress: Callable[[float, str], None] = lambda percent, message: None


@dataclass
class ProducedFile:
    """A file a step produced in the workdir, before artifact promotion."""

    path: Path
    media_type: str = "application/octet-stream"
    attributes: dict = field(default_factory=dict)


class StepExecutionError(Exception):
    """A step failed; carries a normalized error code and message.

    ``details`` is optional machine-readable context for failures a client may
    want to act on programmatically rather than by reading prose — which code
    points were undrawable, for instance. It travels to the `step.failed` event;
    the human message stays the thing people read.
    """

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


class SourceProvider(Protocol):
    name: str
    tool_version: str
    # Order in which candidates are tried for a source (lower first). Explicit
    # because alphabetical order is a trap: a generic `webpage` provider that
    # claims every URL sorts before `ytdlp` and would silently shadow it. A
    # specific extractor must always be offered the source first; generic
    # fallbacks take a high number.
    analysis_priority: int

    def supports(self, source: SourceDescriptor) -> bool: ...

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        """Stable identity of the underlying resource, for the analysis cache.
        May raise AnalysisError (e.g. path outside the allowed roots)."""
        ...

    def analyze(
        self, source: SourceDescriptor, ctx: AnalysisContext
    ) -> SourceAnalysis: ...

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]: ...


class StepRunner(Protocol):
    """The contract the engine needs from anything that can run a plan step.
    SourceProviders satisfy it; processors and service providers implement it
    directly.

    Class attributes describe the *installation* dimension of feasibility
    (docs/domain.md §2 level 2): which abstract operations the runner
    implements, whether it processes content locally or in the cloud
    (`constraints.privacy.allow_cloud_providers`), and — via `available()` —
    whether it is usable right now (daemon reachable, models present...)."""

    name: str
    tool_version: str
    operations: tuple[str, ...]
    location: str  # "local" | "cloud"

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]: ...


# Providers that do not declare a precedence sit in the middle: after the
# specific extractors, before the generic fallbacks.
_DEFAULT_PRIORITY = 100


def runner_is_available(runner) -> bool:
    """Runners may expose ``available() -> bool``; absent means always on."""
    probe = getattr(runner, "available", None)
    return bool(probe()) if callable(probe) else True


class ProviderRegistry:
    """Stable-name lookup of everything that can run steps — source providers
    (access resources) and processors (transform materials). Stable names are
    what make plans serializable and execution resumable."""

    def __init__(
        self,
        providers: list[SourceProvider],
        processors: list[StepRunner] | None = None,
    ):
        self._source_providers = {provider.name: provider for provider in providers}
        self._runners: dict[str, StepRunner] = {**self._source_providers}
        for processor in processors or []:
            if processor.name in self._runners:
                raise ValueError(f"duplicate runner name '{processor.name}'")
            self._runners[processor.name] = processor

    def get(self, name: str) -> StepRunner:
        if name not in self._runners:
            raise KeyError(f"unknown provider '{name}'")
        return self._runners[name]

    def describe(self) -> list[dict]:
        """Inventory of installed runners (name, kind, operations, version,
        location, availability) — for observability / the admin console."""
        inventory: list[dict] = []
        for name in self.names():
            runner = self._runners[name]
            inventory.append(
                {
                    "name": name,
                    "kind": "provider"
                    if name in self._source_providers
                    else "processor",
                    "operations": sorted(getattr(runner, "operations", ()) or ()),
                    "tool_version": getattr(runner, "tool_version", ""),
                    "location": getattr(runner, "location", "local"),
                    "available": runner_is_available(runner),
                }
            )
        return inventory

    def names(self) -> list[str]:
        return sorted(self._runners)

    def candidates_for_source(self, source: SourceDescriptor) -> list[SourceProvider]:
        """Every provider that claims *source*, in the order they must be tried.

        More than one can claim the same source: a URL is offered to yt-dlp
        first, and only a URL it does not recognise as media falls through to
        the generic web-page reader. Routing is therefore decided by what the
        analysis *finds*, not by pattern-matching the URL in advance.
        """
        claiming = [
            provider
            for name, provider in self._source_providers.items()
            if provider.supports(source)
        ]
        # (priority, name): explicit precedence first, name only to stay
        # deterministic between providers that share a priority.
        return sorted(
            claiming,
            key=lambda p: (getattr(p, "analysis_priority", _DEFAULT_PRIORITY), p.name),
        )

    def for_source(self, source: SourceDescriptor) -> SourceProvider | None:
        """The provider that should analyse *source* first."""
        candidates = self.candidates_for_source(source)
        return candidates[0] if candidates else None

    def runners_for_operation(self, operation: str) -> list[StepRunner]:
        """Runners declaring *operation*, deterministic order (sorted names)."""
        return [
            self._runners[name]
            for name in sorted(self._runners)
            if operation in getattr(self._runners[name], "operations", ())
        ]

    def available_runners_for_operation(self, operation: str) -> list[StepRunner]:
        return [
            runner
            for runner in self.runners_for_operation(operation)
            if runner_is_available(runner)
        ]
