"""Real transcript pipeline: a local mkv with an embedded subtitle track goes
through analysis (ffprobe), subtitle extraction (ffmpeg) and the pure
transcript processor. External tools, no network."""

import json
import shutil
import subprocess

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.config import ContentSettings
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.planning.feasibility import output_feasibility
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from tests.conftest import make_request, resolved_capabilities

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed"),
]

SRT = """1
00:00:00,000 --> 00:00:01,500
Bonjour et bienvenue.

2
00:00:01,500 --> 00:00:03,000
Ceci est un test.
"""


@pytest.fixture(scope="module")
def clip_with_subs(tmp_path_factory):
    root = tmp_path_factory.mktemp("input")
    raw = root / "raw.mp4"
    subs = root / "subs.srt"
    final = root / "talk.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    subs.write_text(SRT)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-i",
            str(subs),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            "-c:s",
            "srt",
            "-metadata:s:s:0",
            "language=fra",
            str(final),
        ],
        check=True,
        capture_output=True,
    )
    return final


def test_transcript_from_local_file_with_embedded_subs(tmp_path, clip_with_subs):
    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        step_timeout_seconds=60,
        allowed_input_roots=(clip_with_subs.parent.resolve(),),
    )
    store = Store(settings.db_path)
    providers = ProviderRegistry([FfmpegProvider()], processors=[TranscriptProcessor()])
    service = AnalysisService(store, providers, settings)

    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": str(clip_with_subs)}],
        "outputs": [{"id": "transcript", "type": "transcript"}],
    }
    request = make_request(payload)

    analysis = service.analyze_sources(list(request.sources))
    entry = analysis.sources[0]
    transcript = resolved_capabilities(entry, providers)["transcript.generate"]
    assert transcript.status == "derivable"
    assert transcript.derived_from == ["subtitles"]
    # The concrete languages are planner-level detail, not public capability
    # shape, so they are read where they now live.
    subtitles = output_feasibility("subtitles", entry, providers)
    assert subtitles.details["manual"] == ["fra"]

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
    artifact = store.list_artifacts(result.job_id)[0]
    assert artifact["type"] == "transcript"
    assert artifact["media_type"] == "application/json"

    path = (
        settings.data_dir / "jobs" / result.job_id / "artifacts" / artifact["filename"]
    )
    transcript = json.loads(path.read_text())
    assert transcript["language"] == "fra"
    assert [s["text"] for s in transcript["segments"]] == [
        "Bonjour et bienvenue.",
        "Ceci est un test.",
    ]
