"""Facts-derived feasibility view for the planner (ADR 0013).

Providers no longer emit capabilities — they describe resource *facts*. The
planner still needs, per output, a feasibility status and the resource details
its recipes validate against (available heights, codecs, subtitle languages).

``output_feasibility`` produces that view: the status is computed by the *same*
shared resolver the public /capabilities feed uses (R3 — a job never fails on a
divergent feasibility judgement), and the details are read straight from the
structured ``SourceAnalysis`` facts. It keeps the ``.status`` / ``.details``
shape the recipes already consume, so this is a source swap, not a rewrite.
"""

from dataclasses import dataclass, field

from content.capabilities.catalog import OUTPUT_CAPABILITY, capability
from content.capabilities.facts import facts_from_analysis
from content.capabilities.policy import EffectivePolicy
from content.capabilities.resolver import classify_capability
from content.domain.analysis import SourceAnalysis
from content.domain.capability import CapabilityReason
from content.planning.transformations import build_registry
from content.providers.base import ProviderRegistry


@dataclass(frozen=True)
class OutputFeasibility:
    """Planner-internal view replacing the old provider ``Capability``."""

    output_type: str
    status: str  # "available" | "unknown" | "unavailable"
    details: dict = field(default_factory=dict)
    # The resolver's structured verdict when status is "unavailable" — what the
    # feasibility error tells the caller in user terms, instead of the bare
    # "cannot be produced" that named neither the missing material nor the
    # remedy (ADR 0028 C).
    reason: CapabilityReason | None = None


def _details_for(
    output_type: str, analysis: SourceAnalysis, providers: ProviderRegistry
) -> dict:
    manual = sorted({t.language for t in analysis.subtitles if t.origin == "manual"})
    automatic = sorted(
        {t.language for t in analysis.subtitles if t.origin == "automatic"}
    )
    media = analysis.media
    if output_type == "video":
        return {
            "heights": list(media.video_heights),
            "video_codecs": list(media.video_codecs),
            "audio_codecs": list(media.audio_codecs),
            "audio_languages": list(media.audio_languages),
            "original_audio_language": media.original_audio_language,
        }
    if output_type == "audio":
        return {
            "languages": list(media.audio_languages),
            "original": media.original_audio_language,
        }
    if output_type == "subtitles":
        return {"manual": manual, "automatic": automatic}
    if output_type == "transcript":
        return {
            "from_subtitles": {"manual": manual, "automatic": automatic},
            # Installation fact, not a hardcode: true when an audio.transcribe
            # runner (e.g. the optional Whisper STT) is installed and reachable.
            "speech_to_text": bool(
                providers.available_runners_for_operation("audio.transcribe")
            )
            and media.has_audio,
        }
    return {}


def output_feasibility(
    output_type: str,
    analysis: SourceAnalysis,
    providers: ProviderRegistry,
    policy: EffectivePolicy | None = None,
) -> OutputFeasibility:
    facts = facts_from_analysis(analysis)
    registry = build_registry(providers)
    effective = policy or EffectivePolicy()
    cap_id = OUTPUT_CAPABILITY.get(output_type)
    status = "available"
    reason = None
    if cap_id is not None:
        cap = capability(cap_id)
        if cap is not None:
            resolved, _variant, reason = classify_capability(
                cap, facts, registry, providers, effective
            )
            # available/derivable → plannable; unknown → attempt with a warning;
            # restricted/unavailable → cannot be produced.
            status = {"available": "available", "derivable": "available"}.get(
                resolved, "unknown" if resolved == "unknown" else "unavailable"
            )
    return OutputFeasibility(
        output_type=output_type,
        status=status,
        details=_details_for(output_type, analysis, providers),
        reason=reason,
    )
