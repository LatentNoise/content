"""Central catalog of logical transformations and their implementations.

This is the single source of truth for *what* operations exist (a
``TransformationDefinition`` per operation) and *who* can run them (an
``Implementation`` per (operation, runner)). Recipes compose operations from
here; runners execute them; the PlanBuilder validates every step against this
registry and folds the Content-controlled ``implementation_version`` into the
step signature.

Deliberately lightweight (no graph solver). ``input_kinds``/``output_kinds``
describe the *shape* of the dataflow — not full feasibility: concrete guards
(codecs, containers, precision, available languages) stay in the recipes. The
``properties`` field keeps the model extensible without pretending to encode
feasibility here.

Versioning: ``Implementation.version`` is a semantic version **controlled by
Content** (bump it when the produced bytes change for the same inputs). The
exact tool version (e.g. a yt-dlp/ffmpeg build) is recorded in *provenance*, not
in the signature, so a tool upgrade does not invalidate every cached artifact.
"""

from dataclasses import dataclass

# --- material kinds (the "types" that flow between steps) ----------------------
SOURCE = "source"
VIDEO = "video"
AUDIO = "audio"
SUBTITLES = "subtitles"
IMAGE = "image"
METADATA = "metadata"
TRANSCRIPT = "transcript"
SUMMARY = "summary"
TRANSLATION = "translation"
CHAPTERS = "chapters"
# Readable document content. Canonically **Markdown** — structure (title,
# headings, links) is what makes `markdown.export` faithful and cannot be
# recovered once flattened; the plain-text output is a serialization of it.
TEXT = "text"
# A rendered, paginated document. Distinct from TEXT: TEXT is content, this is
# *presentation* — reflowing it back into content is lossy, which is exactly why
# rendering is its own step rather than a `format` on every text output.
PDF = "pdf"
# An audio file carrying its own words, timed. Distinct from AUDIO: the bytes
# are an audio track either way, but this one has had text written into it, and
# nothing downstream can recover the pairing from the audio alone.
SYNCED_AUDIO = "synced_audio"

# --- operation names (stable, provider-independent verbs) ----------------------
ACQUIRE_VIDEO = "media.acquire_video"
ACQUIRE_AUDIO = "media.acquire_audio"
ACQUIRE_SUBTITLES = "media.acquire_subtitles"
ACQUIRE_THUMBNAIL = "media.acquire_thumbnail"
METADATA_EXPORT = "metadata.export"
SUBTITLES_TO_TRANSCRIPT = "subtitles.to_transcript"
AUDIO_TRANSCRIBE = "audio.transcribe"
TEXT_SUMMARIZE = "text.summarize"
TEXT_TRANSLATE = "text.translate"
CHAPTERS_EXPORT = "chapters.export"
CHAPTERS_DERIVE = "chapters.derive"
VIDEO_CUT = "video.cut"
# Pull still frames out of a video at chosen instants. One operation serves both
# public capabilities (`thumbnail.generate`, `keyframes.extract`) — they differ
# only in how many instants the planner asks for, not in what happens.
VIDEO_EXTRACT_FRAMES = "video.extract_frames"
TEXT_EXTRACT = "text.extract"
# The logical transformation. Deliberately NOT "pdf.render": that string is
# the *public capability id*, and the two are different concepts that happened
# to share a name. One capability, one transformation, several implementations
# (content.pdf.typst, content.pdf.reportlab).
RENDER_PDF = "document.render_pdf"
# Write a timed transcript into an audio file, so a player can show the words
# as they are spoken. `synced_audio.generate` is the public capability id;
# this is the transformation, as with document.render_pdf / pdf.render.
AUDIO_SYNC_TEXT = "audio.sync_text"


@dataclass(frozen=True)
class TransformationDefinition:
    """A logical transformation: what it consumes/produces and coarse,
    planning-relevant properties. Not a feasibility model."""

    operation: str
    input_kinds: tuple[str, ...]
    output_kinds: tuple[str, ...]
    deterministic: bool = True
    cacheable: bool = True
    lossy: bool = False
    macro: bool = False  # still bundles several sub-transforms (acquire_* today)
    properties: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class Implementation:
    """A concrete way to run an operation. ``runner`` is the ProviderRegistry
    key the executor dispatches on; ``name`` is a descriptive strategy id."""

    operation: str
    runner: str
    version: int = 1
    name: str = ""


class UnknownTransformation(Exception):
    """A step referenced an unknown operation or an implementation that does not
    exist / cannot run that operation."""


class TransformationRegistry:
    def __init__(
        self,
        definitions: list[TransformationDefinition],
        implementations: list[Implementation],
    ):
        self._defs = {d.operation: d for d in definitions}
        self._impls: dict[tuple[str, str], Implementation] = {}
        for impl in implementations:
            if impl.operation not in self._defs:
                raise ValueError(
                    f"implementation for unknown operation '{impl.operation}'"
                )
            self._impls[(impl.operation, impl.runner)] = impl

    def definition(self, operation: str) -> TransformationDefinition | None:
        return self._defs.get(operation)

    def implementation(self, operation: str, runner: str) -> Implementation:
        impl = self._impls.get((operation, runner))
        if impl is None:
            raise UnknownTransformation(
                f"no implementation of operation '{operation}' by runner '{runner}'"
            )
        return impl

    def operations(self) -> list[str]:
        return sorted(self._defs)

    def implementations_for(self, operation: str) -> list[Implementation]:
        return [i for (op, _), i in self._impls.items() if op == operation]


def _acquire(op: str, out_kind: str) -> TransformationDefinition:
    # Macro today: acquisition bundles download + remux + embed + sponsorblock.
    return TransformationDefinition(
        operation=op, input_kinds=(SOURCE,), output_kinds=(out_kind,), macro=True
    )


# The operation catalog is static (Content owns the vocabulary). Implementations
# are DERIVED from the installed runners so any runner (real or a test fake) that
# declares a known operation is covered — no independent, drifting declarations.
COLLECTION_MEMBER = "collection.member"

DEFINITIONS: tuple[TransformationDefinition, ...] = (
    # Orchestration, not production (ADR 0019): the step takes one member of a
    # collection through the canonical single-resource pipeline. Its output kind
    # is unknown here on purpose — it is whatever that member's own plan
    # produces, which is decided when the member is analyzed. Not cacheable as a
    # transformation: the member's own steps carry their own signatures, and the
    # member step is deduplicated by the reuse index like any other bound step.
    TransformationDefinition(
        operation=COLLECTION_MEMBER,
        input_kinds=(SOURCE,),
        output_kinds=(),
        macro=True,
    ),
    _acquire(ACQUIRE_VIDEO, VIDEO),
    _acquire(ACQUIRE_AUDIO, AUDIO),
    _acquire(ACQUIRE_SUBTITLES, SUBTITLES),
    _acquire(ACQUIRE_THUMBNAIL, IMAGE),
    TransformationDefinition(
        operation=METADATA_EXPORT, input_kinds=(SOURCE,), output_kinds=(METADATA,)
    ),
    # Readable content out of a non-media resource (a web page, a text or
    # markdown file). Deterministic: the same bytes always extract the same way.
    TransformationDefinition(
        operation=TEXT_EXTRACT, input_kinds=(SOURCE,), output_kinds=(TEXT,)
    ),
    TransformationDefinition(
        operation=SUBTITLES_TO_TRANSCRIPT,
        input_kinds=(SUBTITLES,),
        output_kinds=(TRANSCRIPT,),
    ),
    # Defined but intentionally without an implementation until a speech-to-text
    # runner is installed (ADR 0013): it lets the `*.from_audio` recipe variants
    # be declared and resolved as `unavailable` rather than silently missing.
    TransformationDefinition(
        operation=AUDIO_TRANSCRIBE,
        input_kinds=(AUDIO,),
        output_kinds=(TRANSCRIPT,),
        deterministic=False,
    ),
    TransformationDefinition(
        operation=TEXT_SUMMARIZE,
        input_kinds=(TRANSCRIPT,),
        output_kinds=(SUMMARY,),
        deterministic=False,  # LLM
        lossy=True,
    ),
    # Translate subtitles (cue by cue, timings preserved) or transcript text
    # into a target language. Implemented by the LLM runners (ollama / cloud).
    TransformationDefinition(
        operation=TEXT_TRANSLATE,
        input_kinds=(SUBTITLES, TRANSCRIPT),
        output_kinds=(TRANSLATION,),
        deterministic=False,  # LLM
    ),
    # Chapters: extraction of the source-declared facts (deterministic, the
    # chapter data travels in the step params) vs derivation from a transcript
    # by an LLM runner (non-deterministic, output strictly validated).
    TransformationDefinition(
        operation=CHAPTERS_EXPORT, input_kinds=(SOURCE,), output_kinds=(CHAPTERS,)
    ),
    TransformationDefinition(
        operation=CHAPTERS_DERIVE,
        input_kinds=(TRANSCRIPT,),
        output_kinds=(CHAPTERS,),
        deterministic=False,  # LLM
    ),
    # First truly atomic, composable transform: video -> video (trim a segment).
    TransformationDefinition(
        operation=VIDEO_CUT, input_kinds=(VIDEO,), output_kinds=(VIDEO,)
    ),
    # video -> image[]. Composing over *acquired* video (rather than reading a
    # local file) is what lets a URL source generate frames at all, exactly as
    # video.cut does. Deterministic: the same instants always yield the same
    # frames, so the step is cacheable.
    TransformationDefinition(
        operation=VIDEO_EXTRACT_FRAMES, input_kinds=(VIDEO,), output_kinds=(IMAGE,)
    ),
    # Render readable material into a paginated document. It accepts every
    # text-bearing kind on purpose: rendering is orthogonal to *what* produced
    # the text, which is why it is a step and not a `format` option repeated on
    # summary, transcript, translation and chapters (R1 — one declaration).
    # `lossy`: the rendered page cannot be turned back into its source material.
    # audio + timed text -> the same audio, carrying the text. Two input kinds
    # on purpose, and the only transformation here that takes two: the pairing
    # *is* the product, and neither half can be inferred from the other.
    TransformationDefinition(
        operation=AUDIO_SYNC_TEXT,
        input_kinds=(AUDIO, TRANSCRIPT, SUBTITLES),
        output_kinds=(SYNCED_AUDIO,),
    ),
    TransformationDefinition(
        operation=RENDER_PDF,
        input_kinds=(TEXT, SUMMARY, TRANSCRIPT, TRANSLATION, CHAPTERS),
        output_kinds=(PDF,),
        lossy=True,
    ),
)

_DEFINED_OPS = frozenset(d.operation for d in DEFINITIONS)


def build_registry(providers) -> TransformationRegistry:
    """Registry for the installed runners: the static definition catalog + one
    Implementation per (declared operation, runner). A runner operation absent
    from the catalog is left unregistered on purpose, so a step using it fails
    fast (see ``missing_registrations`` / the drift test)."""
    implementations = [
        Implementation(
            op, entry["name"], version=int(entry.get("implementation_version", 1))
        )
        for entry in providers.describe()
        for op in entry.get("operations", ())
        if op in _DEFINED_OPS
    ]
    return TransformationRegistry(list(DEFINITIONS), implementations)


def default_registry() -> TransformationRegistry:
    """Fallback registry with the definitions only (no implementations) — used
    when a PlanBuilder is created without an installed runner set. Real planning
    always passes a registry built from the providers via ``build_registry``."""
    return TransformationRegistry(list(DEFINITIONS), [])


def missing_registrations(registry: TransformationRegistry, providers) -> list[tuple]:
    """(operation, runner) pairs a runner declares but the registry does not know
    — guards against the registry, recipes and runners drifting apart."""
    missing = []
    for entry in providers.describe():
        for op in entry.get("operations", ()):
            try:
                registry.implementation(op, entry["name"])
            except UnknownTransformation:
                missing.append((op, entry["name"]))
    return missing
