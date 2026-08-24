"""Feasibility validation (phase 2) and ExecutionPlan construction.

Deterministic: identical request + analysis + environment produce the same
plan. Outputs are planned in topological order of their `from_outputs`
dependencies; identical acquisition needs are **mutualized** (one step, several
consumers); requiredness propagates to transitive dependencies (a required
output makes every step it relies on required — docs/domain.md §4).

The planner decides *what* runs and *who* runs it; provider-specific syntax
(yt-dlp selectors, ffmpeg arguments) stays in the providers (ADR 0005).
"""

from graphlib import TopologicalSorter

from content.application.collections import (
    MEMBER_OPERATION,
    RUNNER_NAME,
    derive_member_request,
)
from content.config import ContentSettings
from content.domain import errors as codes
from content.domain.analysis import ResourceAnalysis, SourceAnalysis
from content.domain.errors import (
    RequestRejected,
    ValidationIssue,
    ValidationResult,
)
from content.domain.languages import ORIGINAL, expand_original
from content.domain.plan import ExecutionPlan, OutputDelivery
from content.domain.request import (
    EXECUTABLE_OUTPUT_TYPES,
    FileSource,
    GenerationRequest,
    TextSource,
    UrlSource,
)
from content.domain.validation import resolve_inputs
from content.naming.engine import resolve_naming_plan
from content.naming.sanitize import item_slug
from content.persistence.store import new_id
from content.planning import transformations as T
from content.planning.auth import resolve_source_credential
from content.planning.builder import PlanBuilder
from content.planning.feasibility import OutputFeasibility, output_feasibility
from content.planning.transformations import UnknownTransformation, build_registry
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry

_OPERATIONS = {
    "video": "media.acquire_video",
    "audio": "media.acquire_audio",
    "thumbnail": "media.acquire_thumbnail",
    "subtitles": "media.acquire_subtitles",
    "metadata": "metadata.export",
    "document_text": "text.extract",
    "markdown": "text.extract",
}


def _outputs_in_dependency_order(
    request: GenerationRequest,
) -> list[tuple[int, object]]:
    """(index, output) pairs, upstream outputs first (from_outputs DAG)."""
    indexed = {
        output.id: (index, output) for index, output in enumerate(request.outputs)
    }
    graph = {
        output.id: [ref for ref in output.from_outputs if ref in indexed]
        for _, output in indexed.values()
    }
    order = TopologicalSorter(graph).static_order()
    return [indexed[output_id] for output_id in order]


def _reader_for(source, source_analysis, providers):
    """The provider that can extract text from *source*.

    `providers.for_source` answers "who analyses this first" — for a URL that is
    always yt-dlp, which cannot read text. The reader is the provider that both
    claims the source and declares `text.extract`, preferring the one whose
    analysis we are actually holding.
    """
    candidates = [
        provider
        for provider in providers.candidates_for_source(source)
        if T.TEXT_EXTRACT in getattr(provider, "operations", ())
    ]
    if not candidates:
        return None
    detected = getattr(source_analysis.resource, "detected_provider", "")
    for provider in candidates:
        if provider.name == detected:
            return provider
    return candidates[0]


def _source_params(source, credential_id: str | None = None) -> dict:
    params: dict = {}
    if isinstance(source, UrlSource):
        params["uri"] = source.uri
        if source.provider_args:
            params["provider_args"] = list(source.provider_args)
    elif isinstance(source, FileSource):
        params["path"] = source.path
    elif isinstance(source, TextSource):
        # An inline source has no location to fetch: the body travels in the
        # step, so extraction is reproducible from the plan alone.
        params["content"] = source.content
    if credential_id:
        params["credential_id"] = credential_id
    return params


def _sponsorblock_params(options) -> dict:
    """Normalize SponsorBlock options into step params (empty when disabled)."""
    if not options.remove and not options.mark:
        return {}
    return {
        "sponsorblock": {
            "remove": list(options.remove),
            "mark": list(options.mark),
            "cut_mode": options.cut_mode,
        }
    }


# --- video ----------------------------------------------------------------------


def _resolve_codec_preference(
    pref,
    available: list[str],
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> dict | None:
    """Apply D4 semantics to one codec preference against the analysis.

    `require` + unavailable -> feasibility error; `prefer` + unavailable ->
    warning, the engine falls back to what the source offers."""
    if pref is None:
        return None
    is_available = pref.value in available
    if not is_available:
        issue = ValidationIssue(
            code=(
                codes.CAPABILITY_UNAVAILABLE
                if pref.mode == "require"
                else codes.PREFERENCE_UNAVAILABLE
            ),
            path=path,
            message=(
                f"Codec '{pref.value}' is not available on this source "
                f"(available: {available or 'none detected'})."
            ),
            details={"available": available},
        )
        if pref.mode == "require":
            errors.append(issue)
            return None
        warnings.append(issue)
    return {"mode": pref.mode, "value": pref.value, "available": is_available}


def _plan_video_params(
    output,
    source,
    source_analysis,
    capability,
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> dict | None:
    """Feasibility + normalized params for a video output. Returns None when
    an error was recorded."""
    options = output.options
    processing = options.processing

    if processing.mode == "transcode":
        errors.append(
            ValidationIssue(
                code=codes.OPTION_NOT_SUPPORTED,
                path=f"{path}.options.processing.mode",
                message=(
                    "processing.mode 'transcode' is valid but is not implemented "
                    "by the current engine."
                ),
            )
        )
        return None
    if processing.mode == "copy" and options.container != "source":
        errors.append(
            ValidationIssue(
                code=codes.INVALID_OPTION,
                path=f"{path}.options.processing.mode",
                message=(
                    "processing.mode 'copy' forbids a container change; use "
                    "container 'source' or mode 'remux'."
                ),
            )
        )
        return None
    if processing.mode == "remux" and options.container == "source":
        errors.append(
            ValidationIssue(
                code=codes.INVALID_OPTION,
                path=f"{path}.options.container",
                message="processing.mode 'remux' requires an explicit container.",
            )
        )
        return None

    details = capability.details or {}
    before_errors = len(errors)
    video_codec = _resolve_codec_preference(
        options.selection.video_codec,
        details.get("video_codecs", []),
        f"{path}.options.selection.video_codec",
        errors,
        warnings,
    )
    audio_codec = _resolve_codec_preference(
        options.selection.audio_codec,
        details.get("audio_codecs", []),
        f"{path}.options.selection.audio_codec",
        errors,
        warnings,
    )
    if len(errors) > before_errors:
        return None

    max_height = options.selection.max_height
    if isinstance(source, FileSource):
        # File-backed video is copy/remux only: a hard height ceiling below the
        # source's height would require scaling, i.e. transcoding.
        heights = details.get("heights", [])
        if max_height is not None and any(h > max_height for h in heights):
            errors.append(
                ValidationIssue(
                    code=codes.OPTION_NOT_SUPPORTED,
                    path=f"{path}.options.selection.max_height",
                    message=(
                        f"max_height={max_height} requires scaling this "
                        f"{max(heights)}p file, which needs transcoding "
                        "(not implemented)."
                    ),
                    details={"source_heights": heights},
                )
            )
            return None
        if processing.embed_thumbnail:
            warnings.append(
                ValidationIssue(
                    code=codes.PREFERENCE_UNAVAILABLE,
                    path=f"{path}.options.processing.embed_thumbnail",
                    message="embed_thumbnail is ignored for file sources in V1.",
                )
            )

    embed_subtitles = _resolve_embed_subtitles(
        processing.embed_subtitles, source, source_analysis, path, warnings
    )
    audio_languages = _resolve_audio_languages(
        options.selection.audio_languages,
        details.get("audio_languages", []),
        capability.status,
        f"{path}.options.selection.audio_languages",
        warnings,
        original=details.get("original_audio_language", ""),
    )

    return {
        "selection": {
            "max_height": max_height,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "audio_languages": audio_languages,
        },
        # Codecs the source actually offers — lets the provider order its codec
        # fallback profiles and skip codecs that aren't there.
        "available_video_codecs": details.get("video_codecs", []),
        "container": None if options.container == "source" else options.container,
        "processing_mode": processing.mode,
        "embed_metadata": processing.embed_metadata,
        "embed_thumbnail": processing.embed_thumbnail,
        "embed_chapters": processing.embed_chapters,
        "embed_subtitles": embed_subtitles,
    }


# The runner that copies an audio track out of an already-acquired video.
# ffmpeg is the only implementation, and the registry refuses the step if it
# is absent — the same guarantee `video.cut` relies on.
_AUDIO_EXTRACTOR = "ffmpeg"


def _audio_already_in_the_video(
    audio_output,
    audio_languages: list[str],
    source,
    source_analysis,
    request,
    resolved,
    providers,
    credential_id,
    builder,
) -> str | None:
    """The acquisition step whose video already carries exactly this audio.

    Asking one URL for both a video and an audio output downloaded the same
    stream twice — merged into the video, then again on its own. The waste
    was the smaller half of the problem: a second request is a second chance
    to be refused, and one was, losing a job's audio to a transient 403 while
    the video beside it already held the identical stream (D-57).

    Returning a step here makes the audio a ``-c:a copy`` extraction from that
    video: the same packets, no network, and one less thing that can fail.

    Every condition below is a reason to keep downloading instead. Handing
    back a *different* audio file than the caller asked for — another
    language, a stream cut where it wanted none — would be a worse bug than
    fetching twice, so derivation is offered only where the extracted track is
    provably the one the second download would have produced.
    """
    if isinstance(source, FileSource):
        return None  # already an extraction; nothing is fetched twice
    if audio_output.scope != "single":
        return None
    if audio_output.options.format != "source":
        return None  # an explicit format means transcoding, which copy cannot do
    # An installation without the extractor keeps the download it always had:
    # an optimization must never be the reason a request stops working.
    try:
        extractor = providers.get(_AUDIO_EXTRACTOR)
    except KeyError:
        return None
    if T.ACQUIRE_AUDIO not in getattr(extractor, "operations", ()):
        return None

    wanted_sponsorblock = _sponsorblock_params(audio_output.options.sponsorblock)

    for candidate in request.outputs:
        if candidate.type != "video" or candidate.scope != "single":
            continue
        candidate_sources, _ = resolved.get(candidate.id, ([], []))
        if candidate_sources != [source.id]:
            continue
        # SponsorBlock is applied during acquisition, so it is baked into the
        # file: cutting one side and not the other makes the tracks differ.
        if _sponsorblock_params(candidate.options.sponsorblock) != wanted_sponsorblock:
            continue

        capability = output_feasibility("video", source_analysis, providers)
        if capability.status == "unavailable":
            continue
        provider = providers.for_source(source)
        if provider is None:
            continue

        # Recomputing the video's own parameters, discarding its diagnostics:
        # the real ones are recorded when that output is planned, and issuing
        # them twice would double every warning the caller reads.
        drop_errors: list[ValidationIssue] = []
        drop_warnings: list[ValidationIssue] = []
        video_params = _plan_video_params(
            candidate,
            source,
            source_analysis,
            capability,
            "",
            drop_errors,
            drop_warnings,
        )
        if video_params is None or drop_errors:
            continue
        selection = video_params.get("selection") or {}
        if (selection.get("audio_languages") or []) != audio_languages:
            continue  # different tracks requested; genuinely different downloads

        params = _source_params(source, credential_id)
        params.update(video_params)
        params.update(_sponsorblock_params(candidate.options.sponsorblock))
        # Content-addressed: this returns the step the video output plans (or
        # will plan) for itself, whichever output the loop reaches first — so
        # the two outputs share one download without depending on their order.
        return builder.ensure_step(
            operation=T.ACQUIRE_VIDEO,
            implementation=provider.name,
            params=params,
            resource_key=source_analysis.resource_key,
            source_id=source.id,
            id_suffix=candidate.id,
            unique_id=False,
        )
    return None


# --- collections: orchestration only (ADR 0019) ---------------------------------
#
# A collection generates nothing. It emits one member step per member, and that
# step's runner takes the member through the canonical single-resource pipeline
# (analyze -> build_plan -> execute). Nothing is invented here, because nothing
# is known here: --flat-playlist yields references, not facts. What this block
# used to hold — optimistic video/audio parameter builders and a video/audio-only
# operation map — was precisely the guessing ADR 0019 removes.


def _plan_each_item(
    output,
    index: int,
    builder: PlanBuilder,
    request: GenerationRequest,
    sources_by_id: dict,
    resolved: dict,
    analysis: ResourceAnalysis,
    providers: ProviderRegistry,
    credential_by_source: dict,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> None:
    """Expand an `each_item` output over a collection source into one member
    step per member, each bound to the output.

    This decides *which* members and *in what order*. How a member is produced
    is resolved when it enters execution, by the canonical pipeline — so no
    output type is privileged here, and none is excluded.
    """
    path = f"outputs[{index}]"
    source_ids, _ = resolved.get(output.id, ([], []))
    if len(source_ids) != 1:
        return  # arity already reported structurally
    source = sources_by_id[source_ids[0]]
    source_analysis = analysis.for_source(source_ids[0])
    if source_analysis is None:
        errors.append(
            ValidationIssue(
                code=codes.ANALYSIS_FAILED,
                path=path,
                message=f"No analysis available for source '{source_ids[0]}'.",
            )
        )
        return
    if source_analysis.resource.resource_type != "collection":
        errors.append(
            ValidationIssue(
                code=codes.SCOPE_NOT_SUPPORTED,
                path=f"{path}.scope",
                message=(
                    "scope 'each_item' requires a collection source (e.g. a playlist)."
                ),
            )
        )
        return

    credential_id = credential_by_source.get(source.id)
    usable = [entry for entry in source_analysis.entries if entry.url]
    planned = 0
    for position, entry in enumerate(source_analysis.entries, start=1):
        if not entry.url:
            # The ordinal is the collection's own index: an unusable member
            # leaves a numbering gap instead of renumbering the rest.
            continue
        member_params = {
            "member_uri": entry.url,
            "member_output_id": output.id,
            "member_source_id": source.id,
        }
        params: dict = {
            **member_params,
            "member_index": position,
            "member_total": len(usable),
            "member_request": derive_member_request(request, member_params),
            # The label ties this step to its naming entry (see the naming
            # engine's item_bases), and carries the ordinal for the filename.
            "item_label": item_slug(entry.title or entry.id, position),
            # The member's own title, for progress display ("3/6 · Title") —
            # presentation data the clients would otherwise have to re-derive
            # by parsing slugs.
            "item_title": entry.title or entry.id,
        }
        if credential_id:
            params["credential_id"] = credential_id
        builder.bound_step(
            output.id,
            operation=MEMBER_OPERATION,
            provider=RUNNER_NAME,
            source_id=source.id,
            params=params,
            resource_key="",
            per_item=True,
        )
        planned += 1
    if planned == 0:
        warnings.append(
            ValidationIssue(
                code=codes.PARTIAL_OUTPUT,
                path=path,
                message="the collection lists no usable member.",
            )
        )


def _resolve_audio_languages(
    requested: list[str],
    available: list[str],
    status: str,
    path: str,
    warnings: list[ValidationIssue],
    original: str = "",
) -> list[str]:
    """Keep the requested audio languages that the source offers, in the
    requested order. Unavailable ones are dropped with a warning; an empty
    request (or an inconclusive analysis) is passed through unchanged.

    This is also where the reserved ``original`` token is resolved (ADR 0022),
    and it is resolved *here* on purpose: the single-resource path. A
    collection reaches it by planning each member from that member's own
    analysis, so the token expands per member without the collection knowing
    anything about languages (INV-018).
    """
    if not requested:
        return []
    expanded = expand_original(requested, original)
    if ORIGINAL in requested and not original:
        # Degrade to the next preference rather than fail — but say so. A
        # playlist asking for "each video in its own voice" quietly returning
        # the default track for one member is exactly the kind of silence
        # that gets discovered in the library a week later.
        warnings.append(
            ValidationIssue(
                code=codes.PARTIAL_OUTPUT,
                path=path,
                message=(
                    "The source declares no original audio language, so "
                    f"'{ORIGINAL}' was dropped"
                    + (
                        f"; falling back to {expanded}."
                        if expanded
                        else " and the engine's default track is used."
                    )
                ),
            )
        )
    available_set = set(available)
    if status != "available" or not available_set:
        return expanded  # inconclusive — let the provider attempt it
    matching = [lang for lang in expanded if lang in available_set]
    if len(matching) != len(expanded):
        missing = [lang for lang in expanded if lang not in available_set]
        warnings.append(
            ValidationIssue(
                code=codes.PARTIAL_OUTPUT,
                path=path,
                message=(
                    f"Audio languages {missing} are not offered by the source "
                    "and will be skipped."
                ),
                details={"available": sorted(available_set)},
            )
        )
    return matching


def _resolve_embed_subtitles(
    requested: list[str],
    source,
    source_analysis,
    path: str,
    warnings: list[ValidationIssue],
) -> list[str]:
    """Cross the requested embed languages with what the source offers. Drops
    unavailable languages with a warning; empty request stays empty."""
    if not requested:
        return []
    p = f"{path}.options.processing.embed_subtitles"
    if isinstance(source, FileSource):
        warnings.append(
            ValidationIssue(
                code=codes.PREFERENCE_UNAVAILABLE,
                path=p,
                message="embed_subtitles is ignored for file sources in V1.",
            )
        )
        return []
    available = source_analysis.subtitle_languages() if source_analysis else set()
    matching = [lang for lang in requested if lang in available]
    # Prune only when the source actually offers subtitles (conclusive);
    # otherwise attempt the request as-is.
    if available:
        if len(matching) != len(requested):
            missing = [lang for lang in requested if lang not in available]
            warnings.append(
                ValidationIssue(
                    code=codes.PARTIAL_OUTPUT,
                    path=p,
                    message=(
                        f"Subtitle languages {missing} were not detected on the "
                        "source and will not be embedded."
                    ),
                    details={"available": sorted(available)},
                )
            )
        return matching
    return requested  # inconclusive analysis: attempt the request as-is


# --- transcript -----------------------------------------------------------------


def _resolve_transcript_language(
    requested: str,
    subtitle_details: dict,
    resource_languages: list[str],
    preferred_languages: tuple[str, ...] = (),
) -> str | None:
    """Deterministic language choice for a transcript.

    An explicit request always wins. For ``auto``, a transcript is the text of
    what is actually *said*, so the order chases the source's own language and
    keeps alphabetical order as a genuine last resort:

    1. the language analysis reports for the resource, from either kind of
       track — when we know what was spoken, nothing else can beat it;
    2. otherwise, among **manual** tracks: an operator-configured language
       (``CONTENT_LANGUAGE_PRIMARY`` and its secondaries), then English, then
       the first one. A hand-written track is almost always in the video's own
       language, which makes it the best available stand-in for step 1;
    3. only if no manual track exists at all, the same order among automatic
       ones.

    Note what this deliberately does *not* do: a French installation asking for
    a transcript of an English video gets the English one, not a machine
    translation into French. Preferences choose among what was genuinely said;
    they do not turn a transcript into a translation. Ask for a `translation`
    output for that.

    Why the order matters (D-58): YouTube offers auto-*translated* subtitles in
    around a hundred languages, so "the first track" used to resolve to ``aa``
    (Afar) for an ordinary English video. The engine then downloaded Afar
    subtitles and called the result a transcript — silently wrong, which is
    worse than failing.
    """
    manual = sorted(subtitle_details.get("manual", []))
    automatic = sorted(subtitle_details.get("automatic", []))
    if requested != "auto":
        return requested

    # 1. what the source is known to be in, whoever wrote the track.
    for candidate in resource_languages:
        if candidate and candidate in (set(manual) | set(automatic)):
            return candidate

    # Deduplicate while keeping the order; a language named twice must not let
    # a later, weaker signal jump the queue.
    ordered: list[str] = []
    for lang in (*preferred_languages, "en"):
        if lang and lang not in ordered:
            ordered.append(lang)

    # 2. then 3. — human tracks first, machine ones only if there are none.
    for tracks in (manual, automatic):
        if not tracks:
            continue
        for candidate in ordered:
            if candidate in tracks:
                return candidate
        return tracks[0]  # deterministic, and only ever within one kind
    return None


def _stt_runner(providers: ProviderRegistry):
    """The speech-to-text runner to build with, or None. Deterministic: the
    first *available* implementation of audio.transcribe (sorted names) — the
    same order the resolver's variant verdict consults (R3)."""
    runners = providers.available_runners_for_operation("audio.transcribe")
    return runners[0] if runners else None


def _runner_model(runner) -> str:
    resolve = getattr(runner, "resolve_model", None)
    return resolve() if callable(resolve) else ""


def _transcript_chain(
    request: GenerationRequest,
    builder: PlanBuilder,
    source,
    source_analysis: SourceAnalysis,
    capability: OutputFeasibility,
    provider,
    providers: ProviderRegistry,
    path: str,
    requested_language: str,
    requested_mode: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
    preferred_languages: tuple[str, ...] = (),
) -> tuple[str, str, str, str | None, str] | None:
    """Feasibility + the acquisition chain feeding a transcript, for BOTH
    variants (R3: the same order the resolver selects — subtitles when present,
    else speech-to-text when installed). Returns
    ``(dependency_step_id, transcript_operation, transcript_runner, language,
    model)``, or None after recording errors. Shared by transcript outputs and
    synthesized summary chains."""
    details = capability.details or {}
    subtitle_details = details.get("from_subtitles", {})
    stt = _stt_runner(providers)
    stt_available = bool(details.get("speech_to_text", False)) and stt is not None
    has_subtitles = bool(
        subtitle_details.get("manual") or subtitle_details.get("automatic")
    )
    allowed = request.constraints.content.allowed_languages

    use_stt = False
    if requested_mode == "speech_to_text":
        if not stt_available:
            errors.append(
                ValidationIssue(
                    code=codes.OPTION_NOT_SUPPORTED,
                    path=f"{path}.options.source",
                    message=(
                        "speech_to_text is valid but no speech-to-text provider "
                        "is installed."
                    ),
                )
            )
            return None
        use_stt = True
    elif not has_subtitles and capability.status != "unknown":
        if stt_available:
            use_stt = True
        else:
            errors.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNAVAILABLE,
                    path=path,
                    message=(
                        "No existing subtitles on this source and no "
                        "speech-to-text provider installed."
                    ),
                    details={"from_subtitles": subtitle_details},
                )
            )
            return None

    if use_stt:
        return _stt_chain(
            request,
            builder,
            source,
            source_analysis,
            provider,
            stt,
            path,
            requested_language,
            warnings,
            errors,
            credential_id,
        )

    language = _resolve_transcript_language(
        requested_language,
        subtitle_details,
        source_analysis.resource.languages or [],
        preferred_languages,
    )
    if language is None:
        # Unknown capability (e.g. direct URL): attempt a sensible default.
        language = "en" if requested_language == "auto" else requested_language

    if allowed and language not in allowed:
        errors.append(
            ValidationIssue(
                code=codes.CONSTRAINT_UNSATISFIABLE,
                path=f"{path}.options.language",
                message=(
                    f"Resolved transcript language '{language}' is outside "
                    "constraints.content.allowed_languages."
                ),
                details={"resolved": language, "allowed": allowed},
            )
        )
        return None
    available_langs = set(subtitle_details.get("manual", [])) | set(
        subtitle_details.get("automatic", [])
    )
    if (
        requested_language != "auto"
        and capability.status != "unknown"
        and language not in available_langs
    ):
        # An explicit language the subtitles cannot serve: fall back to
        # speech-to-text when installed (the audio may carry that language),
        # else reject with the structured reason — never a silent swap.
        if stt_available:
            return _stt_chain(
                request,
                builder,
                source,
                source_analysis,
                provider,
                stt,
                path,
                requested_language,
                warnings,
                errors,
                credential_id,
            )
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=f"{path}.options.language",
                message=(
                    f"No existing subtitles in '{language}' on this source, and "
                    "no speech-to-text provider installed."
                ),
                details={"from_subtitles": subtitle_details},
            )
        )
        return None

    if TranscriptProcessor.name not in providers.names():
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message="The transcript processor is not installed.",
            )
        )
        return None
    acquisition_id = builder.acquisition_step(
        operation="media.acquire_subtitles",
        provider=provider.name,
        source_id=source.id,
        params={
            **_source_params(source, credential_id),
            "languages": [language],
            "source": "prefer_manual",
            "format": "vtt",
        },
        resource_key=source_analysis.resource_key,
    )
    return (
        acquisition_id,
        "subtitles.to_transcript",
        TranscriptProcessor.name,
        language,
        "",
    )


def _stt_chain(
    request: GenerationRequest,
    builder: PlanBuilder,
    source,
    source_analysis: SourceAnalysis,
    provider,
    stt,
    path: str,
    requested_language: str,
    warnings: list[ValidationIssue],
    errors: list[ValidationIssue],
    credential_id: str | None = None,
) -> tuple[str, str, str, str | None, str] | None:
    """The speech-to-text variant: acquire the audio, then audio.transcribe.
    `language=None` lets the model detect it at execution time."""
    language = None if requested_language == "auto" else requested_language
    allowed = request.constraints.content.allowed_languages
    if language is not None and allowed and language not in allowed:
        errors.append(
            ValidationIssue(
                code=codes.CONSTRAINT_UNSATISFIABLE,
                path=f"{path}.options.language",
                message=(
                    f"Requested transcript language '{language}' is outside "
                    "constraints.content.allowed_languages."
                ),
                details={"resolved": language, "allowed": allowed},
            )
        )
        return None
    if language is None and allowed:
        warnings.append(
            ValidationIssue(
                code=codes.CONSTRAINT_CHECK_DEFERRED,
                path=f"{path}.options.language",
                message=(
                    "The transcript language is detected at execution time and "
                    "cannot be checked against allowed_languages beforehand."
                ),
            )
        )
    acquisition_id = builder.acquisition_step(
        operation="media.acquire_audio",
        provider=provider.name,
        source_id=source.id,
        params=_source_params(source, credential_id),
        resource_key=source_analysis.resource_key,
    )
    return acquisition_id, "audio.transcribe", stt.name, language, _runner_model(stt)


def _upstream_transcript_reference_error(
    ref_output, path: str, errors: list[ValidationIssue]
) -> None:
    if ref_output.type == "audio":
        errors.append(
            ValidationIssue(
                code=codes.OPTION_NOT_SUPPORTED,
                path=f"{path}.from_outputs",
                message=(
                    "Transcribing audio requires a speech-to-text provider, "
                    "which is not installed."
                ),
            )
        )
    else:
        errors.append(
            ValidationIssue(
                code=codes.INVALID_OPTION,
                path=f"{path}.from_outputs",
                message=(
                    "A transcript derives from a 'subtitles' output "
                    f"(or a source), not from '{ref_output.type}'."
                ),
            )
        )


def _plan_transcript(
    output,
    index: int,
    request: GenerationRequest,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    capability: OutputFeasibility | None,
    provider,
    providers: ProviderRegistry,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
    preferred_languages: tuple[str, ...] = (),
) -> None:
    path = f"outputs[{index}]"
    options = output.options

    if options.timestamps == "word":
        errors.append(
            ValidationIssue(
                code=codes.OPTION_NOT_SUPPORTED,
                path=f"{path}.options.timestamps",
                message="Word-level timestamps are valid but not implemented.",
            )
        )
        return

    if resolved_output_ids:
        ref_id = resolved_output_ids[0]
        ref_output = outputs_by_id.get(ref_id)
        if ref_output is None:
            return  # unknown reference already reported structurally
        language = None if options.language == "auto" else options.language
        if ref_output.type == "audio":
            # Transcribe a bound audio output — legal once an STT runner exists.
            stt = _stt_runner(providers)
            if stt is None:
                _upstream_transcript_reference_error(ref_output, path, errors)
                return
            dependency_id = builder.step_of_output(ref_id)
            if dependency_id is None:
                return  # the referenced output failed feasibility
            builder.bound_step(
                output.id,
                operation="audio.transcribe",
                provider=stt.name,
                source_id=getattr(source, "id", None),
                params={
                    "language": language,
                    "format": options.format,
                    "model": _runner_model(stt),
                },
                depends_on=[dependency_id],
                resource_key=builder.step_resource_key(dependency_id),
            )
            return
        if ref_output.type != "subtitles":
            _upstream_transcript_reference_error(ref_output, path, errors)
            return
        if TranscriptProcessor.name not in providers.names():
            errors.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNAVAILABLE,
                    path=path,
                    message="The transcript processor is not installed.",
                )
            )
            return
        dependency_id = builder.step_of_output(ref_id)
        if dependency_id is None:
            return  # the referenced output failed feasibility; already reported
        builder.bound_step(
            output.id,
            operation="subtitles.to_transcript",
            provider=TranscriptProcessor.name,
            source_id=getattr(source, "id", None),
            params={"language": language, "format": options.format},
            depends_on=[dependency_id],
            resource_key=builder.step_resource_key(dependency_id),
        )
        return

    if source is None or source_analysis is None or capability is None:
        return  # source-level errors already reported
    chain = _transcript_chain(
        request,
        builder,
        source,
        source_analysis,
        capability,
        provider,
        providers,
        path,
        options.language,
        options.source,
        errors,
        warnings,
        credential_id,
        preferred_languages,
    )
    if chain is None:
        return
    dependency_id, operation, runner_name, language, model = chain
    params: dict = {"language": language, "format": options.format}
    if model:
        params["model"] = model
    builder.bound_step(
        output.id,
        operation=operation,
        provider=runner_name,
        source_id=source.id,
        params=params,
        depends_on=[dependency_id],
        resource_key=source_analysis.resource_key,
    )


# --- summary --------------------------------------------------------------------


def _select_llm_runner(
    request: GenerationRequest,
    providers: ProviderRegistry,
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    operation: str = "text.summarize",
):
    """Pick the runner for an LLM operation (text.summarize / text.translate):
    privacy constraint first, then the client's `preferences.providers.llm`
    order, then sorted names. Returns None after recording an error."""
    from content.providers.base import runner_is_available

    verb = "summarization" if operation == "text.summarize" else "translation"
    candidates = providers.runners_for_operation(operation)
    if not candidates:
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=f"No {verb} runner is installed.",
            )
        )
        return None

    allow_cloud = request.constraints.privacy.allow_cloud_providers
    eligible = [
        runner
        for runner in candidates
        if allow_cloud or getattr(runner, "location", "local") != "cloud"
    ]
    if not eligible:
        errors.append(
            ValidationIssue(
                code=codes.CONSTRAINT_UNSATISFIABLE,
                path=path,
                message=(
                    f"Every installed {verb} runner is a cloud provider, "
                    "forbidden by constraints.privacy.allow_cloud_providers."
                ),
                details={"installed": [runner.name for runner in candidates]},
            )
        )
        return None

    preferred = request.preferences.providers.get("llm", [])
    ordered = [r for name in preferred for r in eligible if r.name == name]
    ordered += [r for r in eligible if r.name not in preferred]
    seen: set[str] = set()
    ordered = [r for r in ordered if not (r.name in seen or seen.add(r.name))]

    for runner in ordered:
        if runner_is_available(runner):
            if preferred and runner.name not in preferred:
                warnings.append(
                    ValidationIssue(
                        code=codes.PREFERRED_PROVIDER_UNAVAILABLE,
                        path="preferences.providers.llm",
                        message=(
                            f"No preferred LLM runner is available; using "
                            f"'{runner.name}'."
                        ),
                    )
                )
            return runner
    errors.append(
        ValidationIssue(
            code=codes.CAPABILITY_UNAVAILABLE,
            path=path,
            message=(
                f"{verb.capitalize()} runners are installed but none is "
                "available right now (is the LLM daemon running?)."
            ),
            details={"installed": [runner.name for runner in eligible]},
        )
    )
    return None


def _plan_summary(
    output,
    index: int,
    request: GenerationRequest,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    provider,
    providers: ProviderRegistry,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
    preferred_languages: tuple[str, ...] = (),
) -> None:
    path = f"outputs[{index}]"
    options = output.options

    runner = _select_llm_runner(request, providers, path, errors, warnings)
    if runner is None:
        return

    allowed = request.constraints.content.allowed_languages
    if allowed and options.language != "auto" and options.language not in allowed:
        errors.append(
            ValidationIssue(
                code=codes.CONSTRAINT_UNSATISFIABLE,
                path=f"{path}.options.language",
                message=(
                    f"Summary language '{options.language}' is outside "
                    "constraints.content.allowed_languages."
                ),
                details={"requested": options.language, "allowed": allowed},
            )
        )
        return

    dependency_id = None
    if resolved_output_ids:
        ref_id = resolved_output_ids[0]
        ref_output = outputs_by_id.get(ref_id)
        if ref_output is None:
            return  # unknown reference already reported structurally
        if ref_output.type != "transcript":
            errors.append(
                ValidationIssue(
                    code=codes.INVALID_OPTION,
                    path=f"{path}.from_outputs",
                    message=(
                        "A summary derives from a 'transcript' output "
                        f"(or a source), not from '{ref_output.type}'."
                    ),
                )
            )
            return
        dependency_id = builder.step_of_output(ref_id)
        if dependency_id is None:
            return  # the referenced output failed feasibility
    else:
        # From a source: synthesize the whole transcript chain (subtitles or
        # speech-to-text, per the shared variant selection), mutualized with
        # any identical bound steps by the builder.
        if source is None or source_analysis is None:
            return  # source-level errors already reported

        # A readable resource (article, document) needs no transcript at all:
        # extract the text and summarize it (variant `summary.from_text`). This
        # branch is what keeps the planner honest with the resolver (R3) —
        # without it the catalog would advertise a path the planner refuses.
        if source_analysis.text.has_text:
            text_provider = _reader_for(source, source_analysis, providers)
            if text_provider is not None:
                dependency_id = builder.acquisition_step(
                    operation=T.TEXT_EXTRACT,
                    provider=text_provider.name,
                    source_id=source.id,
                    params={
                        **_source_params(source, credential_id),
                        # Plain text: the summarizer wants prose, not syntax.
                        "format": "text",
                    },
                    resource_key=source_analysis.resource_key,
                )
                return _bind_summary(
                    output, builder, runner, source, options, dependency_id
                )

        transcript_capability = output_feasibility(
            "transcript", source_analysis, providers
        )
        if transcript_capability.status != "available":
            errors.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNAVAILABLE,
                    path=path,
                    message=(
                        "A summary requires a transcript, which cannot be "
                        "derived from this source."
                    ),
                )
            )
            return
        chain = _transcript_chain(
            request,
            builder,
            source,
            source_analysis,
            transcript_capability,
            provider,
            providers,
            path,
            "auto",
            "auto",
            errors,
            warnings,
            credential_id,
            preferred_languages,
        )
        if chain is None:
            return
        acquisition_id, transcript_op, transcript_runner, t_language, t_model = chain
        transcript_params: dict = {"language": t_language, "format": "json"}
        if t_model:
            transcript_params["model"] = t_model
        dependency_id = builder.acquisition_step(
            operation=transcript_op,
            provider=transcript_runner,
            source_id=source.id,
            params=transcript_params,
            depends_on=[acquisition_id],
            resource_key=source_analysis.resource_key,
        )

    _bind_summary(output, builder, runner, source, options, dependency_id)


def _bind_summary(output, builder, runner, source, options, dependency_id) -> None:
    """Bind the summarize step. Shared by every path into a summary (transcript,
    speech-to-text, extracted text) so they cannot drift apart."""
    model = ""
    resolve_model = getattr(runner, "resolve_model", None)
    if callable(resolve_model):
        model = resolve_model()
    builder.bound_step(
        output.id,
        operation="text.summarize",
        provider=runner.name,
        source_id=getattr(source, "id", None),
        params={
            "language": options.language,
            "length": options.length,
            "style": options.style,
            "format": options.format,
            "model": model,
        },
        depends_on=[dependency_id],
        resource_key=builder.step_resource_key(dependency_id),
    )


# --- frames (thumbnail.generate / keyframes.extract) ------------------------------


def _plan_frames_output(
    output,
    index: int,
    builder: PlanBuilder,
    source,
    source_analysis: SourceAnalysis | None,
    provider,
    providers: ProviderRegistry,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
) -> bool:
    """Plan a generated thumbnail or a keyframe sheet.

    Returns True when it handled the output. Both capabilities resolve their
    instants here, against the analyzed duration, so an out-of-range request is
    refused while planning instead of becoming an ffmpeg call that silently
    returns a different frame.
    """
    from content.planning.recipes.frames import (
        keyframe_instants,
        plan_frames,
        thumbnail_instant,
    )

    path = f"outputs[{index}]"
    options = output.options
    if source is None or source_analysis is None:
        return True  # source-level errors already reported

    # R3: the shared feasibility gate upstream checked `thumbnail.download`,
    # because that is what OUTPUT_CAPABILITY maps the output type to. Generation
    # is a *different* capability with a different requirement, so it is checked
    # here — otherwise an installation without ffmpeg reaches the builder and
    # raises UnknownTransformation instead of refusing.
    if not providers.available_runners_for_operation(T.VIDEO_EXTRACT_FRAMES):
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=(
                    "Extracting frames from the video needs ffmpeg, which is "
                    "not available in this installation."
                ),
            )
        )
        return True

    duration = source_analysis.resource.duration_seconds

    if output.type == "thumbnail":
        at = thumbnail_instant(options, duration)
        if duration and at >= duration:
            errors.append(
                ValidationIssue(
                    code=codes.INVALID_OPTION,
                    path=f"{path}.options.at",
                    message=(
                        f"Instant {at:.3f}s is at or past the end of this "
                        f"{duration:.3f}s video."
                    ),
                    details={"duration_seconds": duration},
                )
            )
            return True
        if not duration:
            warnings.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNKNOWN,
                    path=path,
                    message=(
                        "The duration is unknown, so the frame is taken at "
                        f"{at:.0f}s rather than a fraction of the video."
                    ),
                )
            )
        return plan_frames(
            output=output,
            source=source,
            source_analysis=source_analysis,
            provider=provider,
            credential_id=credential_id,
            builder=builder,
            timestamps=[round(at, 3)],
            image_format="jpg",
            width=options.max_width,
            # A poster frame benefits from ffmpeg picking the most
            # representative frame nearby; a named instant does not.
            smart=not options.at,
        )

    # keyframes
    if not duration or duration <= 0:
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=(
                    "A keyframe sheet is spaced across the duration, which this "
                    "source does not report."
                ),
            )
        )
        return True
    if not _frame_format_supported(providers, options.format):
        errors.append(
            ValidationIssue(
                code=codes.OPTION_NOT_SUPPORTED,
                path=f"{path}.options.format",
                message=(
                    f"This ffmpeg build cannot write '{options.format}' images. "
                    f"Available: {', '.join(_frame_formats(providers))}."
                ),
                details={"available": list(_frame_formats(providers))},
            )
        )
        return True
    instants = keyframe_instants(options, duration)
    if not instants:
        errors.append(
            ValidationIssue(
                code=codes.INVALID_OPTION,
                path=f"{path}.options",
                message="The requested range contains no frame to extract.",
                details={"duration_seconds": duration},
            )
        )
        return True
    return plan_frames(
        output=output,
        source=source,
        source_analysis=source_analysis,
        provider=provider,
        credential_id=credential_id,
        builder=builder,
        timestamps=instants,
        image_format=options.format,
        width=options.width,
        smart=False,
    )


def _frame_formats(providers: ProviderRegistry) -> tuple[str, ...]:
    for runner in providers.runners_for_operation(T.VIDEO_EXTRACT_FRAMES):
        probe = getattr(runner, "image_formats", None)
        if callable(probe):
            return probe()
    return ("jpg", "png")


def _frame_format_supported(providers: ProviderRegistry, image_format: str) -> bool:
    return image_format in _frame_formats(providers)


# --- pdf ------------------------------------------------------------------------

# Outputs whose material is readable prose the renderer can lay out. Media
# outputs are absent on purpose: "a PDF of a video" has no meaning, and
# answering `invalid_reference` is better than rendering a path into a page.
RENDERABLE_OUTPUT_TYPES = (
    "summary",
    "transcript",
    "translation",
    "chapters",
    "markdown",
    "document_text",
)

# Formats that serialize for machines. Still renderable — the JSON simply lands
# on the page verbatim — but almost never what the caller meant.
_MACHINE_FORMATS = ("json", "ffmetadata")


def _plan_pdf(
    output,
    index: int,
    request: GenerationRequest,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    providers: ProviderRegistry,
    settings: ContentSettings,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
) -> None:
    path = f"outputs[{index}]"
    options = output.options

    renderer = _select_renderer(request, providers, settings, path, errors, warnings)
    if renderer is None:
        return

    if resolved_output_ids:
        dependency_id = _pdf_from_output(
            outputs_by_id, builder, resolved_output_ids[0], path, errors, warnings
        )
    else:
        dependency_id = _readable_from_source(
            source, source_analysis, builder, providers, path, errors, credential_id
        )
    if dependency_id is None:
        return

    builder.bound_step(
        output.id,
        operation=T.RENDER_PDF,
        provider=renderer.name,
        source_id=getattr(source, "id", None),
        params={
            "page_size": options.page_size,
            "title": options.title,
            # Operator configuration, recorded on the step so the plan fully
            # describes the render and a template change invalidates the cached
            # signature. It is a server-side *name*; the public contract never
            # carries a template, a path or renderer options.
            "template": settings.pdf_template,
        },
        depends_on=[dependency_id],
        resource_key=builder.step_resource_key(dependency_id),
    )


# --- speech ---------------------------------------------------------------------


def _plan_speech(
    output,
    index: int,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    providers: ProviderRegistry,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    credential_id: str | None = None,
) -> None:
    """Speak a sibling output, or the source's own readable text.

    The same two shapes as `_plan_pdf`, because the input is the same thing:
    readable prose. Speaking and rendering differ in what comes out, not in
    what goes in — which is why they share `RENDERABLE_OUTPUT_TYPES` and the
    source-side reader instead of each growing their own list.
    """
    path = f"outputs[{index}]"
    options = output.options

    from content.providers.base import runner_is_available

    runners = [
        runner
        for runner in providers.runners_for_operation(T.TEXT_SPEAK)
        if runner_is_available(runner)
    ]
    if not runners:
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=(
                    "No speech runner is installed. Install the optional TTS "
                    "extra to enable `speech` outputs."
                ),
            )
        )
        return
    runner = runners[0]

    if resolved_output_ids:
        ref_id = resolved_output_ids[0]
        ref_output = outputs_by_id.get(ref_id)
        if ref_output is None:
            return  # unknown reference already reported structurally
        if ref_output.type not in RENDERABLE_OUTPUT_TYPES:
            errors.append(
                ValidationIssue(
                    code=codes.INVALID_OPTION,
                    path=f"{path}.from_outputs",
                    message=(
                        f"Speech reads readable content, not a "
                        f"'{ref_output.type}' output. Expected one of: "
                        f"{', '.join(RENDERABLE_OUTPUT_TYPES)}."
                    ),
                )
            )
            return
        dependency_id = builder.step_of_output(ref_id)
    else:
        dependency_id = _readable_from_source(
            source, source_analysis, builder, providers, path, errors, credential_id
        )
    if dependency_id is None:
        return

    builder.bound_step(
        output.id,
        operation=T.TEXT_SPEAK,
        provider=runner.name,
        source_id=getattr(source, "id", None),
        params={
            "voice": options.voice,
            "language": options.language,
            "format": options.format,
            "speed": options.speed,
        },
        depends_on=[dependency_id],
        resource_key=builder.step_resource_key(dependency_id),
    )


# Renderer preference order for CONTENT_PDF_RENDERER=auto. Typst first: it is
# the high-quality implementation (real typography, byte-reproducible output),
# with ReportLab as the supported pure-Python fallback for installations where
# the binary cannot be shipped.
_RENDERER_PREFERENCE = ("content.pdf.typst", "content.pdf.reportlab")


def _select_renderer(
    request: GenerationRequest,
    providers: ProviderRegistry,
    settings: ContentSettings,
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
):
    """Pick the implementation of `document.render_pdf`.

    Selection lives here, not inside a processor, so the ExecutionPlan records
    *which* renderer will run: the plan stays an honest statement of what will
    happen, and the artifact's provenance names the backend for free.

    Order of authority: the operator's `CONTENT_PDF_RENDERER` pin, then the
    request's `preferences.providers["pdf"]`, then the built-in preference. A
    pinned renderer that is unavailable is an error rather than a silent
    downgrade — the operator asked for that one on purpose.
    """
    from content.providers.base import runner_is_available

    declared = providers.runners_for_operation(T.RENDER_PDF)
    if not declared:
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message="No PDF rendering runner is installed.",
            )
        )
        return None

    by_name = {runner.name: runner for runner in declared}
    pinned = (settings.pdf_renderer or "auto").strip().lower()
    if pinned not in ("", "auto"):
        target = (
            pinned if pinned.startswith("content.pdf.") else f"content.pdf.{pinned}"
        )
        runner = by_name.get(target)
        if runner is None:
            errors.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNAVAILABLE,
                    path=path,
                    message=(
                        f"CONTENT_PDF_RENDERER='{pinned}' names no installed "
                        f"renderer (installed: {', '.join(sorted(by_name))})."
                    ),
                )
            )
            return None
        if not runner_is_available(runner):
            errors.append(
                ValidationIssue(
                    code=codes.CAPABILITY_UNAVAILABLE,
                    path=path,
                    message=(
                        f"The pinned PDF renderer '{runner.name}' is installed "
                        "but not usable right now. " + _renderer_remedy(runner)
                    ),
                )
            )
            return None
        return runner

    preferred = request.preferences.providers.get("pdf", [])
    ordered = [by_name[n] for n in preferred if n in by_name]
    ordered += [by_name[n] for n in _RENDERER_PREFERENCE if n in by_name]
    ordered += [r for r in declared if r not in ordered]
    seen: set[str] = set()
    ordered = [r for r in ordered if not (r.name in seen or seen.add(r.name))]

    for runner in ordered:
        if runner_is_available(runner):
            if preferred and runner.name not in preferred:
                warnings.append(
                    ValidationIssue(
                        code=codes.PREFERRED_PROVIDER_UNAVAILABLE,
                        path="preferences.providers.pdf",
                        message=(
                            "No preferred PDF renderer is available; using "
                            f"'{runner.name}'."
                        ),
                    )
                )
            return runner

    errors.append(
        ValidationIssue(
            code=codes.CAPABILITY_UNAVAILABLE,
            path=path,
            message=(
                "PDF renderers are installed but none is usable: "
                + "; ".join(_renderer_remedy(r) for r in declared)
            ),
            details={"installed": sorted(by_name)},
        )
    )
    return None


def _renderer_remedy(runner) -> str:
    describe = getattr(runner, "unavailable_message", None)
    return describe() if callable(describe) else f"'{runner.name}' is unavailable."


def _pdf_from_output(
    outputs_by_id, builder: PlanBuilder, ref_id: str, path, errors, warnings
) -> str | None:
    """Render a sibling output. The general case: a PDF *of* something."""
    ref_output = outputs_by_id.get(ref_id)
    if ref_output is None:
        return None  # unknown reference already reported structurally
    if ref_output.type not in RENDERABLE_OUTPUT_TYPES:
        errors.append(
            ValidationIssue(
                code=codes.INVALID_OPTION,
                path=f"{path}.from_outputs",
                message=(
                    f"A PDF renders readable content, not a '{ref_output.type}' "
                    f"output. Expected one of: {', '.join(RENDERABLE_OUTPUT_TYPES)}."
                ),
            )
        )
        return None
    ref_format = getattr(ref_output.options, "format", "")
    if ref_format in _MACHINE_FORMATS:
        warnings.append(
            ValidationIssue(
                code=codes.OPTION_NOT_SUPPORTED,
                path=f"{path}.from_outputs",
                message=(
                    f"Output '{ref_id}' is generated as '{ref_format}', so the "
                    "PDF will contain that serialization verbatim. Request "
                    "'text' or 'markdown' on it for a readable document."
                ),
            )
        )
    return builder.step_of_output(ref_id)  # None when that output failed


def _readable_from_source(
    source, source_analysis, builder, providers, path, errors, credential_id
) -> str | None:
    """Render the source's own readable content — the article-to-PDF case."""
    if source is None or source_analysis is None:
        return None  # source-level errors already reported
    if not source_analysis.text.has_text:
        # Rarely reached: the shared feasibility gate already refuses a source
        # whose analysis found no text, with the message every output type
        # gets. It still matters when the analysis was *inconclusive* — the gate
        # only warns there and lets execution try, and "try" must not mean
        # handing the renderer nothing.
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=(
                    "This source has no readable text to render. Reference the "
                    "output to render with `from_outputs` (a summary or a "
                    "transcript, for example)."
                ),
            )
        )
        return None
    reader = _reader_for(source, source_analysis, providers)
    if reader is None:
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message="No installed provider can extract text from this source.",
            )
        )
        return None
    return builder.acquisition_step(
        operation=T.TEXT_EXTRACT,
        provider=reader.name,
        source_id=source.id,
        params={
            **_source_params(source, credential_id),
            # Markdown, not text: the renderer turns headings and lists into
            # real layout, which is the whole point of producing a document.
            "format": "markdown",
        },
        resource_key=source_analysis.resource_key,
    )


# --- translation ----------------------------------------------------------------


def _plan_translation(
    output,
    index: int,
    request: GenerationRequest,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    provider,
    providers: ProviderRegistry,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
) -> None:
    path = f"outputs[{index}]"
    options = output.options

    runner = _select_llm_runner(
        request, providers, path, errors, warnings, operation="text.translate"
    )
    if runner is None:
        return
    target = options.target_language
    allowed = request.constraints.content.allowed_languages
    if allowed and target not in allowed:
        errors.append(
            ValidationIssue(
                code=codes.CONSTRAINT_UNSATISFIABLE,
                path=f"{path}.options.target_language",
                message=(
                    f"Target language '{target}' is outside "
                    "constraints.content.allowed_languages."
                ),
                details={"requested": target, "allowed": allowed},
            )
        )
        return
    params: dict = {
        "target_language": target,
        "source_language": options.source_language,
        "model": _runner_model(runner),
    }

    if resolved_output_ids:
        # Bound input: translate an existing subtitles (SRT/VTT, timings kept)
        # or transcript (text) output.
        ref_id = resolved_output_ids[0]
        ref_output = outputs_by_id.get(ref_id)
        if ref_output is None:
            return  # unknown reference already reported structurally
        if ref_output.type not in ("subtitles", "transcript"):
            errors.append(
                ValidationIssue(
                    code=codes.INVALID_OPTION,
                    path=f"{path}.from_outputs",
                    message=(
                        "A translation derives from a 'subtitles' or "
                        f"'transcript' output (or a source), not from "
                        f"'{ref_output.type}'."
                    ),
                )
            )
            return
        dependency_id = builder.step_of_output(ref_id)
        if dependency_id is None:
            return  # the referenced output failed feasibility
        builder.bound_step(
            output.id,
            operation="text.translate",
            provider=runner.name,
            source_id=getattr(source, "id", None),
            params=params,
            depends_on=[dependency_id],
            resource_key=builder.step_resource_key(dependency_id),
        )
        return

    # From a source: acquire the subtitles, then translate them (the
    # translation.from_subtitles variant — timings preserved).
    if source is None or source_analysis is None:
        return  # source-level errors already reported
    subtitle_details = {
        "manual": sorted(
            {t.language for t in source_analysis.subtitles if t.origin == "manual"}
        ),
        "automatic": sorted(
            {t.language for t in source_analysis.subtitles if t.origin == "automatic"}
        ),
    }
    language = _resolve_transcript_language(
        options.source_language,
        subtitle_details,
        source_analysis.resource.languages,
    )
    available = set(subtitle_details["manual"]) | set(subtitle_details["automatic"])
    if language is None or (
        options.source_language != "auto" and language not in available
    ):
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=f"{path}.options.source_language",
                message="No subtitle track to translate on this source.",
                details={"from_subtitles": subtitle_details},
            )
        )
        return
    acquisition_id = builder.acquisition_step(
        operation="media.acquire_subtitles",
        provider=provider.name,
        source_id=source.id,
        params={
            **_source_params(source, credential_id),
            "languages": [language],
            "source": "prefer_manual",
            "format": "vtt",
        },
        resource_key=source_analysis.resource_key,
    )
    params["source_language"] = language
    builder.bound_step(
        output.id,
        operation="text.translate",
        provider=runner.name,
        source_id=source.id,
        params=params,
        depends_on=[acquisition_id],
        resource_key=source_analysis.resource_key,
    )


# --- chapters -------------------------------------------------------------------


def _plan_chapters(
    output,
    index: int,
    request: GenerationRequest,
    builder: PlanBuilder,
    outputs_by_id: dict,
    source,
    source_analysis: SourceAnalysis | None,
    provider,
    providers: ProviderRegistry,
    resolved_output_ids: list[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
    credential_id: str | None = None,
    preferred_languages: tuple[str, ...] = (),
) -> None:
    from content.processors.chapters import ChaptersProcessor

    path = f"outputs[{index}]"
    fmt = output.options.format
    duration = (
        source_analysis.resource.duration_seconds
        if source_analysis is not None
        else None
    )

    if resolved_output_ids:
        # Bound input: derive chapters from an existing transcript output.
        ref_id = resolved_output_ids[0]
        ref_output = outputs_by_id.get(ref_id)
        if ref_output is None:
            return  # unknown reference already reported structurally
        if ref_output.type != "transcript":
            errors.append(
                ValidationIssue(
                    code=codes.INVALID_OPTION,
                    path=f"{path}.from_outputs",
                    message=(
                        "Chapters derive from a 'transcript' output (or a "
                        f"source), not from '{ref_output.type}'."
                    ),
                )
            )
            return
        runner = _select_llm_runner(
            request, providers, path, errors, warnings, operation="chapters.derive"
        )
        if runner is None:
            return
        dependency_id = builder.step_of_output(ref_id)
        if dependency_id is None:
            return  # the referenced output failed feasibility
        builder.bound_step(
            output.id,
            operation="chapters.derive",
            provider=runner.name,
            source_id=getattr(source, "id", None),
            params={
                "format": fmt,
                "duration": duration,
                "model": _runner_model(runner),
            },
            depends_on=[dependency_id],
            resource_key=builder.step_resource_key(dependency_id),
        )
        return

    if source is None or source_analysis is None:
        return  # source-level errors already reported

    if source_analysis.chapters:
        # Deterministic extraction (chapters.from_source): the facts travel in
        # the step params — no acquisition, no LLM.
        builder.bound_step(
            output.id,
            operation="chapters.export",
            provider=ChaptersProcessor.name,
            source_id=source.id,
            params={
                "chapters": [c.model_dump() for c in source_analysis.chapters],
                "format": fmt,
                "duration": duration,
            },
            resource_key=source_analysis.resource_key,
        )
        return

    # No declared chapters: derive from the transcript (chapters.from_transcript)
    # — the shared transcript chain picks subtitles or speech-to-text (R3).
    runner = _select_llm_runner(
        request, providers, path, errors, warnings, operation="chapters.derive"
    )
    if runner is None:
        return
    transcript_capability = output_feasibility("transcript", source_analysis, providers)
    if transcript_capability.status != "available":
        errors.append(
            ValidationIssue(
                code=codes.CAPABILITY_UNAVAILABLE,
                path=path,
                message=(
                    "This source declares no chapters and no transcript can be "
                    "derived to generate them."
                ),
            )
        )
        return
    chain = _transcript_chain(
        request,
        builder,
        source,
        source_analysis,
        transcript_capability,
        provider,
        providers,
        path,
        "auto",
        "auto",
        errors,
        warnings,
        credential_id,
        preferred_languages,
    )
    if chain is None:
        return
    acquisition_id, transcript_op, transcript_runner, t_language, t_model = chain
    transcript_params: dict = {"language": t_language, "format": "json"}
    if t_model:
        transcript_params["model"] = t_model
    transcript_id = builder.acquisition_step(
        operation=transcript_op,
        provider=transcript_runner,
        source_id=source.id,
        params=transcript_params,
        depends_on=[acquisition_id],
        resource_key=source_analysis.resource_key,
    )
    builder.bound_step(
        output.id,
        operation="chapters.derive",
        provider=runner.name,
        source_id=source.id,
        params={"format": fmt, "duration": duration, "model": _runner_model(runner)},
        depends_on=[transcript_id],
        resource_key=source_analysis.resource_key,
    )


# --- entry point ----------------------------------------------------------------


def build_plan(
    request: GenerationRequest,
    analysis: ResourceAnalysis,
    providers: ProviderRegistry,
    settings: ContentSettings,
) -> ExecutionPlan:
    """Validate feasibility and build the plan; raises RequestRejected on
    feasibility errors, returns a plan carrying non-blocking warnings.

    The backstop lives here: a valid request must never answer 500. Feasibility
    is checked per output before the builder runs, but each of those checks is a
    separate rule, and a combination nobody enumerated (a `text` source asked for
    `metadata`, before this) reached the builder and escaped as
    `UnknownTransformation` — an internal name for "no runner implements this",
    surfaced as a crash. It is a feasibility answer, so it is returned as one.
    The specific checks upstream stay: they say *why* in terms the caller can
    act on, and this only catches what they missed.
    """
    try:
        return _build_plan(request, analysis, providers, settings)
    except UnknownTransformation as exc:
        raise RequestRejected(
            ValidationResult.failure(
                [
                    ValidationIssue(
                        code=codes.CAPABILITY_UNAVAILABLE,
                        path="outputs",
                        message=(
                            "This installation has no implementation for a step "
                            f"the request needs: {exc}. Ask "
                            "POST /api/v1/capabilities which outputs these "
                            "sources can produce here."
                        ),
                    )
                ],
                phase="feasibility",
            )
        ) from exc


def _resolve_delivery(
    request: GenerationRequest, settings: ContentSettings
) -> list[OutputDelivery]:
    """ADR 0018: turn each output's delivery intent plus the server policy
    into an explicit decision the executor can follow without thinking.
    ``inherit`` + policy off keeps the historical field-presence rule."""
    entries: list[OutputDelivery] = []
    for output in request.outputs:
        delivery = output.delivery
        if delivery.mode == "deliver":
            deliver = True
        elif delivery.mode == "none":
            deliver = False
        else:
            deliver = settings.delivery_default or bool(
                delivery.folder or delivery.filename
            )
        entries.append(
            OutputDelivery(output_id=output.id, deliver=deliver, folder=delivery.folder)
        )
    return entries


def _build_plan(
    request: GenerationRequest,
    analysis: ResourceAnalysis,
    providers: ProviderRegistry,
    settings: ContentSettings,
) -> ExecutionPlan:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    # What this installation reads, in order — used when a text output has to
    # choose a language for itself (`language: "auto"`). Configured once by the
    # operator; the request always outranks it.
    preferred_languages = tuple(
        language
        for language in (settings.language_primary, *settings.languages_secondaries)
        if language
    )
    builder = PlanBuilder(
        {output.id: output.required for output in request.outputs},
        registry=build_registry(providers),
    )

    resolved, _ = resolve_inputs(request)
    sources_by_id = {source.id: source for source in request.sources}
    outputs_by_id = {output.id: output for output in request.outputs}

    # Resolve source authentication once per source (honour or reject `auth`).
    credential_ids = set(settings.credentials)
    credential_by_source: dict[str, str | None] = {}
    for src_index, source in enumerate(request.sources):
        cred, issue = resolve_source_credential(
            source, credential_ids, f"sources[{src_index}]"
        )
        if issue is not None:
            errors.append(issue)
        credential_by_source[source.id] = cred

    for family, preferred in request.preferences.providers.items():
        unknown = [name for name in preferred if name not in providers.names()]
        if unknown:
            warnings.append(
                ValidationIssue(
                    code=codes.PREFERRED_PROVIDER_UNAVAILABLE,
                    path=f"preferences.providers.{family}",
                    message=(
                        f"Preferred provider(s) {unknown} are not installed; "
                        "the default provider will be used."
                    ),
                    details={"unknown": unknown},
                )
            )

    # Function-level import: recipes may reference planner helpers, so importing
    # here (not at module load) avoids a circular import during migration.
    from content.planning.recipes.video import plan_video

    for index, output in _outputs_in_dependency_order(request):
        path = f"outputs[{index}]"

        if output.type not in EXECUTABLE_OUTPUT_TYPES:
            errors.append(
                ValidationIssue(
                    code=codes.OUTPUT_TYPE_NOT_SUPPORTED,
                    path=f"{path}.type",
                    message=(
                        f"Output type '{output.type}' is valid but is not supported "
                        "by the current execution engine."
                    ),
                )
            )
            continue

        if output.scope == "each_item":
            _plan_each_item(
                output,
                index,
                builder,
                request,
                sources_by_id,
                resolved,
                analysis,
                providers,
                credential_by_source,
                errors,
                warnings,
            )
            continue

        if output.scope != "single":
            errors.append(
                ValidationIssue(
                    code=codes.SCOPE_NOT_SUPPORTED,
                    path=f"{path}.scope",
                    message=(
                        f"The scope '{output.scope}' is valid but is not supported "
                        "by the current execution engine."
                    ),
                )
            )
            continue

        source_ids, output_ids = resolved.get(output.id, ([], []))

        # Resolve the source context when the output consumes a source.
        source = source_analysis = capability = provider = None
        if source_ids:
            if len(source_ids) != 1:
                continue  # arity already reported structurally
            source_id = source_ids[0]
            source = sources_by_id[source_id]
            source_analysis = analysis.for_source(source_id)
            if source_analysis is None:
                errors.append(
                    ValidationIssue(
                        code=codes.ANALYSIS_FAILED,
                        path=path,
                        message=f"No analysis available for source '{source_id}'.",
                    )
                )
                continue
            provider = providers.for_source(source)
            if provider is None:
                errors.append(
                    ValidationIssue(
                        code=codes.SOURCE_TYPE_NOT_SUPPORTED,
                        path=path,
                        message=(
                            f"No provider supports source type '{source.type}' "
                            "in this installation."
                        ),
                    )
                )
                continue
            # Feasibility gate, computed by the shared resolver (ADR 0013 R3):
            # the planner offers exactly what /capabilities announces.
            capability = output_feasibility(output.type, source_analysis, providers)
            if capability.status == "unavailable":
                errors.append(
                    ValidationIssue(
                        code=codes.CAPABILITY_UNAVAILABLE,
                        path=path,
                        message=(
                            f"'{output.type}' cannot be produced from source "
                            f"'{source_id}'."
                        ),
                        details=capability.details,
                    )
                )
                continue
            if capability.status == "unknown":
                # Facts were insufficient to decide (e.g. an uncharacterised
                # source): attempt at runtime rather than reject (domain.md §2).
                warnings.append(
                    ValidationIssue(
                        code=codes.CAPABILITY_UNKNOWN,
                        path=path,
                        message=(
                            f"Whether '{output.type}' can be produced from source "
                            f"'{source_id}' could not be determined before "
                            "execution; the step will be attempted."
                        ),
                    )
                )

        credential_id = (
            credential_by_source.get(source.id) if source is not None else None
        )

        if output.type == "transcript":
            _plan_transcript(
                output,
                index,
                request,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                capability,
                provider,
                providers,
                output_ids,
                errors,
                warnings,
                credential_id,
                preferred_languages,
            )
            continue

        if output.type == "summary":
            _plan_summary(
                output,
                index,
                request,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                provider,
                providers,
                output_ids,
                errors,
                warnings,
                credential_id,
                preferred_languages,
            )
            continue

        if output.type == "chapters":
            _plan_chapters(
                output,
                index,
                request,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                provider,
                providers,
                output_ids,
                errors,
                warnings,
                credential_id,
                preferred_languages,
            )
            continue

        if output.type == "translation":
            _plan_translation(
                output,
                index,
                request,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                provider,
                providers,
                output_ids,
                errors,
                warnings,
                credential_id,
            )
            continue

        if output.type == "keyframes" or (
            output.type == "thumbnail" and output.options.wants_generation
        ):
            # Generation composes over acquired video, so it needs a source but
            # not the acquire_thumbnail path the generic tail would take.
            if source is not None:
                _plan_frames_output(
                    output,
                    index,
                    builder,
                    source,
                    source_analysis,
                    provider,
                    providers,
                    errors,
                    warnings,
                    credential_id,
                )
                continue

        if output.type == "pdf":
            # Before the source guard: a PDF of a sibling output needs no source
            # of its own (the referenced output already consumed one).
            _plan_pdf(
                output,
                index,
                request,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                providers,
                settings,
                output_ids,
                errors,
                warnings,
                credential_id,
            )
            continue

        if output.type == "speech":
            # Before the source guard, exactly as `pdf`: reading a sibling
            # output aloud needs no source of its own.
            _plan_speech(
                output,
                index,
                builder,
                outputs_by_id,
                source,
                source_analysis,
                providers,
                output_ids,
                errors,
                credential_id,
            )
            continue

        if source is None:
            continue  # media outputs always consume a source; reported already

        if output.type == "video":
            # Recipe-driven (proof of the operation/implementation split); it
            # composes and binds its own step via the generalized builder API.
            plan_video(
                output=output,
                source=source,
                source_analysis=source_analysis,
                capability=capability,
                provider=provider,
                credential_id=credential_id,
                builder=builder,
                path=path,
                errors=errors,
                warnings=warnings,
            )
            continue

        params: dict = _source_params(source, credential_id)

        if output.type == "audio":
            # "source" = best native stream; an explicit format is reached by
            # extracting/transcoding at acquisition. Only the yt-dlp path
            # transcodes today — file sources stream-copy, so a format change
            # there is still "valid but not implemented".
            if output.options.format != "source":
                if isinstance(source, FileSource):
                    errors.append(
                        ValidationIssue(
                            code=codes.OPTION_NOT_SUPPORTED,
                            path=f"{path}.options.format",
                            message=(
                                f"audio format '{output.options.format}' from a file "
                                "source requires transcoding, which the current "
                                "engine does not implement; use 'source'."
                            ),
                        )
                    )
                    continue
                params["audio_format"] = output.options.format
            audio_langs = _resolve_audio_languages(
                output.options.languages,
                (capability.details or {}).get("languages", []),
                capability.status,
                f"{path}.options.languages",
                warnings,
                original=(capability.details or {}).get("original", ""),
            )
            if audio_langs:
                params["audio_languages"] = audio_langs
            params.update(_sponsorblock_params(output.options.sponsorblock))

            # When the same request already downloads a video carrying exactly
            # this audio, copy the track out of it instead of fetching the
            # stream a second time (D-57).
            donor = _audio_already_in_the_video(
                output,
                audio_langs,
                source,
                source_analysis,
                request,
                resolved,
                providers,
                credential_id,
                builder,
            )
            if donor is not None:
                builder.bound_step(
                    output.id,
                    operation=_OPERATIONS[output.type],
                    provider=_AUDIO_EXTRACTOR,
                    source_id=source.id,
                    params={},
                    depends_on=[donor],
                    resource_key=source_analysis.resource_key,
                )
                continue

        if output.type == "subtitles":
            requested = output.options.languages
            allowed = request.constraints.content.allowed_languages
            if allowed and not set(requested) <= set(allowed):
                errors.append(
                    ValidationIssue(
                        code=codes.CONSTRAINT_UNSATISFIABLE,
                        path=f"{path}.options.languages",
                        message=(
                            "Requested subtitle languages exceed "
                            "constraints.content.allowed_languages."
                        ),
                        details={"requested": requested, "allowed": allowed},
                    )
                )
                continue
            available = set(capability.details.get("manual", [])) | set(
                capability.details.get("automatic", [])
            )
            matching = [lang for lang in requested if lang in available]
            # Language matching only makes sense when the analysis was
            # conclusive; an "unknown" capability is attempted as-is.
            if capability.status == "available" and not matching:
                message = (
                    "None of the requested subtitle languages "
                    f"{requested} were detected on the source."
                )
                if output.required:
                    errors.append(
                        ValidationIssue(
                            code=codes.CAPABILITY_UNAVAILABLE,
                            path=f"{path}.options.languages",
                            message=message,
                            details={"available": sorted(available)},
                        )
                    )
                    continue
                warnings.append(
                    ValidationIssue(
                        code=codes.PARTIAL_OUTPUT,
                        path=f"{path}.options.languages",
                        message=message + " The output may produce no artifact.",
                        details={"available": sorted(available)},
                    )
                )
            params["languages"] = requested
            params["source"] = output.options.source
            params["format"] = output.options.format

        if output.type == "thumbnail":
            params["format"] = output.options.format
            if output.options.max_width is not None:
                # D-11 settled. Only the ffmpeg path can scale, because it is
                # the one producing the pixels; a downloaded thumbnail is
                # whatever the platform published. Silently dropping the option
                # was the dishonest half of that asymmetry — and there is now a
                # real remedy to point at, which is why this refuses rather than
                # warns.
                if provider is not None and provider.name != "ffmpeg":
                    errors.append(
                        ValidationIssue(
                            code=codes.OPTION_NOT_SUPPORTED,
                            path=f"{path}.options.max_width",
                            message=(
                                "A downloaded thumbnail is delivered at the size "
                                "the source published; it cannot be scaled. Use "
                                "options.source 'generate' to cut a frame out of "
                                "the video at the width you want."
                            ),
                            details={"provider": provider.name},
                        )
                    )
                    continue
                params["max_width"] = output.options.max_width

        if output.type == "document_text":
            params["format"] = output.options.format

        if output.type == "markdown":
            # Markdown is the extractor's canonical form (R1: one declaration,
            # no lossy round-trip through plain text).
            params["format"] = "markdown"

        if output.type == "metadata":
            resource = source_analysis.resource.model_dump(mode="json")
            if not output.options.include_raw_provider_data:
                params["resource"] = resource
            else:
                # Raw payloads stay in debug snapshots; expose normalized + note.
                params["resource"] = {
                    **resource,
                    "raw_provider_data": "see_debug_snapshot",
                }

        step_provider = provider
        if output.type in ("document_text", "markdown"):
            # The reader, not the first analysis candidate: for a URL that would
            # be yt-dlp, which cannot extract text.
            reader = _reader_for(source, source_analysis, providers)
            if reader is None:
                errors.append(
                    ValidationIssue(
                        code=codes.CAPABILITY_UNAVAILABLE,
                        path=f"outputs[{index}]",
                        message=(
                            "No installed provider can extract text from this source."
                        ),
                    )
                )
                continue
            step_provider = reader

        builder.bound_step(
            output.id,
            operation=_OPERATIONS[output.type],
            provider=step_provider.name,
            source_id=source.id,
            params=params,
            resource_key=source_analysis.resource_key,
        )

    if errors:
        raise RequestRejected(
            ValidationResult.failure(errors, phase="feasibility", warnings=warnings)
        )

    builder.finalize_required()
    return ExecutionPlan(
        plan_id=new_id("plan"),
        schema_version=request.schema_version,
        analysis_id=analysis.analysis_id,
        steps=builder.steps,
        output_bindings=builder.bindings,
        naming=resolve_naming_plan(request, analysis),
        delivery=_resolve_delivery(request, settings),
        warnings=warnings,
    )
