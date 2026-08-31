"""The Capability Catalog — the product's single public vocabulary (ADR 0013).

A **capability** is a public action a user can ask for (``video.download``,
``video.clip``, ``summary.generate``…). It is NOT an internal operation and NOT
an ``output_type``: it maps to one or more **recipe variants**, each a fixed,
explicit chain of registry operations (R1: one declaration, read by both the
resolver *and* the planner). Alternative paths are named variants (R2:
``summary.from_subtitles`` vs ``summary.from_audio``).

This module declares only the *static* shape (R5): the option **schema** (type +
semantics) lives here; the resolver supplies the concrete **domains** (allowed
values, bounds, defaults) for a given analyzed source. Nothing here decides
feasibility — that is the resolver's job, from the same declarations.

Invariant (R6): this is the ONLY explicit public list. Providers do not emit
capabilities; clients never redeclare them.
"""

from dataclasses import dataclass

from content.planning import transformations as T

# --- static option schema (R5: TYPE + SEMANTICS only, never values) ------------


@dataclass(frozen=True)
class OptionSpec:
    """The static description of one option. The resolver later attaches the
    dynamic domain (allowed values / bounds / default) for the analyzed source."""

    id: str
    kind: str  # enum | int | bool | language_set | time | string
    title: str
    description: str = ""


# Named bundles of options a recipe variant may expose. A group is applicable to
# a variant only when the variant lists it — so a playlist's video variant can
# omit "cut" simply by not referencing it.
OPTION_GROUPS: dict[str, tuple[OptionSpec, ...]] = {
    "quality": (
        OptionSpec("max_height", "int", "Max resolution", "Upper bound in pixels."),
        OptionSpec("video_codec", "enum", "Preferred video codec"),
        OptionSpec("container", "enum", "Container format"),
    ),
    "embedding": (
        OptionSpec("embed_metadata", "bool", "Embed metadata"),
        OptionSpec("embed_thumbnail", "bool", "Embed thumbnail"),
        OptionSpec("embed_chapters", "bool", "Embed chapters"),
        OptionSpec("embed_subtitles", "language_set", "Embed subtitles"),
    ),
    "sponsorblock": (OptionSpec("sponsorblock", "enum", "SponsorBlock preset"),),
    "cut": (
        OptionSpec("start", "time", "Start", "HH:MM:SS or seconds."),
        OptionSpec("end", "time", "End", "HH:MM:SS or seconds."),
        OptionSpec("mode", "enum", "Cut mode", "keyframes (fast) or precise."),
    ),
    "audio_format": (
        OptionSpec("format", "enum", "Audio format", "source keeps the native stream."),
    ),
    "audio_languages": (
        OptionSpec("audio_languages", "language_set", "Audio languages"),
    ),
    "subtitle_languages": (
        OptionSpec("languages", "language_set", "Subtitle languages"),
    ),
    "transcript": (
        OptionSpec("language", "enum", "Transcript language"),
        OptionSpec("format", "enum", "Transcript format"),
    ),
    "translation": (
        OptionSpec(
            "target_language", "enum", "Target language", "Language to translate into."
        ),
        OptionSpec("source_language", "enum", "Source language", "auto = detected."),
    ),
    "chapters": (
        OptionSpec("format", "enum", "Chapters format", "json or ffmetadata."),
    ),
    "text_format": (
        OptionSpec(
            "format",
            "enum",
            "Text format",
            "markdown keeps the structure; text flattens it.",
        ),
    ),
    "summary": (
        OptionSpec("length", "enum", "Summary length"),
        OptionSpec("format", "enum", "Summary format"),
    ),
    "frame": (
        OptionSpec(
            "at", "time", "Instant", "HH:MM:SS or seconds; bounded by the duration."
        ),
        OptionSpec("format", "enum", "Image format", "jpg, png or webp."),
        OptionSpec("max_width", "int", "Max width", "Upper bound in pixels."),
    ),
    "keyframes": (
        OptionSpec("every", "int", "Interval", "Seconds between frames."),
        OptionSpec("count", "int", "Frame count", "Evenly spaced across the range."),
        OptionSpec("format", "enum", "Image format", "jpg, png or webp."),
        OptionSpec("width", "int", "Max width", "Upper bound in pixels."),
        OptionSpec("start", "time", "Range start"),
        OptionSpec("end", "time", "Range end"),
    ),
    "synced_audio": (
        OptionSpec(
            "lrc_sidecar",
            "bool",
            "Also write an .lrc",
            "ID3 lyrics are read by few players; .lrc is what phones open.",
        ),
        OptionSpec(
            "language",
            "string",
            "Language tag",
            "auto = take it from the transcript.",
        ),
    ),
    "pdf": (
        OptionSpec("page_size", "enum", "Page size", "a4 or letter."),
        OptionSpec(
            "title",
            "string",
            "Document title",
            "Empty = take it from the rendered material.",
        ),
    ),
}


# --- recipe variants (R1: single declaration of a variant's SHAPE) -------------


@dataclass(frozen=True)
class RecipeVariant:
    """One concrete path to fulfil a capability: an ordered chain of registry
    operations, the source materials it needs, and the option groups it exposes.
    Interpreted by the resolver (feasibility) AND the planner (construction)."""

    id: str
    capability_id: str
    operations: tuple[str, ...]
    # Source material kinds the analysis must provide for the first acquisition
    # to be possible (e.g. AUDIO for from_audio). Empty = always satisfiable.
    requires_materials: tuple[str, ...] = ()
    option_groups: tuple[str, ...] = ()


# --- public capabilities -------------------------------------------------------


@dataclass(frozen=True)
class CapabilityDef:
    """A public capability: id, human copy, the contract output it yields, and
    its ordered variants (first feasible one wins — R3 deterministic)."""

    id: str
    title: str
    description: str
    output_type: str
    variants: tuple[RecipeVariant, ...]


def _v(
    id: str, cap: str, ops: tuple, materials: tuple = (), groups: tuple = ()
) -> RecipeVariant:
    return RecipeVariant(id, cap, ops, materials, groups)


CAPABILITY_CATALOG: tuple[CapabilityDef, ...] = (
    CapabilityDef(
        "video.download",
        "Download video",
        "Download the video, optionally multi-audio and with embedded subtitles.",
        "video",
        (
            _v(
                "video.download.direct",
                "video.download",
                (T.ACQUIRE_VIDEO,),
                (T.VIDEO,),
                ("quality", "embedding", "sponsorblock", "audio_languages"),
            ),
        ),
    ),
    CapabilityDef(
        "video.clip",
        "Clip a segment",
        "Download the video and keep only a chosen segment.",
        "video",
        (
            _v(
                "video.clip.download_cut",
                "video.clip",
                (T.ACQUIRE_VIDEO, T.VIDEO_CUT),
                (T.VIDEO,),
                ("quality", "embedding", "sponsorblock", "audio_languages", "cut"),
            ),
        ),
    ),
    CapabilityDef(
        "audio.download",
        "Download audio",
        "Extract the audio track, optionally transcoded.",
        "audio",
        (
            _v(
                "audio.download.direct",
                "audio.download",
                (T.ACQUIRE_AUDIO,),
                (T.AUDIO,),
                ("audio_format", "sponsorblock", "audio_languages"),
            ),
        ),
    ),
    CapabilityDef(
        "subtitles.download",
        "Download subtitles",
        "Deliver subtitle tracks as sidecar files.",
        "subtitles",
        (
            _v(
                "subtitles.download.direct",
                "subtitles.download",
                (T.ACQUIRE_SUBTITLES,),
                (T.SUBTITLES,),
                ("subtitle_languages",),
            ),
        ),
    ),
    CapabilityDef(
        "thumbnail.download",
        "Download thumbnail",
        "Deliver the resource thumbnail image.",
        "thumbnail",
        (
            _v(
                "thumbnail.download.direct",
                "thumbnail.download",
                (T.ACQUIRE_THUMBNAIL,),
                (T.IMAGE,),
            ),
        ),
    ),
    CapabilityDef(
        "thumbnail.generate",
        "Generate a thumbnail",
        "Extract a still from the video at a chosen instant.",
        "thumbnail",
        (
            # Composed over *acquired* video rather than a local file — that is
            # what makes a URL source work, exactly as video.clip does. A second
            # capability on the same output type follows the video.download /
            # video.clip precedent; OUTPUT_CAPABILITY names the preferred one.
            _v(
                "thumbnail.generate.from_video",
                "thumbnail.generate",
                (T.ACQUIRE_VIDEO, T.VIDEO_EXTRACT_FRAMES),
                (T.VIDEO,),
                ("frame",),
            ),
        ),
    ),
    CapabilityDef(
        "keyframes.extract",
        "Extract keyframes",
        "Deliver a sheet of stills taken across the video.",
        "keyframes",
        (
            _v(
                "keyframes.extract.from_video",
                "keyframes.extract",
                (T.ACQUIRE_VIDEO, T.VIDEO_EXTRACT_FRAMES),
                (T.VIDEO,),
                ("keyframes",),
            ),
        ),
    ),
    CapabilityDef(
        "metadata.export",
        "Export metadata",
        "Export the normalized resource metadata.",
        "metadata",
        (_v("metadata.export.direct", "metadata.export", (T.METADATA_EXPORT,)),),
    ),
    CapabilityDef(
        "transcript.generate",
        "Generate transcript",
        "Produce a text transcript, from subtitles when present else from audio.",
        "transcript",
        (
            _v(
                "transcript.from_subtitles",
                "transcript.generate",
                (T.ACQUIRE_SUBTITLES, T.SUBTITLES_TO_TRANSCRIPT),
                (T.SUBTITLES,),
                ("transcript",),
            ),
            _v(
                "transcript.from_audio",
                "transcript.generate",
                (T.ACQUIRE_AUDIO, T.AUDIO_TRANSCRIBE),
                (T.AUDIO,),
                ("transcript",),
            ),
        ),
    ),
    CapabilityDef(
        "text.extract",
        "Extract text",
        "Deliver the readable content of a page or document as plain text.",
        "document_text",
        (
            _v(
                "text.extract.direct",
                "text.extract",
                (T.TEXT_EXTRACT,),
                (T.TEXT,),
                ("text_format",),
            ),
        ),
    ),
    CapabilityDef(
        "markdown.export",
        "Export as Markdown",
        "Deliver the readable content with its structure — title, headings, links.",
        "markdown",
        (
            # The extraction is canonically Markdown, so the export *is* the
            # text material: no lossy round-trip through plain text (R1).
            _v(
                "markdown.export.from_text",
                "markdown.export",
                (T.TEXT_EXTRACT,),
                (T.TEXT,),
            ),
        ),
    ),
    CapabilityDef(
        "pdf.render",
        "Render as PDF",
        "Render readable content as a paginated PDF document.",
        "pdf",
        (
            # Only the source-level chain is declared here, because that is what
            # a capability answers: "what can this source alone yield?". Rendering
            # *another output* (a summary, a transcript, a translation) is a
            # composition expressed with `from_outputs`, exactly like every other
            # derived output — it is not a property of the source.
            _v(
                "pdf.render.from_text",
                "pdf.render",
                (T.TEXT_EXTRACT, T.RENDER_PDF),
                (T.TEXT,),
                ("pdf",),
            ),
        ),
    ),
    CapabilityDef(
        "summary.generate",
        "Generate summary",
        "Summarize the content with an LLM, via a transcript.",
        "summary",
        (
            _v(
                "summary.from_subtitles",
                "summary.generate",
                (T.ACQUIRE_SUBTITLES, T.SUBTITLES_TO_TRANSCRIPT, T.TEXT_SUMMARIZE),
                (T.SUBTITLES,),
                ("summary",),
            ),
            _v(
                "summary.from_audio",
                "summary.generate",
                (T.ACQUIRE_AUDIO, T.AUDIO_TRANSCRIBE, T.TEXT_SUMMARIZE),
                (T.AUDIO,),
                ("summary",),
            ),
            # A readable resource (article, document) needs neither audio nor
            # subtitles: extract the text, then summarize it.
            _v(
                "summary.from_text",
                "summary.generate",
                (T.TEXT_EXTRACT, T.TEXT_SUMMARIZE),
                (T.TEXT,),
                ("summary",),
            ),
        ),
    ),
    CapabilityDef(
        "chapters.generate",
        "Chapters",
        "The source-declared chapters, or chapters derived from the transcript "
        "with an LLM when the source declares none.",
        "chapters",
        (
            _v(
                "chapters.from_source",
                "chapters.generate",
                (T.CHAPTERS_EXPORT,),
                (T.CHAPTERS,),
                ("chapters",),
            ),
            _v(
                "chapters.from_transcript",
                "chapters.generate",
                (T.ACQUIRE_SUBTITLES, T.SUBTITLES_TO_TRANSCRIPT, T.CHAPTERS_DERIVE),
                (T.SUBTITLES,),
                ("chapters",),
            ),
        ),
    ),
    CapabilityDef(
        "translation.generate",
        "Translate",
        "Translate the subtitles (timings preserved) or the transcript into a "
        "target language, with an LLM.",
        "translation",
        (
            _v(
                "translation.from_subtitles",
                "translation.generate",
                (T.ACQUIRE_SUBTITLES, T.TEXT_TRANSLATE),
                (T.SUBTITLES,),
                ("translation",),
            ),
            _v(
                "translation.from_transcript",
                "translation.generate",
                (T.ACQUIRE_SUBTITLES, T.SUBTITLES_TO_TRANSCRIPT, T.TEXT_TRANSLATE),
                (T.SUBTITLES,),
                ("translation",),
            ),
        ),
    ),
)


# Which public capability an output type maps to, for the planner's feasibility
# gate (R3: the planner and resolver ask the same question through this map).
OUTPUT_CAPABILITY: dict[str, str] = {
    "video": "video.download",
    "audio": "audio.download",
    "subtitles": "subtitles.download",
    "thumbnail": "thumbnail.download",
    "metadata": "metadata.export",
    "transcript": "transcript.generate",
    "summary": "summary.generate",
    "translation": "translation.generate",
    "chapters": "chapters.generate",
    "document_text": "text.extract",
    "markdown": "markdown.export",
    "pdf": "pdf.render",
    # thumbnail keeps mapping to the download: it is the preferred path when the
    # source publishes an image, and the planner switches to thumbnail.generate
    # from the request's options (as it does for video.clip).
    "keyframes": "keyframes.extract",
}


def all_capabilities() -> tuple[CapabilityDef, ...]:
    return CAPABILITY_CATALOG


def capability(capability_id: str) -> CapabilityDef | None:
    for cap in CAPABILITY_CATALOG:
        if cap.id == capability_id:
            return cap
    return None


def all_variants() -> list[RecipeVariant]:
    return [variant for cap in CAPABILITY_CATALOG for variant in cap.variants]


def option_specs(group: str) -> tuple[OptionSpec, ...]:
    return OPTION_GROUPS.get(group, ())


KNOWN_OPTION_GROUPS: frozenset[str] = frozenset(OPTION_GROUPS)
