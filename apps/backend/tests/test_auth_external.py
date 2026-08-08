"""Real end-to-end auth: a configured credential threads --cookies all the way
to yt-dlp. A local HTTP server ignores the cookies, so a successful download
proves the flag traverses the pipeline without breaking it. External, local."""

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
from content.providers.ytdlp import YtDlpProvider
from tests.conftest import make_request

HAVE_TOOLS = shutil.which("ffmpeg") is not None and shutil.which("yt-dlp") is not None

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_TOOLS, reason="ffmpeg/yt-dlp not installed"),
]


def test_credential_cookies_reach_ytdlp(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=160x120:rate=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(media / "clip.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        step_timeout_seconds=120,
        allow_private_networks=True,
        credentials={"local": cookies},
    )
    store = Store(settings.db_path)
    providers = ProviderRegistry([YtDlpProvider()])
    service = AnalysisService(store, providers, settings)

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(media), **k
    )
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            payload = {
                "schema_version": "1.0",
                "sources": [
                    {
                        "id": "main",
                        "type": "url",
                        "uri": f"http://127.0.0.1:{port}/clip.mp4",
                        "auth": {"credential_id": "local"},
                    }
                ],
                "outputs": [{"id": "video_main", "type": "video"}],
            }
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
        finally:
            httpd.shutdown()
            thread.join(timeout=5)

    assert store.get_job(result.job_id)["status"] == "succeeded"
    artifacts = store.list_artifacts(result.job_id)
    assert len(artifacts) == 1 and artifacts[0]["size_bytes"] > 0
    steps = store.list_steps(result.job_id)
    assert any(s["operation"] == "media.acquire_video" for s in steps)
