"""Content SDK — the official Python client for the Content engine.

The single client every consumer (CLI, MCP, applications) speaks through. It
fully encapsulates the REST API; nothing else duplicates the engine's rules.

    from content_sdk import ContentClient
    from content_sdk import outputs

    with ContentClient("http://localhost:8010") as client:
        analysis = client.analyze(outputs.url_source("https://…"))  # sources or id
        caps = client.get_capabilities(analysis.id)
        job = client.generate(analysis.id, [outputs.audio_output()])
        job.wait()
        for artifact in job.artifacts:
            print(artifact.filename)
"""

from __future__ import annotations

from . import models as outputs  # builders live in models; alias for ergonomics
from .aio import AsyncAnalysis, AsyncContentClient, AsyncJob
from .client import ContentClient
from .errors import (
    APIError,
    Conflict,
    ContentError,
    Gone,
    NotFound,
    TransportError,
    ValidationError,
)
from .models import (
    ORIGINAL,
    AnalysisData,
    AnalyzedSource,
    ArtifactData,
    CapabilitiesData,
    Capability,
    Event,
    JobData,
    SourceCapabilities,
)
from .resources import Analysis, Job

__version__ = "0.6.4"

__all__ = [
    "ORIGINAL",
    "APIError",
    "Analysis",
    # data models
    "AnalysisData",
    "AnalyzedSource",
    "ArtifactData",
    "AsyncAnalysis",
    "AsyncContentClient",
    "AsyncJob",
    "CapabilitiesData",
    "Capability",
    "Conflict",
    "ContentClient",
    # errors
    "ContentError",
    "Event",
    "Gone",
    "Job",
    "JobData",
    "NotFound",
    "SourceCapabilities",
    "TransportError",
    "ValidationError",
    "outputs",
]
