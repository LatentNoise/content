"""Anti-drift guards for the Capability Catalog (ADR 0013, rule R7).

These lock the catalog to the transformation registry and the installed
implementations so a capability can never advertise a recipe that does not
exist, an unregistered operation, or an option group with no schema.
"""

import pytest

from content.capabilities import catalog
from content.planning.transformations import (
    AUDIO_TRANSCRIBE,
    build_registry,
    default_registry,
)
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ytdlp import YtDlpProvider


def _installed_registry():
    """A representative installed set: yt-dlp + ffmpeg + transcript + a cloud
    summarizer. Notably NO speech-to-text runner (audio.transcribe)."""
    providers = ProviderRegistry(
        [YtDlpProvider(), FfmpegProvider()],
        processors=[TranscriptProcessor(), CloudSummarizer("anthropic", "k", "m")],
    )
    return build_registry(providers)


def _variant_registered(registry, variant) -> bool:
    return all(registry.definition(op) is not None for op in variant.operations)


def _variant_implementable(registry, variant) -> bool:
    return all(registry.implementations_for(op) for op in variant.operations)


# --- R7 (i): every capability references at least one real variant -------------


def test_every_capability_has_variants_bound_to_itself():
    assert catalog.all_capabilities(), "catalog is empty"
    for cap in catalog.all_capabilities():
        assert cap.variants, f"{cap.id} has no variant"
        for variant in cap.variants:
            assert variant.capability_id == cap.id


def test_capability_lookup_roundtrips():
    for cap in catalog.all_capabilities():
        assert catalog.capability(cap.id) is cap
    assert catalog.capability("does.not.exist") is None


# --- R7 (ii): every variant's operations are registered ------------------------


def test_every_variant_operation_is_registered():
    registry = default_registry()  # definitions only — registration, not runners
    for variant in catalog.all_variants():
        for op in variant.operations:
            assert registry.definition(op) is not None, (
                f"{variant.id} uses unregistered operation '{op}'"
            )


def test_every_referenced_option_group_has_a_schema():
    for variant in catalog.all_variants():
        for group in variant.option_groups:
            assert group in catalog.OPTION_GROUPS, (
                f"{variant.id} references unknown option group '{group}'"
            )
            assert catalog.option_specs(group), f"empty schema for group '{group}'"


# --- R7 (iii): an announced-available variant has a resolvable implementation ---


@pytest.mark.parametrize(
    "variant_id,implementable",
    [
        ("video.download.direct", True),
        ("video.clip.download_cut", True),
        ("audio.download.direct", True),
        ("subtitles.download.direct", True),
        ("thumbnail.download.direct", True),
        ("metadata.export.direct", True),
        ("transcript.from_subtitles", True),
        ("summary.from_subtitles", True),
        # No STT runner installed → the from_audio variants are NOT implementable.
        ("transcript.from_audio", False),
        ("summary.from_audio", False),
    ],
)
def test_variant_implementability_matches_installed_runners(variant_id, implementable):
    registry = _installed_registry()
    variant = next(v for v in catalog.all_variants() if v.id == variant_id)
    assert _variant_registered(registry, variant)
    assert _variant_implementable(registry, variant) is implementable


def test_audio_transcribe_is_defined_but_unimplemented():
    """The seat for speech-to-text: defined in the registry so from_audio
    variants exist, but with no runner so they resolve as unavailable."""
    registry = _installed_registry()
    assert registry.definition(AUDIO_TRANSCRIBE) is not None
    assert registry.implementations_for(AUDIO_TRANSCRIBE) == []
