"""The public contract: GenerationRequest and its typed sub-models.

This is the executable form of docs/contract.md. Two properties matter:

* the root schema is stable; sub-types are discriminated unions with typed,
  validated options — never free-form dicts;
* nothing here names a tool or a provider (the only exception, by design, is
  ``preferences.providers``, which expresses *preferences* over logical
  provider families).

Reserved output types (video, transcript, ...) are part of the schema so the
namespace is owned by the contract, but they are rejected at feasibility time
with ``output_type_not_supported`` — "valid but not implemented" is a different
answer than "invalid".
"""

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from content.domain.languages import ORIGINAL
from content.naming.sanitize import display_name

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

LocalId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]

SourceRole = Literal[
    "primary", "context", "reference", "instruction", "attachment", "alternative"
]

Scope = Literal[
    "single", "each_source", "each_item", "all_sources", "collection", "group"
]

# Output types the current engine can execute (see docs/architecture.md §9).
EXECUTABLE_OUTPUT_TYPES = (
    "video",
    "audio",
    "metadata",
    "thumbnail",
    "subtitles",
    "transcript",
    "summary",
    "translation",
    "chapters",
    "document_text",
    "markdown",
    "pdf",
    "synced_audio",
    "keyframes",
)

# Declared for forward compatibility; rejected at feasibility with
# output_type_not_supported.
RESERVED_OUTPUT_TYPES = (
    "ocr",
    "embeddings",
    "semantic_index",
    "archive",
    "collection",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- sources -------------------------------------------------------------------


class SourceAuth(StrictModel):
    """A reference to a credential — never a raw secret."""

    credential_id: str | None = None
    session_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "SourceAuth":
        if bool(self.credential_id) == bool(self.session_id):
            raise ValueError("auth requires exactly one of credential_id / session_id")
        return self


class SourceHints(StrictModel):
    """Non-guaranteed indications; may be wrong, ignored or corrected."""

    resource_type: str | None = None
    language: str | None = None
    preferred_provider: str | None = None


class BaseSource(StrictModel):
    id: LocalId
    role: SourceRole | None = None
    hints: SourceHints | None = None
    auth: SourceAuth | None = None


# yt-dlp flags a client must never set: they run commands or redirect the
# engine's controlled output/config/cookies. Rejected in provider_args.
_UNSAFE_PROVIDER_ARGS = {
    "--exec",
    "--exec-before-download",
    "-o",
    "--output",
    "-P",
    "--paths",
    "--load-info-json",
    "--load-info",
    "-a",
    "--batch-file",
    "--config-location",
    "--config",
    "--cookies",
    "--cookies-from-browser",
    "--cache-dir",
    "--print-to-file",
}


class UrlSource(BaseSource):
    type: Literal["url"]
    uri: str = Field(min_length=1)
    # Advanced escape hatch: extra arguments forwarded to the acquisition
    # provider (yt-dlp for URLs), e.g. ["--proxy", "http://p:8080"]. Explicitly
    # provider-specific — a documented exception to keep the contract otherwise
    # clean. Command-execution and output/config/cookies overrides are rejected.
    provider_args: list[str] = Field(default_factory=list)

    @field_validator("provider_args")
    @classmethod
    def _guard_provider_args(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for token in value:
            token = token.strip()
            if not token:
                continue
            flag = token.split("=", 1)[0]
            if flag in _UNSAFE_PROVIDER_ARGS:
                raise ValueError(
                    f"provider argument '{flag}' is not allowed for safety"
                )
            cleaned.append(token)
        return cleaned


class FileSource(BaseSource):
    type: Literal["file"]
    path: str = Field(min_length=1)


class UploadSource(BaseSource):
    type: Literal["upload"]
    upload_id: str = Field(min_length=1)


class TextSource(BaseSource):
    type: Literal["text"]
    content: str
    mime_type: str = "text/plain"


SourceDescriptor = Annotated[
    Union[UrlSource, FileSource, UploadSource, TextSource],
    Field(discriminator="type"),
]


# --- outputs -------------------------------------------------------------------


class Delivery(StrictModel):
    """Where and under what name a produced artifact should also be delivered.

    Artifacts always live in the job's artifact store (source of truth) and are
    downloadable through the API. When ``folder`` and/or ``filename`` are set,
    the engine *additionally* copies each artifact of the output into the
    server's delivery root under ``<folder>/<filename>.<ext>`` — the equivalent
    of dropping the file into a media library. Both are user *intent*: the
    backend sanitizes, never rejects, a name (ADR 0017; D-51) and controls
    every path segment before touching the filesystem.

    ``folder`` is a relative path (``/``-separated); traversal (``.``/``..``),
    absolute paths and backslashes are rejected. ``filename`` is a base name
    without extension; path separators are sanitized into ``" - "`` (an
    ordinary video title contains slashes). The sanitized value is the **base
    name of the artifact family**, not necessarily the literal final filename:
    it replaces the engine's resolved base, while qualifiers, language
    suffixes and numbering still apply to sidecars and multi-artifact outputs
    (ADR 0017). A future major contract version may rename it ``base_name``.

    ``mode`` (ADR 0018) makes the delivery decision explicit instead of
    encoding it in field presence: ``inherit`` (default) lets the server's
    delivery policy decide when the fields carry no intent — and preserves
    the historical behaviour (deliver iff ``folder`` or ``filename`` is set)
    when the policy is off; ``deliver`` always delivers; ``none`` never does,
    and carrying ``folder``/``filename`` alongside it is contradictory intent,
    rejected."""

    mode: Literal["inherit", "deliver", "none"] = "inherit"
    folder: str = ""
    filename: str = ""

    @model_validator(mode="after")
    def _none_carries_no_destination(self) -> "Delivery":
        if self.mode == "none" and (self.folder or self.filename):
            raise ValueError(
                "delivery.mode 'none' contradicts a folder/filename intent"
            )
        return self

    @field_validator("folder")
    @classmethod
    def _validate_folder(cls, value: str) -> str:
        value = value.strip().strip("/")
        if not value:
            return ""
        if "\\" in value:
            raise ValueError("folder must not contain backslashes")
        for segment in value.split("/"):
            if segment in ("", ".", ".."):
                raise ValueError("folder must be a safe relative path")
        return value

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        # Sanitized, not rejected (D-51): "Artist - Song / Official Video" is
        # an ordinary title, and every client was reimplementing this cleanup.
        # The display profile turns separators into " - "; a name that
        # sanitizes to nothing is simply absent intent.
        return display_name(value)


class BaseOutput(StrictModel):
    id: LocalId
    scope: Scope = "single"
    from_sources: list[LocalId] = Field(default_factory=list)
    from_outputs: list[LocalId] = Field(default_factory=list)
    required: bool = True
    delivery: Delivery = Field(default_factory=Delivery)
    metadata: dict = Field(default_factory=dict)


class VideoCodecPreference(StrictModel):
    """D4 semantics: `prefer` falls back with a warning, `require` fails
    feasibility when the codec is unavailable."""

    mode: Literal["prefer", "require"] = "prefer"
    value: Literal["h264", "av1", "vp9"]


def _reject_original(value: list[str] | str, field: str) -> None:
    """``original`` means "the source's own audio language" (ADR 0022) and is
    reserved *inside audio language lists only*. Asking for the original
    subtitle track, or translating *into* "original", has no defined meaning —
    and silently treating the word as an ISO code would produce a confusing
    "language not offered" warning instead of an answer. Invalid is a
    different answer from unsupported, so this is a refusal."""
    languages = [value] if isinstance(value, str) else value
    if ORIGINAL in languages:
        raise ValueError(
            f"'{ORIGINAL}' is reserved for audio language lists (ADR 0022) and "
            f"has no meaning in {field}; name a language code."
        )


class AudioCodecPreference(StrictModel):
    mode: Literal["prefer", "require"] = "prefer"
    value: Literal["aac", "opus"]


class VideoSelection(StrictModel):
    # max_* is always a hard ceiling (D4); a preferred target would be target_*.
    max_height: int | None = Field(default=None, gt=0)
    video_codec: VideoCodecPreference | None = None
    audio_codec: AudioCodecPreference | None = None
    # Ordered audio languages to include as tracks (empty = engine default: the
    # best single track). More than one embeds a multi-audio file, in this
    # order. Crossed at feasibility with what the source offers. Accepts the
    # reserved token "original" — the source's own audio language, resolved
    # per resource at plan time (ADR 0022).
    audio_languages: list[str] = Field(default_factory=list)

    @field_validator("audio_languages")
    @classmethod
    def _normalize_audio_languages(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for lang in value:
            lang = lang.strip()
            if lang and lang not in seen:
                seen.append(lang)
        return seen


class VideoProcessing(StrictModel):
    """How far the engine may transform streams (docs/contract.md §3).

    `auto` lets the engine choose; `copy` forbids any container change;
    `remux` changes the container without re-encoding; `transcode` re-encodes
    (valid, not implemented in V1 -> option_not_supported)."""

    mode: Literal["auto", "copy", "remux", "transcode"] = "auto"
    embed_metadata: bool = True
    embed_thumbnail: bool = False
    embed_chapters: bool = False
    # Subtitle languages to embed into the container (empty = none). Crossed at
    # feasibility with what the source actually offers; unavailable languages
    # are dropped with a warning.
    embed_subtitles: list[str] = Field(default_factory=list)

    @field_validator("embed_subtitles")
    @classmethod
    def _no_original_token(cls, value: list[str]) -> list[str]:
        _reject_original(value, "embed_subtitles")
        return value

    @field_validator("embed_subtitles")
    @classmethod
    def _normalize_embed_subtitles(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for lang in value:
            lang = lang.strip()
            if lang and lang not in seen:
                seen.append(lang)
        return seen


SponsorBlockCategory = Literal[
    "sponsor",
    "intro",
    "outro",
    "selfpromo",
    "preview",
    "filler",
    "interaction",
    "music_offtopic",
]


class SponsorBlockOptions(StrictModel):
    """SponsorBlock segment handling (yt-dlp). Explicit and typed: `remove`
    deletes matching segments from the media, `mark` only records chapters.
    Empty = disabled. Client presets (UI) expand into these lists.

    `cut_mode` is the same trade-off `VideoCut.mode` already names, applied to
    the cuts SponsorBlock makes:

    * `keyframes` (default) stream-copies: yt-dlp cuts on the keyframes the
      stream already has, so the file is written at I/O speed and keeps the
      codecs that were downloaded;
    * `precise` forces a keyframe at each cut, which yt-dlp can only honour by
      **re-encoding the whole file** (its `[ModifyChapters] Re-encoding …`
      pass) — not just the segment around the cut.

    Stream copy is the default because the alternative is not a slightly
    slower cut, it is a full transcode. Measured on a 2 min 19 s 2160p
    download: 17 s to fetch, then 8 min 33 s of CPU, and the result came back
    H.264/Vorbis — ffmpeg's container defaults — in place of the AV1/Opus that
    was actually downloaded: 46% larger for visibly less quality. Nobody asks
    for that in exchange for a tidier cut boundary.

    The stream copy has a known artifact of its own: because the cut lands on
    the nearest existing keyframe, frames just before it can be spliced back
    in with colliding timestamps and the tail stutters. That is a real defect
    and it is not fixed by re-encoding the entire video to hide it.
    """

    remove: list[SponsorBlockCategory] = Field(default_factory=list)
    mark: list[SponsorBlockCategory] = Field(default_factory=list)
    cut_mode: Literal["keyframes", "precise"] = "keyframes"


def _parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS(.ms)`` or a plain seconds string into seconds."""
    value = value.strip()
    if not value:
        raise ValueError("empty timestamp")
    if ":" in value:
        parts = value.split(":")
        if len(parts) > 3:
            raise ValueError(f"invalid timestamp '{value}'")
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
        return seconds
    return float(value)


class VideoCut(StrictModel):
    """Keep only the ``[start, end]`` segment. ``keyframes`` cuts on the nearest
    keyframes without re-encoding (fast, container-lossless, approximate
    bounds); ``precise`` re-encodes the segment (frame-accurate bounds, slower —
    the encoder follows the source container)."""

    start: str = "0"
    end: str
    mode: Literal["keyframes", "precise"] = "keyframes"

    @model_validator(mode="after")
    def _check_range(self) -> "VideoCut":
        if self.duration <= 0:
            raise ValueError("cut end must be after start")
        return self

    @property
    def start_seconds(self) -> float:
        return _parse_timestamp(self.start)

    @property
    def end_seconds(self) -> float:
        return _parse_timestamp(self.end)

    @property
    def duration(self) -> float:
        try:
            return self.end_seconds - self.start_seconds
        except ValueError as exc:
            raise ValueError(f"invalid cut timestamp: {exc}") from exc


class VideoOptions(StrictModel):
    selection: VideoSelection = Field(default_factory=VideoSelection)
    container: Literal["source", "mkv", "mp4"] = "source"
    processing: VideoProcessing = Field(default_factory=VideoProcessing)
    sponsorblock: SponsorBlockOptions = Field(default_factory=SponsorBlockOptions)
    cut: VideoCut | None = None


class VideoOutput(BaseOutput):
    type: Literal["video"]
    options: VideoOptions = Field(default_factory=VideoOptions)


class AudioOptions(StrictModel):
    # "source" = best native stream (URL sources may transcode to reach an
    # explicit format; file sources stream-copy only).
    format: Literal["source", "opus", "mp3", "m4a"] = "source"
    # Ordered preferred audio languages: the first available wins (empty =
    # engine default track). Crossed at feasibility with the source's offer.
    # Accepts the reserved token "original" (ADR 0022).
    languages: list[str] = Field(default_factory=list)
    sponsorblock: SponsorBlockOptions = Field(default_factory=SponsorBlockOptions)

    @field_validator("languages")
    @classmethod
    def _normalize_languages(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for lang in value:
            lang = lang.strip()
            if lang and lang not in seen:
                seen.append(lang)
        return seen


class AudioOutput(BaseOutput):
    type: Literal["audio"]
    options: AudioOptions = Field(default_factory=AudioOptions)


class MetadataOptions(StrictModel):
    include_raw_provider_data: bool = False


class MetadataOutput(BaseOutput):
    type: Literal["metadata"]
    options: MetadataOptions = Field(default_factory=MetadataOptions)


class DocumentTextOptions(StrictModel):
    """Readable content as plain prose. `markdown` keeps the structure, `text`
    flattens it — the extraction is the same, only the serialization differs."""

    format: Literal["text", "markdown"] = "text"


class DocumentTextOutput(BaseOutput):
    type: Literal["document_text"]
    options: DocumentTextOptions = Field(default_factory=DocumentTextOptions)


class MarkdownOptions(StrictModel):
    """Markdown is the canonical form of the text material, so there is nothing
    to choose here yet; the model exists so options can be added compatibly."""


class MarkdownOutput(BaseOutput):
    type: Literal["markdown"]
    options: MarkdownOptions = Field(default_factory=MarkdownOptions)


class PdfOptions(StrictModel):
    """Presentation of the rendered document.

    Deliberately small. A PDF here is a *rendering* of material another output
    already produced, so anything about the content — language, length, style —
    belongs to that output, not to this one.
    """

    page_size: Literal["a4", "letter"] = "a4"
    # Empty means "take the title from the rendered material", which is what the
    # extraction and the summary already carry.
    title: str = ""


class PdfOutput(BaseOutput):
    type: Literal["pdf"]
    options: PdfOptions = Field(default_factory=PdfOptions)


class SyncedAudioOptions(StrictModel):
    """An audio file that carries its own words, timed to them.

    Small, like `PdfOptions`, and for the same reason: what is *said* belongs to
    the transcript, what is *heard* belongs to the audio. Only the pairing is
    decided here.
    """

    # The words go into the file as ID3 SYLT, which is the standard answer and
    # is read by disappointingly few players. An `.lrc` beside the file is what
    # phone players actually open, so it ships by default — off only for
    # somebody who knows their player reads SYLT and wants one file.
    lrc_sidecar: bool = True
    # The ISO 639-2 tag the ID3 frames are labelled with; `auto` takes it from
    # the transcript, which already carries one.
    language: str = "auto"

    @field_validator("language")
    @classmethod
    def _no_original_token(cls, value: str) -> str:
        _reject_original(value, "a synced audio language")
        return value


class SyncedAudioOutput(BaseOutput):
    type: Literal["synced_audio"]
    options: SyncedAudioOptions = Field(default_factory=SyncedAudioOptions)


class ThumbnailOptions(StrictModel):
    """A thumbnail is either the one the source already publishes or one cut
    out of the video. `source` chooses; `auto` prefers the published image when
    there is one, because it is what the author picked."""

    strategy: Literal["best_available"] = "best_available"
    format: Literal["source", "jpeg"] = "source"
    max_width: int | None = Field(default=None, gt=0)
    source: Literal["auto", "download", "generate"] = "auto"
    # An instant (`HH:MM:SS(.ms)` or seconds). Naming one implies generation:
    # a published thumbnail has no instant to honour.
    at: str = ""

    @model_validator(mode="after")
    def _check_at(self) -> "ThumbnailOptions":
        if self.at:
            try:
                if _parse_timestamp(self.at) < 0:
                    raise ValueError("negative timestamp")
            except ValueError as exc:
                raise ValueError(f"invalid 'at' timestamp: {exc}") from exc
            if self.source == "download":
                raise ValueError(
                    "'at' names an instant to extract, which the downloaded "
                    "thumbnail cannot honour; use source 'generate' or 'auto'."
                )
        return self

    @property
    def wants_generation(self) -> bool:
        return self.source == "generate" or bool(self.at)


class ThumbnailOutput(BaseOutput):
    type: Literal["thumbnail"]
    options: ThumbnailOptions = Field(default_factory=ThumbnailOptions)


class KeyframesOptions(StrictModel):
    """A sheet of stills. `every` and `count` are two ways to say how many —
    exactly one of them, because together they would contradict each other and
    neither would be wrong to honour."""

    every: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, gt=0, le=200)
    format: Literal["jpg", "png", "webp"] = "jpg"
    width: int | None = Field(default=None, gt=0)
    # Bounds the sheet to a segment; empty end means "to the end".
    start: str = ""
    end: str = ""

    @model_validator(mode="after")
    def _check_spacing(self) -> "KeyframesOptions":
        if self.every is not None and self.count is not None:
            raise ValueError("set either 'every' or 'count', not both")
        for field in ("start", "end"):
            value = getattr(self, field)
            if value:
                try:
                    _parse_timestamp(value)
                except ValueError as exc:
                    raise ValueError(f"invalid '{field}' timestamp: {exc}") from exc
        return self


class KeyframesOutput(BaseOutput):
    type: Literal["keyframes"]
    options: KeyframesOptions = Field(default_factory=KeyframesOptions)


class SubtitlesOptions(StrictModel):
    languages: list[str] = Field(min_length=1)
    source: Literal["prefer_manual", "manual_only", "automatic_only", "any"] = (
        "prefer_manual"
    )
    format: Literal["srt", "vtt"] = "srt"

    @field_validator("languages")
    @classmethod
    def _no_original_token(cls, value: list[str]) -> list[str]:
        _reject_original(value, "a subtitles output's languages")
        return value


class SubtitlesOutput(BaseOutput):
    type: Literal["subtitles"]
    options: SubtitlesOptions = Field(
        default_factory=lambda: SubtitlesOptions(languages=["en"])
    )


class TranscriptOptions(StrictModel):
    """Contract §8.6. The canonical transcript is structured JSON (D8);
    `text` is a derivation. `speech_to_text` is a valid mode the current
    installation may not implement (option_not_supported)."""

    language: str = "auto"  # "auto" — not "original", which is an audio token
    source: Literal[
        "auto",
        "prefer_existing_subtitles",
        "existing_subtitles_only",
        "speech_to_text",
    ] = "auto"
    timestamps: Literal["none", "segment", "word"] = "segment"
    format: Literal["json", "text"] = "json"


class TranscriptOutput(BaseOutput):
    type: Literal["transcript"]
    options: TranscriptOptions = Field(default_factory=TranscriptOptions)


class SummaryOptions(StrictModel):
    """Contract §8.7. A summary derives from a transcript (or, later, any
    text material); the model/provider belongs to the planner and the
    ``preferences.providers.llm`` family, never to this contract."""

    language: str = "auto"  # auto = follow the transcript's language
    length: Literal["short", "medium", "long"] = "medium"
    style: Literal["structured", "plain", "bullet_points"] = "structured"
    format: Literal["markdown", "text"] = "markdown"


class SummaryOutput(BaseOutput):
    type: Literal["summary"]
    options: SummaryOptions = Field(default_factory=SummaryOptions)


class TranslationOptions(StrictModel):
    """A translation derives from subtitles (cue by cue, timings preserved,
    same SRT/VTT format out) or from a transcript (plain text out). The model
    belongs to the planner and ``preferences.providers.llm``, never here."""

    target_language: str = Field(min_length=2, max_length=16)
    source_language: str = "auto"  # auto = detected from the material

    @field_validator("target_language", "source_language")
    @classmethod
    def _no_original_token(cls, value: str) -> str:
        _reject_original(value, "a translation's languages")
        return value


class TranslationOutput(BaseOutput):
    type: Literal["translation"]
    options: TranslationOptions  # target_language is required — no default


class ChaptersOptions(StrictModel):
    """Chapters come from the source's declared facts when present, else are
    derived from the transcript by an LLM (strictly validated). Same canonical
    data either way; `ffmetadata` is the ffmpeg-ingestible projection."""

    format: Literal["json", "ffmetadata"] = "json"


class ChaptersOutput(BaseOutput):
    type: Literal["chapters"]
    options: ChaptersOptions = Field(default_factory=ChaptersOptions)


class ReservedOutput(BaseOutput):
    """Schema-valid output types the engine does not execute yet."""

    type: Literal[
        "ocr",
        "embeddings",
        "semantic_index",
        "archive",
        "collection",
    ]
    options: dict = Field(default_factory=dict)


ArtifactRequest = Annotated[
    Union[
        VideoOutput,
        AudioOutput,
        MetadataOutput,
        ThumbnailOutput,
        SubtitlesOutput,
        TranscriptOutput,
        SummaryOutput,
        TranslationOutput,
        ChaptersOutput,
        SyncedAudioOutput,
        DocumentTextOutput,
        MarkdownOutput,
        PdfOutput,
        KeyframesOutput,
        ReservedOutput,
    ],
    Field(discriminator="type"),
]


# --- preferences / constraints / execution ------------------------------------


class Preferences(StrictModel):
    language: str | None = None
    optimize_for: Literal["speed", "quality", "cost", "storage", "balanced"] = (
        "balanced"
    )
    execution_location: Literal["local", "any"] = "any"
    providers: dict[str, list[str]] = Field(default_factory=dict)


class PrivacyConstraints(StrictModel):
    allow_cloud_providers: bool = True


class NetworkConstraints(StrictModel):
    allow_remote_processing: bool = True


class ResourceConstraints(StrictModel):
    max_runtime_seconds: int | None = Field(default=None, gt=0)
    max_output_bytes: int | None = Field(default=None, gt=0)


class ContentConstraints(StrictModel):
    allowed_languages: list[str] = Field(default_factory=list)


class Constraints(StrictModel):
    privacy: PrivacyConstraints = Field(default_factory=PrivacyConstraints)
    network: NetworkConstraints = Field(default_factory=NetworkConstraints)
    resources: ResourceConstraints = Field(default_factory=ResourceConstraints)
    content: ContentConstraints = Field(default_factory=ContentConstraints)


class Retention(StrictModel):
    outputs: str = "30d"
    working_files: str = "24h"
    logs: str = "7d"


class ExecutionPolicy(StrictModel):
    mode: Literal["async", "sync"] = "async"
    failure_policy: Literal["fail_fast", "required_only", "best_effort"] = (
        "required_only"
    )
    priority: Literal["low", "normal", "high"] = "normal"
    reuse_existing: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    retention: Retention = Field(default_factory=Retention)


# --- root ----------------------------------------------------------------------


class GenerationRequest(StrictModel):
    """External intent: what the client wants, from what, under which
    preferences, constraints and execution policy.

    The inputs are given **either** inline as ``sources`` **or** by reference to
    an addressable analysis via ``analysis_id`` — exactly one (ADR 0014). The
    exclusivity is declared in the JSON schema (``oneOf``) so it shows in the
    OpenAPI; the stable-coded rejection is emitted at the API boundary.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [{"required": ["sources"]}, {"required": ["analysis_id"]}]
        },
    )

    schema_version: str = Field(pattern=r"^\d+\.\d+$")
    analysis_id: str | None = Field(default=None, min_length=1)
    sources: list[SourceDescriptor] | None = Field(default=None, min_length=1)
    outputs: list[ArtifactRequest] = Field(min_length=1)
    preferences: Preferences = Field(default_factory=Preferences)
    constraints: Constraints = Field(default_factory=Constraints)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    metadata: dict = Field(default_factory=dict)

    def canonical_dump(self) -> dict:
        """Normalized form (defaults materialized) — what gets snapshotted and
        compared for idempotency."""
        return self.model_dump(mode="json")
