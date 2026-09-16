"""ResourceAnalysis: the normalized view of what a source really is.

``resource_type`` (detected nature) is deliberately distinct from
``SourceDescriptor.type`` (how the source was provided). Public metadata is
normalized (D5); raw provider payloads only survive in debug snapshots.
"""

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, model_validator

from content.domain.errors import ValidationIssue

ResourceType = Literal[
    "video",
    "audio",
    "image",
    "document",
    "pdf",
    "webpage",
    "text",
    "collection",
    "archive",
    "unknown",
]

CapabilityStatus = Literal[
    "available", "derivable", "unavailable", "unknown", "restricted"
]


class CollectionEntry(BaseModel):
    """One member of a collection resource (e.g. a video in a playlist). Kept
    lightweight — the flat listing, not a full per-item analysis."""

    id: str = ""
    title: str = ""
    url: str = ""
    uploader: str = ""
    duration_seconds: float | None = None


class NormalizedResource(BaseModel):
    resource_type: ResourceType = "unknown"
    title: str = ""
    description: str = ""
    author: str = ""
    channel: str = ""
    published_at: str = ""  # ISO date when known
    duration_seconds: float | None = None
    languages: list[str] = Field(default_factory=list)
    mime_type: str = ""
    size_bytes: int | None = None
    # Public engagement metrics, when the provider exposes them (D5 normalized).
    view_count: int | None = None
    like_count: int | None = None
    thumbnail_url: str = ""
    canonical_url: str = ""
    provider_id: str = ""
    detected_provider: str = ""


class StreamInfo(BaseModel):
    type: Literal["video", "audio"]
    codec: str = ""
    width: int | None = None
    height: int | None = None
    language: str = ""


class SubtitleTrack(BaseModel):
    language: str
    origin: Literal["manual", "automatic"]


class ChapterFact(BaseModel):
    """One chapter declared by the source (a fact, provider-described). The
    resolver derives `chapters.from_source` feasibility from their presence;
    generation from a transcript is a separate, non-deterministic variant."""

    start: float
    end: float
    title: str = ""


class MediaFacts(BaseModel):
    """Structured resource facts a provider extracts about a source's media
    (ADR 0013). Providers describe *what the resource is*; the capability
    resolver derives *what can be produced* and the option domains from these —
    they never carry feasibility. Replaces the ad-hoc ``Capability.details``."""

    has_video: bool = False
    has_audio: bool = False
    video_heights: list[int] = Field(default_factory=list)
    video_codecs: list[str] = Field(default_factory=list)
    audio_codecs: list[str] = Field(default_factory=list)
    audio_languages: list[str] = Field(default_factory=list)
    original_audio_language: str = ""


class TextFacts(BaseModel):
    """Facts about a source's readable content, mirroring ``MediaFacts`` for the
    non-media verticals. Like every fact model it says what the resource *is*,
    never what can be produced from it — the resolver derives that (ADR 0013)."""

    has_text: bool = False
    word_count: int | None = None
    # Which extractor produced the reading, for provenance and cache identity.
    extractor: str = ""


# Hosts that are the same site under another name. A person who pasted a
# youtu.be link last week and a youtube.com link today asked for one video.
_SAME_SITE = {
    "youtu.be": "youtube.com",
    "youtube-nocookie.com": "youtube.com",
    "music.youtube.com": "youtube.com",
}


def _site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return _SAME_SITE.get(host, host)


def source_ref_of(resource: NormalizedResource) -> str:
    """A durable name for what a source *is*, for remembering what someone asked.

    The deliberate opposite of `resource_key` (D-12): that key moves with the
    provider and its version, so a yt-dlp upgrade would make every memory
    forget. This one is built from facts the site itself states — the site and
    the id it gives the video or the playlist — so the same video pasted as a
    `youtu.be` link or a `youtube.com` link, today or after an upgrade, is the
    same ref.

    `<site>:<item|collection>:<id>` when the provider knows the id; the page's
    address without its fragment when it does not; empty for things that are
    not addressable at all (an upload), which nothing remembers.
    """
    site = _site_of(resource.canonical_url)
    kind = "collection" if resource.resource_type == "collection" else "item"
    if resource.provider_id and site:
        return f"{site}:{kind}:{resource.provider_id}"
    if resource.canonical_url.startswith(("http://", "https://")):
        parts = urlsplit(resource.canonical_url)
        return "url:" + urlunsplit(
            (
                parts.scheme,
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                parts.query,
                "",
            )
        )
    return ""


class SourceAnalysis(BaseModel):
    source_id: str
    # Stable identity of the underlying resource (provider-computed); the
    # anchor of reuse_existing and of the analysis cache.
    #
    # PUBLIC BUT UNSTABLE (D-12). It is published because operators need it to
    # reason about reuse and cache hits, but its *shape*
    # (`ytdlp:url:<sha256>`) is an internal cache key: it depends on which
    # provider answered and on that provider's version, and it changes whenever
    # either does. Treat it as an opaque string — compare it for equality,
    # never parse it, and do not persist it as an identifier. It may change
    # form without a major version; see docs/contract.md §Stability.
    resource_key: str = ""
    # PUBLIC AND STABLE, unlike `resource_key` above: what a client remembers a
    # person's last request by (ADR 0039). Derived from the resource, so an
    # analysis cached before this field existed carries it too.
    source_ref: str = ""
    resource: NormalizedResource
    streams: list[StreamInfo] = Field(default_factory=list)
    subtitles: list[SubtitleTrack] = Field(default_factory=list)
    media: MediaFacts = Field(default_factory=MediaFacts)
    # Readable content facts (web pages, documents); empty for pure media.
    text: TextFacts = Field(default_factory=TextFacts)
    # Chapters declared by the source (facts; empty = none declared).
    chapters: list[ChapterFact] = Field(default_factory=list)
    # Members of a collection resource (playlists); empty for a single item.
    entries: list[CollectionEntry] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_source_ref(self) -> "SourceAnalysis":
        if not self.source_ref:
            self.source_ref = source_ref_of(self.resource)
        return self

    def subtitle_languages(self) -> set[str]:
        return {track.language for track in self.subtitles}


class ResourceAnalysis(BaseModel):
    analysis_id: str
    created_at: str
    # When the addressable record expires (ADR 0014); None for transient
    # in-memory analyses that were never persisted as a record.
    expires_at: str | None = None
    sources: list[SourceAnalysis] = Field(default_factory=list)

    def for_source(self, source_id: str) -> SourceAnalysis | None:
        for entry in self.sources:
            if entry.source_id == source_id:
                return entry
        return None


class AnalysisError(Exception):
    """A source could not be analyzed; carries a normalized issue.

    ``terminal`` separates the two very different reasons a provider declines.
    The default (False) means "this is not mine" — a routing miss, so the next
    candidate is tried and this failure is never shown. ``terminal=True`` means
    "I recognise this and deliberately refuse it": the chain stops there and the
    caller sees *that* message, instead of a later provider's confused failure.
    Without the distinction a deliberate `.pdf` refusal was overwritten by
    ffmpeg's "ffprobe could not analyze the file" (D-27).
    """

    def __init__(self, issue: ValidationIssue, *, terminal: bool = False):
        self.issue = issue
        self.terminal = terminal
        super().__init__(issue.message)
