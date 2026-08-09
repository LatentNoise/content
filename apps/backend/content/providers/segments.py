"""Lossless segment removal on an acquired media file (INV-019).

In fast mode (``cut_mode: keyframes``, the default) Content removes
SponsorBlock segments itself with a keyframe-snapped, stream-copied ffmpeg
concat — the generalization of HomeTube's cutting strategy ("smart cutting
with no re-encoding"). yt-dlp's own remover is not used, for two measured
reasons (see docs/architecture/invariants.md, INV-019):

* A segment that reaches the end of the video leaves a *phantom keep-chunk*
  behind: yt-dlp snaps the segment end to the metadata duration (an integer;
  YouTube rounds down) but checks it against the real file duration, so a
  sub-second "keep" range survives after the cut. Stream-copying that range
  drags in its whole preceding GOP with offset timestamps — measured 141
  frames replayed over the last 4 s with colliding PTS, the historical
  "the end of the video stutters" defect.
* The natural guard, ``--remove-chapters`` with a ``*start-inf`` time range,
  is broken in the current yt-dlp CLI: ranges alone never attach the
  postprocessor, and ranges combined with any ``--sponsorblock-*`` flag crash
  on an unhashable list.

Cutting only at existing keyframes makes the defect structurally impossible:
a chunk that starts on a keyframe has no GOP lead-in to replay, and the final
chunk simply ends where an end-reaching removal begins. The cost is honesty
about precision — a cut boundary moves to the nearest keyframe (typically
under a second) — and that trade is the whole point of fast mode. The
``precise`` opt-in keeps yt-dlp's ``--force-keyframes-at-cuts`` re-encode for
callers who want exact bounds at transcoding prices.

Everything in this module is pure so the arithmetic is testable without
media files; the yt-dlp provider owns the probes and the ffmpeg invocation.
"""

from __future__ import annotations

from dataclasses import dataclass

# A removal ending this close to the end of the file removes the tail
# entirely. Covers both SponsorBlock's snapped integer ends (metadata says
# 159, the file lasts 159.261) and honest sub-second mismatches.
END_TOLERANCE_SECONDS = 1.0

# Keep-chunks shorter than this after snapping are dropped: a few frames
# between two removals are noise, not content.
MIN_CHUNK_SECONDS = 0.25

# Chapters shorter than this after remapping vanished with the cut content.
MIN_CHAPTER_SECONDS = 0.1


@dataclass(frozen=True)
class SegmentCutPlan:
    """The resolved cut: what to keep, and where the chapters land."""

    # (start, end) keep-ranges in seconds; end None = to the end of file.
    chunks: tuple[tuple[float, float | None], ...]
    # Remapped {"start", "end", "title"} chapter dicts, post-cut timeline.
    chapters: tuple[dict, ...]
    removed_seconds: float
    removed_count: int


def merge_removals(
    segments: list[tuple[float, float]],
    duration: float,
    end_tolerance: float = END_TOLERANCE_SECONDS,
) -> list[tuple[float, float | None]]:
    """Overlapping/adjacent removals coalesced, clamped to the file, ordered.

    An end within *end_tolerance* of the real duration becomes ``None``:
    "remove to the end of the file", however long the file really is. That is
    the guard against the phantom keep-chunk described in the module docstring.
    """
    cleaned: list[tuple[float, float | None]] = []
    for start, end in sorted(segments):
        start = max(0.0, start)
        capped: float | None = min(end, duration)
        if capped is not None and capped >= duration - end_tolerance:
            capped = None
        if capped is not None and capped - start <= 0:
            continue
        if start >= duration:
            continue
        if cleaned and (cleaned[-1][1] is None or start <= cleaned[-1][1]):
            previous_start, previous_end = cleaned[-1]
            if previous_end is None:
                continue
            merged_end = None if capped is None else max(previous_end, capped)
            cleaned[-1] = (previous_start, merged_end)
        else:
            cleaned.append((start, capped))
    return cleaned


def keep_chunks(
    removals: list[tuple[float, float | None]], duration: float
) -> list[tuple[float, float | None]]:
    """Invert removals into the ranges to keep. Last chunk end None = to EOF."""
    chunks: list[tuple[float, float | None]] = []
    cursor = 0.0
    for start, end in removals:
        if start > cursor:
            chunks.append((cursor, start))
        if end is None:
            return chunks
        cursor = max(cursor, end)
    if cursor < duration:
        chunks.append((cursor, None))
    return chunks


def snap_starts(
    chunks: list[tuple[float, float | None]], keyframes: list[float]
) -> list[tuple[float, float | None]]:
    """Snap every chunk start except 0 to the nearest keyframe.

    Only *starts* need snapping: a stream-copy chunk that begins on a keyframe
    has no GOP lead-in to splice back in, while an end can fall anywhere (the
    trailing partial GOP is simply dropped — measured clean). No keyframe list
    (audio-only file) leaves the chunk untouched. Degenerate chunks are
    dropped rather than written.
    """
    snapped: list[tuple[float, float | None]] = []
    for start, end in chunks:
        if start > 0 and keyframes:
            start = min(keyframes, key=lambda keyframe: abs(keyframe - start))
        if end is not None and end - start < MIN_CHUNK_SECONDS:
            continue
        snapped.append((start, end))
    return snapped


def remap_time(instant: float, chunks: list[tuple[float, float | None]]) -> float:
    """Original timeline → post-cut timeline (HomeTube's remap, generalized).

    An instant inside a removed range maps to the seam it collapsed into.
    """
    offset = 0.0
    for start, end in chunks:
        if instant < start:
            return offset
        span_end = end if end is not None else float("inf")
        if instant <= span_end:
            return offset + (instant - start)
        offset += span_end - start
    return offset


def remap_chapters(
    chapters: list[dict], chunks: list[tuple[float, float | None]]
) -> list[dict]:
    """Chapter marks re-expressed on the post-cut timeline.

    Chapters that lived inside removed content collapse to nothing and are
    dropped — including the SponsorBlock marks of the segments just removed.
    """
    remapped = []
    for chapter in chapters:
        start = remap_time(float(chapter["start"]), chunks)
        end = remap_time(float(chapter["end"]), chunks)
        if end - start < MIN_CHAPTER_SECONDS:
            continue
        remapped.append({"start": start, "end": end, "title": chapter.get("title", "")})
    return remapped


def plan_segment_cut(
    segments: list[tuple[float, float]],
    duration: float,
    keyframes: list[float],
    chapters: list[dict],
) -> SegmentCutPlan | None:
    """Resolve a lossless removal, or ``None`` when there is nothing to cut."""
    removals = merge_removals(segments, duration)
    if not removals:
        return None
    chunks = snap_starts(keep_chunks(removals, duration), keyframes)
    if not chunks:
        return None
    if chunks == [(0.0, None)]:
        return None
    kept = sum((end if end is not None else duration) - start for start, end in chunks)
    return SegmentCutPlan(
        chunks=tuple(chunks),
        chapters=tuple(remap_chapters(chapters, chunks)),
        removed_seconds=max(0.0, duration - kept),
        removed_count=len(removals),
    )


def render_concat(source: str, chunks: tuple[tuple[float, float | None], ...]) -> str:
    """The ffmpeg concat-demuxer spec for the keep-list.

    Same shape yt-dlp itself writes (inpoint/outpoint per file entry); the
    final chunk omits its outpoint so the file runs to its natural end.
    """
    escaped = source.replace("'", "'\\''")
    lines = []
    for start, end in chunks:
        lines.append(f"file '{escaped}'")
        lines.append(f"inpoint {start:.6f}")
        if end is not None:
            lines.append(f"outpoint {end:.6f}")
    return "\n".join(lines) + "\n"


def render_ffmetadata(chapters: tuple[dict, ...]) -> str:
    """An ffmetadata document carrying the remapped chapters."""

    def escape(value: str) -> str:
        for char in "\\=;#":
            value = value.replace(char, f"\\{char}")
        return value.replace("\n", "\\\n")

    lines = [";FFMETADATA1"]
    for chapter in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(chapter['start'] * 1000)}",
            f"END={round(chapter['end'] * 1000)}",
            f"title={escape(chapter['title'])}",
        ]
    return "\n".join(lines) + "\n"
