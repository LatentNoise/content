"""Real video execution — external tools, no Internet: yt-dlp downloads from a
local HTTP server; ffmpeg remuxes a generated file."""

import http.server
import shutil
import socketserver
import subprocess
import threading

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.config import ContentSettings
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ytdlp import YtDlpProvider
from tests.conftest import make_request

HAVE_TOOLS = all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "yt-dlp"))

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_TOOLS, reason="ffmpeg/ffprobe/yt-dlp not installed"),
]


def generate_clip(path) -> None:
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
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def pipeline(tmp_path):
    def _make(providers: ProviderRegistry, **settings_overrides):
        settings = ContentSettings(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "data" / "db.sqlite",
            step_timeout_seconds=120,
            **settings_overrides,
        )
        store = Store(settings.db_path)
        service = AnalysisService(store, providers, settings)

        def run(payload: dict) -> tuple[Store, str]:
            request = make_request(payload)
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
            return store, result.job_id

        return run

    return _make


def test_ffmpeg_remux_mp4_to_mkv(tmp_path, pipeline):
    input_root = tmp_path / "input"
    input_root.mkdir()
    clip = input_root / "clip.mp4"
    generate_clip(clip)

    run = pipeline(
        ProviderRegistry([FfmpegProvider()]),
        allowed_input_roots=(input_root.resolve(),),
    )
    store, job_id = run(
        {
            "schema_version": "1.0",
            "sources": [{"id": "vid", "type": "file", "path": str(clip)}],
            "outputs": [
                {"id": "video_mkv", "type": "video", "options": {"container": "mkv"}}
            ],
        }
    )
    assert store.get_job(job_id)["status"] == "succeeded"
    artifact = store.list_artifacts(job_id)[0]
    assert artifact["filename"] == "video_mkv.mkv"
    assert artifact["media_type"] == "video/x-matroska"
    produced = tmp_path / "data" / "jobs" / job_id / "artifacts" / artifact["filename"]
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(produced),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "matroska" in probe.stdout


def test_ytdlp_video_from_local_http(tmp_path, pipeline):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    generate_clip(media_dir / "clip.mp4")

    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(media_dir), **kwargs
    )
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            run = pipeline(
                ProviderRegistry([YtDlpProvider()]),
                allow_private_networks=True,  # localhost test server
            )
            store, job_id = run(
                {
                    "schema_version": "1.0",
                    "sources": [
                        {
                            "id": "main",
                            "type": "url",
                            "uri": f"http://127.0.0.1:{port}/clip.mp4",
                        }
                    ],
                    # Direct URLs expose a single combined format: the b-branch
                    # of the selector chain must kick in.
                    "outputs": [{"id": "video_main", "type": "video"}],
                }
            )
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

    assert store.get_job(job_id)["status"] == "succeeded"
    artifact = store.list_artifacts(job_id)[0]
    assert artifact["type"] == "video"
    assert artifact["size_bytes"] > 0
    assert artifact["provenance"]["producer"]["operation"] == "media.acquire_video"
