"""The lossless segment cut (INV-019) — pure arithmetic.

The numbers in these tests come from the measured reproduction: a 159.261 s
file whose SponsorBlock selfpromo segment [138.331, 159] reaches the end
(snapped to the integer metadata duration), plus a synthetic mid-video
removal. The keyframe-snapped stream-copy concat built from these plans was
verified clean on the real file: 0 duplicate PTS, 0 non-monotonic frames,
AV1/Opus preserved.
"""

from content.providers.segments import (
    keep_chunks,
    merge_removals,
    plan_segment_cut,
    remap_chapters,
    remap_time,
    render_concat,
    render_ffmetadata,
    snap_starts,
)

DURATION = 159.261


# --- removal normalization ------------------------------------------------------


def test_end_reaching_segment_removes_the_tail_entirely():
    """The phantom-keep-chunk guard: a segment ending within the tolerance of
    the real duration must extend to the true end of file, however the
    metadata rounded it. [138.331, 159] on a 159.261 s file is the measured
    case — yt-dlp keeps [159, 159.261] and splices the final GOP back in."""
    removals = merge_removals([(138.331, 159.0)], DURATION)
    assert removals == [(138.331, None)]


def test_mid_video_segment_is_kept_as_is():
    assert merge_removals([(30.0, 40.0)], DURATION) == [(30.0, 40.0)]


def test_overlapping_removals_merge():
    removals = merge_removals([(30.0, 40.0), (38.0, 50.0), (60.0, 61.0)], DURATION)
    assert removals == [(30.0, 50.0), (60.0, 61.0)]


def test_degenerate_and_out_of_range_segments_vanish():
    assert merge_removals([(50.0, 50.0), (200.0, 210.0)], DURATION) == []


# --- keep-list inversion --------------------------------------------------------


def test_keep_chunks_around_mid_and_end_removals():
    removals = [(30.0, 40.0), (138.331, None)]
    assert keep_chunks(removals, DURATION) == [(0.0, 30.0), (40.0, 138.331)]


def test_keep_chunks_without_end_removal_runs_to_eof():
    assert keep_chunks([(30.0, 40.0)], DURATION) == [(0.0, 30.0), (40.0, None)]


def test_removal_from_zero_starts_at_the_segment_end():
    assert keep_chunks([(0.0, 12.5)], DURATION) == [(12.5, None)]


# --- keyframe snapping ----------------------------------------------------------


def test_chunk_starts_snap_to_the_nearest_keyframe():
    """Only starts snap (a chunk starting on a keyframe has no GOP lead-in to
    replay); ends fall anywhere (the trailing partial GOP is dropped)."""
    chunks = [(0.0, 30.0), (40.0, 138.331)]
    snapped = snap_starts(chunks, [0.0, 28.56, 39.28, 41.72, 140.0])
    assert snapped == [(0.0, 30.0), (39.28, 138.331)]


def test_no_keyframes_means_no_snapping():
    """Audio-only files have no video keyframes; every boundary is cuttable."""
    chunks = [(40.0, 138.331)]
    assert snap_starts(chunks, []) == chunks


def test_degenerate_chunk_after_snapping_is_dropped():
    assert snap_starts([(10.0, 10.1)], [10.0]) == []


# --- chapter remapping ----------------------------------------------------------


def test_chapters_shift_left_past_a_removal():
    chunks = [(0.0, 30.0), (40.0, None)]
    chapters = remap_chapters(
        [{"start": 50.0, "end": 70.0, "title": "after the cut"}], chunks
    )
    assert chapters == [{"start": 40.0, "end": 60.0, "title": "after the cut"}]


def test_chapter_inside_a_removal_is_dropped():
    """Including the SponsorBlock mark of the removed segment itself."""
    chunks = [(0.0, 138.331)]
    chapters = remap_chapters(
        [{"start": 138.331, "end": 159.0, "title": "[SponsorBlock]: Self Promotion"}],
        chunks,
    )
    assert chapters == []


def test_chapter_straddling_a_removal_contracts():
    chunks = [(0.0, 30.0), (40.0, None)]
    chapters = remap_chapters(
        [{"start": 25.0, "end": 45.0, "title": "straddle"}], chunks
    )
    assert chapters == [{"start": 25.0, "end": 35.0, "title": "straddle"}]


def test_remap_time_clamps_inside_removed_ranges():
    chunks = [(0.0, 30.0), (40.0, None)]
    assert remap_time(35.0, chunks) == 30.0


# --- the full plan --------------------------------------------------------------


def test_plan_for_the_measured_file():
    """The exact reproduction scenario, end to end."""
    plan = plan_segment_cut(
        segments=[(30.0, 40.0), (138.331, 159.0)],
        duration=DURATION,
        keyframes=[0.0, 28.56, 39.28, 41.72],
        chapters=[
            {"start": 0.0, "end": 135.52, "title": "Trapped by plates"},
            {"start": 138.331, "end": 159.0, "title": "[SponsorBlock]: Self Promotion"},
        ],
    )
    assert plan is not None
    assert plan.chunks == ((0.0, 30.0), (39.28, 138.331))
    assert plan.removed_count == 2
    # 159.261 - (30 + 99.051) kept
    assert round(plan.removed_seconds, 3) == round(DURATION - 129.051, 3)
    # The video chapter survives contracted; the removed segment's mark is gone.
    assert [chapter["title"] for chapter in plan.chapters] == ["Trapped by plates"]


def test_no_segments_no_plan():
    assert plan_segment_cut([], DURATION, [], []) is None


def test_nothing_left_to_cut_after_clamping_no_plan():
    assert plan_segment_cut([(200.0, 210.0)], DURATION, [], []) is None


# --- rendering ------------------------------------------------------------------


def test_concat_spec_matches_the_verified_shape():
    """inpoint/outpoint per chunk; the final chunk omits its outpoint so the
    file runs to its natural end — never a phantom trailing range."""
    spec = render_concat("/work/video.mkv", ((0.0, 30.0), (39.28, None)))
    assert spec.splitlines() == [
        "file '/work/video.mkv'",
        "inpoint 0.000000",
        "outpoint 30.000000",
        "file '/work/video.mkv'",
        "inpoint 39.280000",
    ]


def test_concat_spec_escapes_quotes_in_paths():
    spec = render_concat("/work/it's.mkv", ((0.0, None),))
    assert "file '/work/it'\\''s.mkv'" in spec


def test_ffmetadata_carries_remapped_chapters():
    text = render_ffmetadata(
        ({"start": 0.0, "end": 135.52, "title": "One; two = three"},)
    )
    assert text.startswith(";FFMETADATA1\n")
    assert "START=0" in text
    assert "END=135520" in text
    assert "title=One\\; two \\= three" in text


# --- the real cut (external: needs ffmpeg/ffprobe, no network) ------------------


import pytest  # noqa: E402


@pytest.mark.external
def test_fast_cut_preserves_codecs_and_produces_a_clean_stream(tmp_path, settings):
    """The whole fast path on real media: the input's codecs come out
    unchanged (stream copy, INV-019), the removed ranges are gone, chapters
    are remapped, and the result has none of the duplicate-PTS splicing that
    yt-dlp's own end-reaching cut produces."""
    import json as jsonlib
    import shutil
    import subprocess

    from content.domain.plan import PlanStep
    from content.providers.base import ExecutionContext
    from content.providers.ytdlp import SBCUT_PRINT_PREFIX, YtDlpProvider

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")

    # A 20 s h264+aac file with 2 s GOPs and one chapter over the full span.
    metadata = tmp_path / "chapters.ffmeta"
    metadata.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=20000\ntitle=Whole\n"
    )
    source = tmp_path / "video-step.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=20:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=20",
            "-i",
            str(metadata),
            "-map_metadata",
            "2",
            "-c:v",
            "libx264",
            "-g",
            "60",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    # The SBCUT line a download run would have left behind: one mid-video
    # segment and one that reaches the end (the phantom-keep-chunk shape).
    stdout_log = tmp_path / "step.stdout.log"
    segments = [
        {"start_time": 5.0, "end_time": 8.0, "category": "sponsor"},
        {"start_time": 18.9, "end_time": 20.0, "category": "selfpromo"},
    ]
    stdout_log.write_text(f"{SBCUT_PRINT_PREFIX}{jsonlib.dumps(segments)}\n")

    ctx = ExecutionContext(
        settings=settings,
        workdir=tmp_path,
        stdout_log=stdout_log,
        stderr_log=tmp_path / "step.stderr.log",
        timeout_seconds=120,
    )
    step = PlanStep(
        id="step",
        operation="media.acquire_video",
        provider="ytdlp",
        params={
            "uri": "https://example.com/v",
            "sponsorblock": {"remove": ["sponsor", "selfpromo"], "mark": []},
        },
    )
    attributes = YtDlpProvider._apply_fast_sponsorblock_cut(
        YtDlpProvider.__new__(YtDlpProvider), step, ctx, source
    )
    assert attributes == {
        "sponsorblock_cut": "fast",
        "removed_segments": 2,
        "removed_seconds": attributes["removed_seconds"],
    }

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    facts = jsonlib.loads(probe.stdout)
    # Codecs preserved: a stream copy, not a transcode.
    assert [stream["codec_name"] for stream in facts["streams"]] == ["h264", "aac"]
    # Both removals applied: ~3 s mid-video (snapped to the 2 s GOP grid) and
    # the 1.1 s tail; duration lands near 20 - 3 - 1.1 with keyframe slack.
    duration = float(facts["format"]["duration"])
    assert 15.0 < duration < 17.0, duration
    # The chapter survives, contracted to the post-cut timeline.
    chapters = facts.get("chapters") or []
    assert [chapter["tags"]["title"] for chapter in chapters] == ["Whole"]
    assert abs(float(chapters[0]["end_time"]) - duration) < 0.5

    # And the tail defect is absent. Two bars, deliberately different:
    #
    # * A mid-video seam may duplicate up to the B-frame reorder window
    #   (~2 frames, 66 ms): the concat demuxer discards on DTS, so a couple of
    #   reordered frames straddle the outpoint. That micro-splice is inherent
    #   to lossless stream-copy cutting — yt-dlp's own remover has it at every
    #   seam — and is the honest cost of fast mode.
    # * The *end* of the file must be perfectly clean. The historical defect
    #   was 141 frames replayed over the last 4 s (the phantom keep-chunk);
    #   our final chunk carries no outpoint, so not one duplicate may appear
    #   in the tail.
    frames = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=pts_time",
            "-of",
            "csv=p=0",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pts = [line.rstrip(",") for line in frames.stdout.split() if line.strip(",")]
    from collections import Counter

    duplicated = [value for value, count in Counter(pts).items() if count > 1]
    assert len(duplicated) <= 3, f"more than a reorder window of dups: {duplicated}"
    tail = [value for value in duplicated if float(value) > duration - 2.0]
    assert tail == [], f"duplicate PTS in the tail — the EOF defect is back: {tail}"
