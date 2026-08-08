"""Generated thumbnails and keyframe sheets.

Two capabilities, one operation, one recipe. The tests are organised around the
things that would break quietly: which instants get asked for, which provider
ends up running, and whether a request that cannot be honoured is refused
instead of silently producing the wrong frame.

The ffmpeg invocation itself is asserted through a fake runner — a real ffmpeg
would prove the same argument list at a hundred times the cost, and the opt-in
external test covers the pixels.
"""

import pytest

from content.analysis.service import AnalysisService
from content.config import ContentSettings
from content.domain.errors import RequestRejected
from content.domain.request import (
    EXECUTABLE_OUTPUT_TYPES,
    RESERVED_OUTPUT_TYPES,
    GenerationRequest,
    KeyframesOptions,
    ThumbnailOptions,
)
from content.persistence.store import Store
from content.planning import transformations as T
from content.planning.planner import build_plan
from content.planning.recipes.frames import (
    DEFAULT_POSITION,
    keyframe_instants,
    thumbnail_instant,
)
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from tests.conftest import FakeProvider

# The fake URL provider reports a 120 s video, which is what every duration
# assertion below is relative to.
SOURCE_DURATION = 120.0


@pytest.fixture
def planning(tmp_path):
    """Plan a request against a URL source, with ffmpeg installed."""

    def _plan(outputs, *, with_ffmpeg=True):
        settings = ContentSettings(data_dir=tmp_path, db_path=tmp_path / "c.db")
        providers = ProviderRegistry(
            [FakeProvider()] + ([FfmpegProvider()] if with_ffmpeg else [])
        )
        service = AnalysisService(Store(settings.db_path), providers, settings)
        request = GenerationRequest.model_validate(
            {
                "schema_version": "1.0",
                "sources": [{"id": "v", "type": "url", "uri": "https://x/talk"}],
                "outputs": outputs,
            }
        )
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, settings)

    return _plan


def _frames_step(plan):
    return next(s for s in plan.steps if s.operation == T.VIDEO_EXTRACT_FRAMES)


# --- instant resolution (pure) --------------------------------------------------


def test_the_default_instant_scales_with_the_video():
    """The old behaviour was a fixed 3 s, which lands in the titles of a feature
    and past the end of a two-second clip. A fraction is still a dumb heuristic
    — no scene detection — but it is at least proportional."""
    assert thumbnail_instant(ThumbnailOptions(), 600.0) == 600.0 * DEFAULT_POSITION
    assert thumbnail_instant(ThumbnailOptions(), 10.0) == 10.0 * DEFAULT_POSITION


def test_a_named_instant_wins_over_the_default():
    assert thumbnail_instant(ThumbnailOptions(at="00:01:30"), 600.0) == 90.0
    assert thumbnail_instant(ThumbnailOptions(at="42.5"), 600.0) == 42.5


def test_an_unknown_duration_falls_back_rather_than_failing():
    """A source that reports no duration should still get a thumbnail."""
    assert thumbnail_instant(ThumbnailOptions(), None) == 3.0
    assert thumbnail_instant(ThumbnailOptions(), 0) == 3.0


def test_count_spreads_frames_across_the_whole_video():
    instants = keyframe_instants(KeyframesOptions(count=5), 120.0)
    assert instants == [0.0, 30.0, 60.0, 90.0, 119.95]


def test_count_of_one_is_the_first_frame_not_a_division_by_zero():
    assert keyframe_instants(KeyframesOptions(count=1), 120.0) == [0.0]


def test_every_steps_through_the_video():
    assert keyframe_instants(KeyframesOptions(every=45), 120.0) == [0.0, 45.0, 90.0]


def test_a_range_bounds_the_sheet():
    instants = keyframe_instants(
        KeyframesOptions(count=3, start="00:00:30", end="00:00:50"), 120.0
    )
    assert instants == [30.0, 40.0, 50.0]


def test_no_frame_is_requested_at_the_very_last_instant():
    """Encoders routinely have nothing there. A sheet that silently drops its
    final image is worse than one that lands 50 ms early."""
    instants = keyframe_instants(KeyframesOptions(count=4), 60.0)
    assert instants[-1] < 60.0
    assert instants[-1] == pytest.approx(59.95)


def test_an_interval_cannot_request_an_unbounded_sheet():
    """`every: 1` on a long video would otherwise ask for thousands of files."""
    instants = keyframe_instants(KeyframesOptions(every=0.5), 6000.0)
    assert len(instants) <= 200


# --- the contract ---------------------------------------------------------------


def test_keyframes_moved_from_reserved_to_executable():
    assert "keyframes" in EXECUTABLE_OUTPUT_TYPES
    assert "keyframes" not in RESERVED_OUTPUT_TYPES


def test_every_and_count_cannot_both_be_given():
    """Together they contradict, and neither would be wrong to honour — which is
    exactly when guessing is worst."""
    with pytest.raises(ValueError, match="not both"):
        KeyframesOptions(every=5, count=10)


def test_naming_an_instant_implies_generation():
    assert ThumbnailOptions(at="10").wants_generation
    assert ThumbnailOptions(source="generate").wants_generation
    assert not ThumbnailOptions().wants_generation


def test_an_instant_on_a_downloaded_thumbnail_is_refused_not_ignored():
    """A published image has no instant to honour."""
    with pytest.raises(ValueError, match="cannot honour"):
        ThumbnailOptions(at="10", source="download")


# --- composition ----------------------------------------------------------------


def test_a_url_source_can_generate_a_thumbnail(planning):
    """The proof the recipe composes rather than special-casing files: before
    this, frame extraction was reachable only from a local path."""
    plan = planning([{"id": "t", "type": "thumbnail", "options": {"at": "00:00:30"}}])
    assert [s.operation for s in plan.steps] == [
        T.ACQUIRE_VIDEO,
        T.VIDEO_EXTRACT_FRAMES,
    ]
    frames = _frames_step(plan)
    assert frames.provider == "ffmpeg"
    assert frames.params["timestamps"] == [30.0]
    # The extraction consumes the acquisition rather than re-fetching.
    assert frames.depends_on == [plan.steps[0].id]


def test_the_default_thumbnail_still_downloads(planning):
    """Deterministic selection (R3): the published image is what the author
    chose, so it stays preferred when the source offers one."""
    plan = planning([{"id": "t", "type": "thumbnail"}])
    assert [s.operation for s in plan.steps] == [T.ACQUIRE_THUMBNAIL]


def test_generation_can_be_forced_without_naming_an_instant(planning):
    plan = planning(
        [{"id": "t", "type": "thumbnail", "options": {"source": "generate"}}]
    )
    assert T.VIDEO_EXTRACT_FRAMES in [s.operation for s in plan.steps]
    frames = _frames_step(plan)
    # 20% of 120 s, and the `thumbnail` filter is allowed to pick a good frame
    # nearby because no exact instant was requested.
    assert frames.params["timestamps"] == [24.0]
    assert frames.params["smart"] is True


def test_a_named_instant_disables_the_representative_frame_filter(planning):
    """ffmpeg's `thumbnail` filter returns the most representative frame in a
    window — useful for a poster, wrong when someone said 04:12."""
    plan = planning([{"id": "t", "type": "thumbnail", "options": {"at": "12"}}])
    assert _frames_step(plan).params["smart"] is False


def test_a_keyframe_sheet_is_one_step_with_every_instant(planning):
    plan = planning([{"id": "k", "type": "keyframes", "options": {"count": 4}}])
    frames = _frames_step(plan)
    assert len(frames.params["timestamps"]) == 4
    assert frames.params["format"] == "jpg"


def test_the_image_format_and_width_reach_the_step(planning):
    plan = planning(
        [
            {
                "id": "k",
                "type": "keyframes",
                "options": {"count": 2, "format": "png", "width": 320},
            }
        ]
    )
    frames = _frames_step(plan)
    assert frames.params["format"] == "png"
    assert frames.params["width"] == 320


def test_a_thumbnail_and_a_sheet_share_one_acquisition(planning):
    """The planner mutualizes identical acquisitions; asking for both must not
    download the video twice."""
    plan = planning(
        [
            {"id": "t", "type": "thumbnail", "options": {"source": "generate"}},
            {"id": "k", "type": "keyframes", "options": {"count": 3}},
        ]
    )
    acquisitions = [s for s in plan.steps if s.operation == T.ACQUIRE_VIDEO]
    assert len(acquisitions) == 1


# --- refusals -------------------------------------------------------------------


def test_an_instant_past_the_end_is_refused_with_the_real_duration(planning):
    with pytest.raises(RequestRejected) as exc:
        planning([{"id": "t", "type": "thumbnail", "options": {"at": "99:00:00"}}])
    issue = exc.value.result.errors[0]
    assert issue.code == "invalid_option"
    assert issue.details["duration_seconds"] == SOURCE_DURATION


def test_frames_without_ffmpeg_are_refused_not_crashed(planning):
    """R3 regression. The shared gate checks OUTPUT_CAPABILITY['thumbnail'] =
    thumbnail.download, which yt-dlp satisfies — so generation sailed past it
    and raised UnknownTransformation from the builder, a 500 where a refusal
    belonged. The capability checked must be the capability planned."""
    with pytest.raises(RequestRejected) as exc:
        planning(
            [{"id": "t", "type": "thumbnail", "options": {"source": "generate"}}],
            with_ffmpeg=False,
        )
    issue = exc.value.result.errors[0]
    assert issue.code == "capability_unavailable"
    assert "ffmpeg" in issue.message


def test_max_width_on_a_downloaded_thumbnail_is_refused_with_a_remedy(planning):
    """D-11 settled. Only the path producing the pixels can scale them; a
    downloaded image is whatever the platform published. Silently dropping the
    option was the dishonest half of that asymmetry."""
    with pytest.raises(RequestRejected) as exc:
        planning([{"id": "t", "type": "thumbnail", "options": {"max_width": 640}}])
    issue = exc.value.result.errors[0]
    assert issue.code == "option_not_supported"
    assert "generate" in issue.message, "the refusal must name the way forward"


def test_max_width_is_honoured_on_the_generated_path(planning):
    plan = planning(
        [
            {
                "id": "t",
                "type": "thumbnail",
                "options": {"source": "generate", "max_width": 640},
            }
        ]
    )
    assert _frames_step(plan).params["width"] == 640


# --- hermetic jobs --------------------------------------------------------------


class _RecordingFfmpeg(FfmpegProvider):
    """Stands in for ffmpeg: records the params it was asked for and writes
    plausible files. Asserting the *request* hermetically and the *pixels* in the
    opt-in external tests keeps the fast suite fast without leaving the
    invocation unchecked."""

    def __init__(self):
        super().__init__()
        self.tool_version = "fake-ffmpeg"
        self.calls: list[dict] = []
        self._image_formats = ("jpg", "png", "webp")

    def _extract_frames(self, step, ctx):
        from content.providers.base import ProducedFile
        from content.providers.ffmpeg import _timestamp_slug

        self.calls.append(dict(step.params))
        media_type, suffix, _enc = self.FRAME_FORMATS[step.params["format"]]
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        produced = []
        for index, at in enumerate(step.params["timestamps"]):
            target = ctx.workdir / f"frame-{step.id}-{_timestamp_slug(at)}{suffix}"
            target.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]))
            produced.append(
                ProducedFile(
                    path=target,
                    media_type=media_type,
                    attributes={"at_seconds": at, "index": index},
                )
            )
        return produced

    def _copy_or_remux_video(self, step, ctx):
        from content.providers.base import ProducedFile

        ctx.workdir.mkdir(parents=True, exist_ok=True)
        target = ctx.workdir / f"video-{step.id}.mp4"
        target.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return [ProducedFile(path=target, media_type="video/mp4")]


def _api(tmp_path, provider):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    settings = ContentSettings(data_dir=tmp_path, db_path=tmp_path / "c.db")
    providers = ProviderRegistry([FakeProvider(), provider])
    return TestClient(create_app(settings, providers=providers, start_worker=False))


def _run_job(client, outputs):
    from tests.test_api import run_queued_job

    submitted = client.post(
        "/api/v1/jobs",
        json={
            "schema_version": "1.0",
            "sources": [{"id": "v", "type": "url", "uri": "https://x/talk"}],
            "outputs": outputs,
        },
    )
    assert submitted.status_code == 201, submitted.text
    job_id = submitted.json()["job_id"]
    run_queued_job(client)  # one claim runs every step of the job
    return client.get(f"/api/v1/jobs/{job_id}/artifacts").json()


def test_one_keyframes_request_produces_many_image_artifacts(tmp_path):
    """D7 — one ArtifactRequest, several artifacts — has only ever been
    exercised on subtitles. This is the same contract on images."""
    provider = _RecordingFfmpeg()
    with _api(tmp_path, provider) as client:
        artifacts = _run_job(
            client, [{"id": "k", "type": "keyframes", "options": {"count": 4}}]
        )
    assert len(artifacts) == 4
    assert {a["artifact_request_id"] for a in artifacts} == {"k"}
    assert {a["media_type"] for a in artifacts} == {"image/jpeg"}
    # Provenance names the operation that made them, not the acquisition.
    assert artifacts[0]["provenance"]["producer"]["operation"] == T.VIDEO_EXTRACT_FRAMES


def test_the_generated_thumbnail_reaches_ffmpeg_with_the_right_arguments(tmp_path):
    provider = _RecordingFfmpeg()
    with _api(tmp_path, provider) as client:
        artifacts = _run_job(
            client,
            [
                {
                    "id": "t",
                    "type": "thumbnail",
                    "options": {"at": "00:00:45", "source": "generate"},
                }
            ],
        )
    assert len(artifacts) == 1
    assert provider.calls == [{"timestamps": [45.0], "format": "jpg", "smart": False}]


def test_a_sheet_and_a_thumbnail_do_not_acquire_the_video_twice(tmp_path):
    """The planner mutualizes acquisitions; on a URL source that is the
    difference between one download and two."""
    provider = _RecordingFfmpeg()
    with _api(tmp_path, provider) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "v", "type": "url", "uri": "https://x/talk"}],
                "outputs": [
                    {"id": "t", "type": "thumbnail", "options": {"source": "generate"}},
                    {"id": "k", "type": "keyframes", "options": {"count": 2}},
                ],
            },
        )
        assert submitted.status_code == 201, submitted.text
        job = client.get(f"/api/v1/jobs/{submitted.json()['job_id']}").json()
    acquisitions = [s for s in job["steps"] if s["operation"] == T.ACQUIRE_VIDEO]
    assert len(acquisitions) == 1


# --- real ffmpeg ----------------------------------------------------------------

import shutil  # noqa: E402 - grouped with the external block it serves
import subprocess  # noqa: E402

from content.domain.plan import PlanStep  # noqa: E402
from content.providers.base import ExecutionContext, Material  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

external = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed"),
]


@pytest.fixture
def clip(tmp_path):
    """A 12 s clip whose picture changes over time, so a seek that silently
    failed would produce identical frames and be caught."""
    target = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=12:size=320x240:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        capture_output=True,
        check=True,
    )
    return target


def _run_frames(clip, tmp_path, params):
    provider = FfmpegProvider()
    tmp_path.mkdir(parents=True, exist_ok=True)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    settings = ContentSettings(
        data_dir=tmp_path, db_path=tmp_path / "c.db", allowed_input_roots=(tmp_path,)
    )
    step = PlanStep(
        id="s1",
        operation=T.VIDEO_EXTRACT_FRAMES,
        provider="ffmpeg",
        implementation_version=1,
        params=params,
    )
    ctx = ExecutionContext(
        settings=settings,
        workdir=workdir,
        stdout_log=workdir / "out.log",
        stderr_log=workdir / "err.log",
        timeout_seconds=120,
        input_materials=[Material(path=clip, media_type="video/mp4")],
    )
    return provider.execute(step, ctx)


def _dimensions(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(int(v) for v in result.stdout.strip().split(","))


@pytest.mark.parametrize(
    "image_format,expected_type",
    [
        ("jpg", "image/jpeg"),
        ("png", "image/png"),
        ("webp", "image/webp"),
    ],
)
@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_real_extraction_produces_each_format(
    clip, tmp_path, image_format, expected_type
):
    """Skipped per format the local build cannot encode. webp needs libwebp
    compiled in and many builds (Homebrew's included) ship without it — which
    is why the planner refuses it rather than letting execution fail."""
    if image_format not in FfmpegProvider().image_formats():
        pytest.skip(f"this ffmpeg build cannot encode {image_format}")
    produced = _run_frames(
        clip, tmp_path, {"timestamps": [2.0], "format": image_format}
    )
    assert len(produced) == 1
    assert produced[0].media_type == expected_type
    assert produced[0].path.stat().st_size > 0


@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_the_probe_matches_what_the_build_can_really_do(clip, tmp_path):
    """The probe drives a public refusal, so it has to be right — every format
    it claims must actually produce a file."""
    provider = FfmpegProvider()
    formats = provider.image_formats()
    assert "jpg" in formats and "png" in formats, "every build has these two"
    for image_format in formats:
        produced = _run_frames(
            clip, tmp_path / image_format, {"timestamps": [1.0], "format": image_format}
        )
        assert produced[0].path.stat().st_size > 0, image_format


@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_real_extraction_seeks_to_distinct_instants(clip, tmp_path):
    """The assertion that matters: a seek that silently failed would hand back
    the same frame every time, and every other check would still pass."""
    produced = _run_frames(
        clip, tmp_path, {"timestamps": [0.0, 3.0, 6.5, 11.0], "format": "png"}
    )
    assert len(produced) == 4
    digests = {p.path.read_bytes()[:2048] for p in produced}
    assert len(digests) == 4, "frames must differ — the seek has to move"
    assert [p.attributes["at_seconds"] for p in produced] == [0.0, 3.0, 6.5, 11.0]
    # Named by the instant they show, so a sheet is readable without opening it.
    assert "00h00m06s500" in produced[2].path.name


@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_real_extraction_scales_and_keeps_the_aspect_ratio(clip, tmp_path):
    produced = _run_frames(
        clip, tmp_path, {"timestamps": [1.0], "format": "png", "width": 160}
    )
    assert _dimensions(produced[0].path) == (160, 120)


@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_a_frame_past_the_end_reports_the_instant_it_actually_shows(clip, tmp_path):
    """Provenance describes the bytes. Keeping the requested timestamp would
    label a picture of second 0 as second 999 — in its name and its record."""
    produced = _run_frames(clip, tmp_path, {"timestamps": [999.0], "format": "jpg"})
    assert len(produced) == 1
    attributes = produced[0].attributes
    assert attributes["at_seconds"] == 0.0
    assert attributes["requested_at_seconds"] == 999.0
    assert attributes["clamped"] is True
    assert "00h00m00s000" in produced[0].path.name
