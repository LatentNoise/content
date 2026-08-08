"""Recipe for a ``video`` output.

Today acquisition is still a macro-operation (``media.acquire_video`` bundles
download + remux + embed + sponsorblock in one runner call), so this recipe is a
single bound step. The point of extracting it is the shape: the recipe composes
*operations* via the generalized builder API and never touches provider syntax —
which is exactly what makes it ready to grow (e.g. inserting ``video.cut`` as a
second step over the acquisition material).
"""

from content.domain.request import FileSource
from content.planning.transformations import ACQUIRE_VIDEO, VIDEO_CUT

# video.cut runs on the ffmpeg runner (only implementation today; the registry
# validates it exists — ensure_step raises if ffmpeg is not installed).
_CUT_IMPL = "ffmpeg"


def plan_video(
    *,
    output,
    source,
    source_analysis,
    capability,
    provider,
    credential_id,
    builder,
    path: str,
    errors: list,
    warnings: list,
) -> bool:
    """Plan the video output. Returns False if a feasibility error was recorded
    (the caller then skips this output)."""
    # Param computation helpers still live in the planner during the migration;
    # imported here to avoid a load-time import cycle (planner imports recipes
    # only inside build_plan).
    from content.planning.planner import (
        _plan_video_params,
        _source_params,
        _sponsorblock_params,
    )

    video_params = _plan_video_params(
        output, source, source_analysis, capability, path, errors, warnings
    )
    if video_params is None:
        return False

    params = _source_params(source, credential_id)
    params.update(video_params)
    params.update(_sponsorblock_params(output.options.sponsorblock))
    rk = source_analysis.resource_key
    cut = output.options.cut

    if cut is None:
        # No transform: acquisition is bound directly (macro-op, current shape).
        acquire = builder.ensure_step(
            operation=ACQUIRE_VIDEO,
            implementation=provider.name,
            params=params,
            resource_key=rk,
            source_id=source.id,
            id_suffix=output.id,
            unique_id=False,  # preserves the "acquire_video_<output_id>" id
        )
        builder.bind_output(output.id, acquire)
        return True

    # Both modes are executable: keyframes = stream copy (fast, bounds snap to
    # keyframes), precise = re-encode of the segment (frame-accurate bounds).
    cut_params = {
        "cut": {"start": cut.start_seconds, "duration": cut.duration, "mode": cut.mode}
    }

    if isinstance(source, FileSource):
        # File source: video.cut reads the input file directly (no acquisition).
        cut_params["path"] = source.path
        cut_step = builder.ensure_step(
            operation=VIDEO_CUT,
            implementation=_CUT_IMPL,
            params=cut_params,
            resource_key=rk,
            source_id=source.id,
            id_suffix=output.id,
        )
    else:
        # URL source: acquire (internal material) -> video.cut (bound). The same
        # transform composes on the acquired video without recoding it.
        acquire = builder.ensure_step(
            operation=ACQUIRE_VIDEO,
            implementation=provider.name,
            params=params,
            resource_key=rk,
            source_id=source.id,
            id_suffix=output.id,
        )
        cut_step = builder.ensure_step(
            operation=VIDEO_CUT,
            implementation=_CUT_IMPL,
            params=cut_params,
            inputs=[acquire],
            resource_key=rk,
            source_id=source.id,
            id_suffix=output.id,
        )
    builder.bind_output(output.id, cut_step)
    return True
