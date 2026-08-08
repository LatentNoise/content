"""Opt-in real-YouTube end-to-end test with real cookies.

Runs ONLY when ``CONTENT_TEST_COOKIES`` points to a cookies file (never in the
default suite, so the gate stays hermetic and non-flaky). Downloads a tiny,
stable public video through the full pipeline with authentication, exercising
cookies + the codec profile ladder against real YouTube.

    CONTENT_TEST_COOKIES=playground/input/cookies.txt \\
        backend/.venv/bin/python -m pytest backend/tests/test_youtube_external.py
"""

import os
import shutil
from pathlib import Path

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.config import ContentSettings
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.providers.base import ProviderRegistry
from content.providers.ytdlp import YtDlpProvider
from tests.conftest import make_request

_COOKIES = os.getenv("CONTENT_TEST_COOKIES", "")

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        not (_COOKIES and Path(_COOKIES).is_file() and shutil.which("yt-dlp")),
        reason="set CONTENT_TEST_COOKIES to a cookies file to run this test",
    ),
]

# "Me at the zoo" — the first YouTube video: 19 s, ~240p, always available.
STABLE_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_authenticated_youtube_download(tmp_path):
    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        step_timeout_seconds=180,
        credentials={"youtube": Path(_COOKIES).resolve()},
    )
    store = Store(settings.db_path)
    providers = ProviderRegistry([YtDlpProvider()])
    service = AnalysisService(store, providers, settings)

    payload = {
        "schema_version": "1.0",
        "sources": [
            {
                "id": "main",
                "type": "url",
                "uri": STABLE_VIDEO,
                "auth": {"credential_id": "youtube"},
            }
        ],
        "outputs": [
            {
                "id": "video_main",
                "type": "video",
                "options": {"selection": {"max_height": 360}},
            },
            {"id": "metadata_main", "type": "metadata", "required": False},
        ],
    }
    request = make_request(payload)

    analysis = service.analyze_sources(list(request.sources))
    assert analysis.sources[0].resource.resource_type == "video"

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

    job = store.get_job(result.job_id)
    assert job["status"] == "succeeded", job["error"]
    artifacts = {
        a["artifact_request_id"]: a for a in store.list_artifacts(result.job_id)
    }
    assert "video_main" in artifacts
    video = artifacts["video_main"]
    assert video["size_bytes"] > 0
    # provenance records which profile/client actually worked
    assert "player_client" in video["provenance"]["attributes"]
