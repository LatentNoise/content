"""SourceFacts — the resolver's factual view of an analyzed source (ADR 0013).

The resolver decides feasibility from *facts*, not from provider-emitted
capabilities. This adapter extracts what the current analysis exposes; it is the
seam that Phase 4 widens (heights, codecs, richer language sets) once providers
stop computing capabilities and emit those facts directly.
"""

from dataclasses import dataclass, field

from content.domain.analysis import SourceAnalysis
from content.planning import transformations as T

# Which resource material a source material-kind corresponds to.
_MATERIAL_FACT = {T.VIDEO, T.AUDIO, T.SUBTITLES, T.IMAGE, T.CHAPTERS, T.TEXT}


@dataclass(frozen=True)
class SourceFacts:
    resource_type: str = "unknown"
    has_video: bool = False
    has_audio: bool = False
    has_thumbnail: bool = False
    subtitle_languages: tuple[str, ...] = ()
    audio_languages: tuple[str, ...] = ()
    duration_seconds: float | None = None
    is_collection: bool = False
    has_text: bool = False
    # Whether the analysis produced enough facts to conclude. When False, a
    # material we cannot see is 'unknown' (attemptable) rather than 'absent'
    # (ADR 0013: 'unknown' is reserved for insufficient facts).
    conclusive: bool = True
    _present: frozenset[str] = field(default_factory=frozenset)

    def material_state(self, kind: str) -> str:
        """'present' | 'absent' | 'unknown' for a source material kind. Unknown
        kinds (and SOURCE) are always present — the acquisition needs no prior
        material."""
        if kind not in _MATERIAL_FACT:
            return "present"
        if kind in self._present:
            return "present"
        return "absent" if self.conclusive else "unknown"

    def has_material(self, kind: str) -> bool:
        return self.material_state(kind) == "present"


def facts_from_analysis(entry: SourceAnalysis) -> SourceFacts:
    resource = entry.resource
    rtype = resource.resource_type
    media = entry.media
    has_video = media.has_video or any(s.type == "video" for s in entry.streams)
    has_audio = media.has_audio or any(s.type == "audio" for s in entry.streams)
    subtitle_languages = tuple(sorted({t.language for t in entry.subtitles}))
    audio_languages = tuple(media.audio_languages) or tuple(
        sorted({s.language for s in entry.streams if s.type == "audio" and s.language})
    )
    has_thumbnail = bool(resource.thumbnail_url)
    has_text = entry.text.has_text

    present = set()
    if has_video:
        present.add(T.VIDEO)
    if has_audio:
        present.add(T.AUDIO)
    if subtitle_languages:
        present.add(T.SUBTITLES)
    # A thumbnail can be delivered from an explicit image or a video frame.
    if has_thumbnail or has_video:
        present.add(T.IMAGE)
    # Chapters declared by the source (facts; absence is a real fact for a
    # conclusive analysis — never silently derivable).
    if entry.chapters:
        present.add(T.CHAPTERS)
    # Readable content — the non-media vertical's entry point.
    if has_text:
        present.add(T.TEXT)

    # Facts are conclusive unless the provider could not characterise the
    # resource at all (an unknown type with no media signal).
    conclusive = (
        rtype != "unknown"
        or bool(entry.streams)
        or has_video
        or has_audio
        or bool(subtitle_languages)
        or has_text
    )

    return SourceFacts(
        resource_type=rtype,
        has_video=has_video,
        has_audio=has_audio,
        has_thumbnail=has_thumbnail,
        subtitle_languages=subtitle_languages,
        audio_languages=audio_languages,
        duration_seconds=resource.duration_seconds,
        is_collection=rtype == "collection",
        has_text=has_text,
        conclusive=conclusive,
        _present=frozenset(present),
    )
