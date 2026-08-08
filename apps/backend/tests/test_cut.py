"""video.cut — the first atomic, composable transform (operation != impl).

Proves the acquire→transform split and source-agnostic composition: the same
video.cut runs after acquisition for a URL, or directly on a file source.
"""

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.request import VideoCut
from content.execution.executor import JobExecutor
from content.planning.planner import build_plan
from content.providers.base import ProviderRegistry
from tests.conftest import FakeFileProvider, FakeProvider, make_request, minimal_payload


@pytest.fixture
def registry():
    # ytdlp (url acquisition) + ffmpeg (file access AND video.cut).
    return ProviderRegistry([FakeProvider(), FakeFileProvider()])


@pytest.fixture
def plan(store, settings, registry):
    service = AnalysisService(store, registry, settings)

    def _plan(payload):
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, registry, settings)

    return _plan


def _url_video_cut(**cut) -> dict:
    options = {"selection": {"max_height": 360}, "cut": {"end": "5", **cut}}
    return minimal_payload(
        outputs=[{"id": "video_main", "type": "video", "options": options}]
    )


def _file_video_cut(**cut) -> dict:
    return {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": "/input/movie.mp4"}],
        "outputs": [
            {
                "id": "video_main",
                "type": "video",
                "options": {"cut": {"end": "5", **cut}},
            }
        ],
    }


# --- contract ------------------------------------------------------------------


def test_cut_rejects_end_before_start():
    with pytest.raises(ValidationError):
        VideoCut(start="10", end="5")


def test_cut_rejects_bad_timestamp():
    with pytest.raises(ValidationError):
        VideoCut(end="abc")


def test_cut_parses_hms_timestamps():
    cut = VideoCut(start="00:00:10", end="00:01:00")
    assert cut.start_seconds == 10 and cut.duration == 50


# --- planning ------------------------------------------------------------------


def test_url_cut_composes_acquire_then_cut(plan):
    p = plan(_url_video_cut())
    acquire = next(s for s in p.steps if s.operation == "media.acquire_video")
    cut = next(s for s in p.steps if s.operation == "video.cut")
    # the transform depends on the (internal) acquisition and is bound to output
    assert acquire.id in cut.depends_on
    assert {b.produced_by for b in p.output_bindings} == {cut.id}
    # operation is dispatched to distinct implementations
    assert acquire.provider == "ytdlp" and cut.provider == "ffmpeg"
    assert cut.params["cut"] == {"start": 0.0, "duration": 5.0, "mode": "keyframes"}


def test_file_cut_reads_the_file_directly(plan):
    p = plan(_file_video_cut())
    assert [s.operation for s in p.steps] == ["video.cut"]
    cut = p.steps[0]
    assert cut.depends_on == [] and cut.params["path"] == "/input/movie.mp4"
    assert {b.produced_by for b in p.output_bindings} == {cut.id}


def test_precise_cut_plans_the_same_chain_with_the_mode(plan):
    p = plan(_url_video_cut(mode="precise"))
    cut = next(s for s in p.steps if s.operation == "video.cut")
    assert cut.params["cut"]["mode"] == "precise"
    assert {b.produced_by for b in p.output_bindings} == {cut.id}


def test_cut_plan_is_deterministic(plan):
    a, b = plan(_url_video_cut()), plan(_url_video_cut())
    assert [s.model_dump(exclude={"id"}) for s in a.steps] == [
        s.model_dump(exclude={"id"}) for s in b.steps
    ]


# --- execution -----------------------------------------------------------------


@pytest.fixture
def pipeline(store, settings, registry):
    service = AnalysisService(store, registry, settings)
    executor = JobExecutor(store, settings, registry)

    def run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=registry,
            analysis_service=service,
        )
        executor.execute(store.claim_next_queued())
        return result.job_id

    return run


def test_url_cut_executes_end_to_end(pipeline, store):
    job_id = pipeline(_url_video_cut())
    assert store.get_job(job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1 and artifacts[0]["artifact_request_id"] == "video_main"
    assert artifacts[0]["provenance"]["producer"]["operation"] == "video.cut"


def test_file_cut_executes_end_to_end(pipeline, store):
    job_id = pipeline(_file_video_cut())
    assert store.get_job(job_id)["status"] == "succeeded"
    assert (
        store.list_artifacts(job_id)[0]["provenance"]["producer"]["operation"]
        == "video.cut"
    )


def test_precise_cut_executes_end_to_end(pipeline, store):
    job_id = pipeline(_url_video_cut(mode="precise"))
    assert store.get_job(job_id)["status"] == "succeeded"


# --- ffmpeg arguments (the mode → command projection) ---------------------------


def _run_cut(tmp_path, settings, suffix: str, mode: str) -> list[str]:
    """Drive the real FfmpegProvider._cut_video with a captured _run."""
    from content.domain.plan import PlanStep
    from content.providers.base import ExecutionContext
    from content.providers.ffmpeg import FfmpegProvider

    source = tmp_path / f"movie{suffix}"
    source.write_bytes(b"fake")
    provider = FfmpegProvider()
    calls: list[list[str]] = []

    def fake_run(args, ctx):
        calls.append([str(a) for a in args])
        (tmp_path / f"cut-c1{suffix}").write_bytes(b"out")

    provider._run = fake_run
    step = PlanStep(
        id="c1",
        operation="video.cut",
        provider="ffmpeg",
        params={
            "path": str(source),
            "cut": {"start": 1.0, "duration": 4.0, "mode": mode},
        },
    )
    file_settings = type(settings)(
        **{**settings.__dict__, "allowed_input_roots": (tmp_path,)}
    )
    ctx = ExecutionContext(
        settings=file_settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=30,
    )
    provider.execute(step, ctx)
    return calls[0]


def test_keyframes_mode_stream_copies(tmp_path, settings):
    args = _run_cut(tmp_path, settings, ".mp4", "keyframes")
    assert ["-c", "copy"] == args[args.index("-c") : args.index("-c") + 2]
    assert "libx264" not in args


def test_precise_mode_reencodes_h264_for_mp4(tmp_path, settings):
    args = _run_cut(tmp_path, settings, ".mp4", "precise")
    assert "libx264" in args and "aac" in args
    assert "copy" not in args[args.index("-c:v") : args.index("-c:v") + 2]


def test_precise_mode_follows_the_webm_container(tmp_path, settings):
    args = _run_cut(tmp_path, settings, ".webm", "precise")
    assert "libvpx-vp9" in args and "libopus" in args
    assert "libx264" not in args


# --- real ffmpeg (external): precise is frame-accurate --------------------------


@pytest.mark.external
def test_precise_cut_duration_matches_the_request(tmp_path, settings):
    """Real ffmpeg: a synthetic 10s clip (keyframes every 3s) cut 2.5→6.5.
    precise must land within a frame or two of the requested 4.0s."""
    import json as jsonlib
    import shutil
    import subprocess

    from content.domain.plan import PlanStep
    from content.providers.base import ExecutionContext
    from content.providers.ffmpeg import FfmpegProvider

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=10:size=320x240:rate=30",
            "-g",
            "90",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    provider = FfmpegProvider()
    file_settings = type(settings)(
        **{**settings.__dict__, "allowed_input_roots": (tmp_path,)}
    )
    ctx = ExecutionContext(
        settings=file_settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=120,
    )
    step = PlanStep(
        id="cx",
        operation="video.cut",
        provider="ffmpeg",
        params={
            "path": str(source),
            "cut": {"start": 2.5, "duration": 4.0, "mode": "precise"},
        },
    )
    produced = provider.execute(step, ctx)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            str(produced[0].path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(jsonlib.loads(probe.stdout)["format"]["duration"])
    assert abs(duration - 4.0) < 0.15, duration
    assert produced[0].attributes["bounds"] == "exact"
