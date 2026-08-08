"""Recipe for still frames: ``thumbnail.generate`` and ``keyframes.extract``.

Both capabilities are the same composition over the same operation — acquire the
video, then pull frames out of it — differing only in how many instants the
planner asks for. Sharing the recipe is what keeps them from drifting into two
subtly different extraction paths (R1).

The instants are resolved **here**, from the analyzed duration, and travel in the
step params. Two consequences worth stating: the provider does no arithmetic, so
what executes is exactly what the plan says; and an out-of-range request is
refused during planning with `invalid_option` rather than becoming an ffmpeg
invocation that quietly returns the wrong frame (R5 — the resolver publishes the
domain, the planner enforces it).
"""

from content.domain.request import FileSource
from content.planning.transformations import ACQUIRE_VIDEO, VIDEO_EXTRACT_FRAMES

# Frame extraction runs on ffmpeg (the only implementation; the registry
# validates it exists — ensure_step raises if ffmpeg is not installed).
_FRAMES_IMPL = "ffmpeg"

# Where to take a poster frame when the caller did not choose. A fraction rather
# than the old fixed 3 s: it scales with the video, so it lands past titles and
# opening black frames on a feature and still lands somewhere sensible on a clip.
# Deliberately a dumb heuristic — scene detection is out of scope (prompt 11).
DEFAULT_POSITION = 0.20

# Used only when the analysis could not determine a duration; the previous
# hard-coded behaviour, kept as the floor rather than failing a thumbnail.
FALLBACK_SEEK_SECONDS = 3.0

# A sheet has to stay a sheet. `every: 1` on a two-hour film would otherwise
# request 7200 artifacts; the contract caps `count`, and this caps the interval
# path that has no count.
MAX_FRAMES = 200


def _clamp_range(options, duration: float) -> tuple[float, float]:
    from content.domain.request import _parse_timestamp

    start = _parse_timestamp(options.start) if options.start else 0.0
    end = _parse_timestamp(options.end) if options.end else duration
    return max(start, 0.0), min(end, duration)


def thumbnail_instant(options, duration: float | None) -> float:
    """The instant a generated thumbnail is taken from."""
    from content.domain.request import _parse_timestamp

    if options.at:
        return _parse_timestamp(options.at)
    if duration and duration > 0:
        return duration * DEFAULT_POSITION
    return FALLBACK_SEEK_SECONDS


def keyframe_instants(options, duration: float) -> list[float]:
    """Evenly spaced instants across the requested range.

    `count` spreads N frames over the range; `every` steps through it. Both are
    inclusive of the start and stay strictly inside the duration, because a
    frame requested at exactly the last second frequently does not exist.
    """
    start, end = _clamp_range(options, duration)
    span = max(end - start, 0.0)
    if options.count:
        if options.count == 1:
            return [start]
        step = span / (options.count - 1) if span > 0 else 0.0
        instants = [start + step * index for index in range(options.count)]
    else:
        every = options.every or max(span / 10.0, 1.0)
        instants = []
        current = start
        while current <= end and len(instants) < MAX_FRAMES:
            instants.append(current)
            current += every
    # Never ask for the very last frame: encoders routinely have nothing there.
    ceiling = max(duration - 0.05, 0.0)
    return [round(min(value, ceiling), 3) for value in instants]


def plan_frames(
    *,
    output,
    source,
    source_analysis,
    provider,
    credential_id,
    builder,
    timestamps: list[float],
    image_format: str,
    width: int | None,
    smart: bool,
) -> bool:
    """Compose acquire_video → video.extract_frames and bind the output.

    The file/URL split mirrors `plan_video`: a local file is read directly,
    while a URL is acquired first and the transform composes on the acquired
    material. That composition is the whole reason a URL source can generate
    frames at all.
    """
    from content.planning.planner import _source_params

    frame_params: dict = {
        "timestamps": timestamps,
        "format": image_format,
        "smart": smart,
    }
    if width:
        frame_params["width"] = int(width)
    resource_key = source_analysis.resource_key

    if isinstance(source, FileSource):
        frame_params["path"] = source.path
        step = builder.ensure_step(
            operation=VIDEO_EXTRACT_FRAMES,
            implementation=_FRAMES_IMPL,
            params=frame_params,
            resource_key=resource_key,
            source_id=source.id,
            id_suffix=output.id,
        )
    else:
        acquire = builder.ensure_step(
            operation=ACQUIRE_VIDEO,
            implementation=provider.name,
            params=_source_params(source, credential_id),
            resource_key=resource_key,
            source_id=source.id,
            id_suffix=output.id,
        )
        step = builder.ensure_step(
            operation=VIDEO_EXTRACT_FRAMES,
            implementation=_FRAMES_IMPL,
            params=frame_params,
            inputs=[acquire],
            resource_key=resource_key,
            source_id=source.id,
            id_suffix=output.id,
        )
    builder.bind_output(output.id, step)
    return True
