"""The transformation registry is the central source of operations and their
implementations; the PlanBuilder validates every step against it."""

import pytest

from content.planning.builder import PlanBuilder
from content.planning.transformations import (
    DEFINITIONS,
    Implementation,
    TransformationRegistry,
    UnknownTransformation,
    build_registry,
    default_registry,
    missing_registrations,
)
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ollama import OllamaProvider
from content.providers.ytdlp import YtDlpProvider


def _real_providers() -> ProviderRegistry:
    return ProviderRegistry(
        [YtDlpProvider(), FfmpegProvider()],
        processors=[TranscriptProcessor(), OllamaProvider()],
    )


def test_registry_covers_installed_runners_without_drift():
    providers = _real_providers()
    registry = build_registry(providers)
    # every operation a runner declares is known to the registry for that runner
    assert missing_registrations(registry, providers) == []


def test_ensure_step_rejects_unknown_operation():
    builder = PlanBuilder({}, registry=default_registry())
    with pytest.raises(UnknownTransformation):
        builder.ensure_step(
            operation="media.teleport", implementation="ytdlp", params={}
        )


def test_ensure_step_rejects_incompatible_implementation():
    # ytdlp implements acquisition, not text.summarize.
    registry = build_registry(_real_providers())
    builder = PlanBuilder({}, registry=registry)
    with pytest.raises(UnknownTransformation):
        builder.ensure_step(
            operation="text.summarize", implementation="ytdlp", params={}
        )


def test_implementation_version_is_part_of_the_signature():
    def sig(version: int) -> str:
        reg = TransformationRegistry(
            list(DEFINITIONS), [Implementation("text.summarize", "x", version)]
        )
        b = PlanBuilder({}, registry=reg)
        b.ensure_step(operation="text.summarize", implementation="x", params={})
        return b.steps[0].signature

    # a Content-controlled version bump changes the content-addressed identity
    assert sig(1) != sig(2)
