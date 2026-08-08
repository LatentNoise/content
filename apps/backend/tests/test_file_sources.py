"""File sources: path security (hermetic) and the real ffprobe/ffmpeg pipeline
(external — requires ffmpeg, no network)."""

import shutil
import subprocess

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.config import ContentSettings
from content.domain.analysis import AnalysisError
from content.domain.errors import RequestRejected
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider, check_path_allowed
from tests.conftest import make_request, resolved_capabilities

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- path security (hermetic) ---------------------------------------------------


def test_no_roots_configured_disables_file_sources(tmp_path):
    with pytest.raises(AnalysisError) as excinfo:
        check_path_allowed(str(tmp_path / "video.mp4"), ())
    assert excinfo.value.issue.code == "source_type_not_supported"


def test_path_outside_roots_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    with pytest.raises(AnalysisError) as excinfo:
        check_path_allowed(str(secret), (root,))
    assert excinfo.value.issue.code == "path_not_allowed"


def test_dotdot_traversal_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    with pytest.raises(AnalysisError) as excinfo:
        check_path_allowed(str(root / ".." / "secret.txt"), (root,))
    assert excinfo.value.issue.code == "path_not_allowed"


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    link = root / "sneaky.mp4"
    link.symlink_to(outside)
    with pytest.raises(AnalysisError) as excinfo:
        check_path_allowed(str(link), (root,))
    assert excinfo.value.issue.code == "path_not_allowed"


def test_missing_file_is_analysis_failed(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(AnalysisError) as excinfo:
        check_path_allowed(str(root / "absent.mp4"), (root,))
    assert excinfo.value.issue.code == "analysis_failed"


def test_valid_path_is_resolved(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    video = root / "video.mp4"
    video.write_bytes(b"x")
    assert check_path_allowed(str(video), (root,)) == video.resolve()


def test_file_source_without_roots_rejected_at_analysis(tmp_path):
    settings = ContentSettings(
        data_dir=tmp_path / "data", db_path=tmp_path / "db.sqlite"
    )
    store = Store(settings.db_path)
    service = AnalysisService(store, ProviderRegistry([FfmpegProvider()]), settings)
    request = make_request(
        {
            "schema_version": "1.0",
            "sources": [{"id": "vid", "type": "file", "path": str(tmp_path / "v.mp4")}],
            "outputs": [{"id": "audio", "type": "audio"}],
        }
    )
    with pytest.raises(RequestRejected) as excinfo:
        service.analyze_sources(list(request.sources))
    assert excinfo.value.result.errors[0].code == "source_type_not_supported"


# --- real ffprobe/ffmpeg pipeline (external, local-only, no network) ------------


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """A 2 s test video (testsrc + sine audio) generated with ffmpeg."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    root = tmp_path_factory.mktemp("input")
    path = root / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-metadata",
            "title=Sample clip",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.external
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_file_video_to_audio_metadata_thumbnail(tmp_path, sample_video):
    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        step_timeout_seconds=60,
        allowed_input_roots=(sample_video.parent.resolve(),),
    )
    store = Store(settings.db_path)
    providers = ProviderRegistry([FfmpegProvider()])
    service = AnalysisService(store, providers, settings)

    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": str(sample_video)}],
        "outputs": [
            {"id": "audio", "type": "audio"},
            {"id": "meta", "type": "metadata"},
            {"id": "thumb", "type": "thumbnail", "required": False},
        ],
    }
    request = make_request(payload)

    analysis = service.analyze_sources(list(request.sources))
    entry = analysis.sources[0]
    assert entry.resource.resource_type == "video"
    assert entry.resource.title == "Sample clip"
    capabilities = resolved_capabilities(entry, providers)
    assert capabilities["audio.download"].status == "available"
    assert capabilities["thumbnail.download"].status == "available"

    result = submit_generation(
        payload,
        request,
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=service,
    )
    claimed = store.claim_next_queued()
    JobExecutor(store, settings, providers).execute(claimed)

    assert store.get_job(result.job_id)["status"] == "succeeded"
    artifacts = {
        a["artifact_request_id"]: a for a in store.list_artifacts(result.job_id)
    }
    assert set(artifacts) == {"audio", "meta", "thumb"}
    assert artifacts["audio"]["filename"].endswith(".m4a")  # aac stream-copied
    assert artifacts["thumb"]["media_type"] == "image/jpeg"
    assert artifacts["audio"]["provenance"]["producer"]["provider"] == "ffmpeg"
    for artifact in artifacts.values():
        path = (
            settings.data_dir
            / "jobs"
            / result.job_id
            / "artifacts"
            / artifact["filename"]
        )
        assert path.is_file() and path.stat().st_size > 0
