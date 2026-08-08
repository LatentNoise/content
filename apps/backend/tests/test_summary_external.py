"""Real summary pipeline: local mkv with embedded subtitles -> transcript ->
summary through a running Ollama daemon (smallest installed model). External,
local-only — skipped when Ollama or ffmpeg are unavailable."""

import json
import shutil
import urllib.error
import urllib.request

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.config import ContentSettings
from content.execution.executor import JobExecutor
from content.persistence.store import Store
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ollama import OllamaProvider
from tests.conftest import make_request, resolved_capabilities
from tests.test_transcript_external import clip_with_subs  # noqa: F401 — fixture

OLLAMA_URL = "http://localhost:11434"


def _smallest_model() -> str | None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as response:
            models = json.loads(response.read()).get("models", [])
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    chat_models = [m for m in models if "embed" not in m.get("name", "").lower()]
    if not chat_models:
        return None
    return min(chat_models, key=lambda m: m.get("size", 0)).get("name")


MODEL = _smallest_model()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed"),
    pytest.mark.skipif(
        MODEL is None, reason="Ollama not running or no model installed"
    ),
]


def test_summary_from_local_file_via_ollama(tmp_path, clip_with_subs):  # noqa: F811
    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        step_timeout_seconds=300,
        allowed_input_roots=(clip_with_subs.parent.resolve(),),
        ollama_model=MODEL,
    )
    store = Store(settings.db_path)
    providers = ProviderRegistry(
        [FfmpegProvider()],
        processors=[TranscriptProcessor(), OllamaProvider(OLLAMA_URL, MODEL)],
    )
    service = AnalysisService(store, providers, settings)

    payload = {
        "schema_version": "1.0",
        "sources": [{"id": "vid", "type": "file", "path": str(clip_with_subs)}],
        "outputs": [
            {
                "id": "summary",
                "type": "summary",
                "options": {"length": "short", "style": "plain"},
            },
        ],
    }
    request = make_request(payload)

    analysis = service.analyze_sources(list(request.sources))
    summary = resolved_capabilities(analysis.sources[0], providers)["summary.generate"]
    assert summary.status == "derivable"
    # R3: the resolver names the variant the planner will actually build.
    assert summary.selected_variant == "summary.from_subtitles"

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
    assert artifact["type"] == "summary"
    assert artifact["provenance"]["attributes"]["model"] == MODEL
    path = (
        settings.data_dir / "jobs" / result.job_id / "artifacts" / artifact["filename"]
    )
    body = path.read_text().strip()
    assert len(body) > 10
    assert "<think>" not in body
