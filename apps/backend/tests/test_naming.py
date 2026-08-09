"""The Artifact Naming Engine (ADR 0017).

Pure rules first (display profile, plan resolution, binding), then the naming
plan's ride through the real pipeline: plan snapshot, registered artifacts,
download filename.
"""

import json

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.analysis import (
    NormalizedResource,
    ResourceAnalysis,
    SourceAnalysis,
)
from content.domain.request import GenerationRequest
from content.execution.executor import JobExecutor
from content.naming.engine import bind_filename, resolve_naming_plan
from content.naming.sanitize import display_name, item_slug
from tests.conftest import make_request, minimal_payload

# --- display profile -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Conference", "My Conference"),
        ("Artist - Song / Official Video", "Artist - Song - Official Video"),
        ("a\\b", "a - b"),
        ('What? A "quote": here*', "What A quote here"),
        ("  spaced   out  ", "spaced out"),
        ("Trailing dots...", "Trailing dots"),
        ("héhé — l'été à Zürich", "héhé — l'été à Zürich"),  # unicode survives
        ("..", ""),
        ("///", ""),
        ("con", "con_"),  # Windows-reserved stem defused
    ],
)
def test_display_name(raw, expected):
    assert display_name(raw) == expected


def test_display_name_caps_length():
    assert len(display_name("x" * 500)) == 120


# --- plan resolution -----------------------------------------------------------


def _naming_plan(outputs, title="My Conference", sources=None):
    request = GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": sources
            or [{"id": "s0", "type": "url", "uri": "https://example.com/v"}],
            "outputs": outputs,
        }
    )
    analysis = ResourceAnalysis(
        analysis_id="an_test",
        created_at="2026-08-07T00:00:00Z",
        sources=[
            SourceAnalysis(
                source_id=request.sources[0].id,
                resource=NormalizedResource(title=title, provider_id="prov123"),
            )
        ],
    )
    return request, resolve_naming_plan(request, analysis)


def _bound(plan, output_id, extension, **kwargs):
    return bind_filename(
        plan.for_output(output_id), output_id=output_id, extension=extension, **kwargs
    )


def test_conference_acceptance_case():
    """The ADR 0017 example, byte for byte."""
    _, plan = _naming_plan(
        [
            {"id": "video_main", "type": "video"},
            {"id": "audio_main", "type": "audio"},
            {"id": "transcript_main", "type": "transcript"},
            {
                "id": "summary_main",
                "type": "summary",
                "from_outputs": ["transcript_main"],
            },
            {
                "id": "subs",
                "type": "subtitles",
                "options": {"languages": ["en", "fr"]},
            },
            {"id": "pdf_main", "type": "pdf", "from_outputs": ["summary_main"]},
        ]
    )
    assert _bound(plan, "video_main", ".mp4") == "My Conference.mp4"
    assert _bound(plan, "audio_main", ".opus") == "My Conference - audio.opus"
    assert _bound(plan, "summary_main", ".md") == "My Conference - summary.md"
    # Provenance-driven: a PDF of the summary is a summary, not a "pdf".
    assert _bound(plan, "pdf_main", ".pdf") == "My Conference - summary.pdf"
    assert (
        _bound(plan, "subs", ".srt", language="en")
        == "My Conference - subtitles - en.srt"
    )
    assert (
        _bound(plan, "subs", ".srt", language="fr")
        == "My Conference - subtitles - fr.srt"
    )


def test_single_output_is_always_primary():
    _, plan = _naming_plan([{"id": "audio_main", "type": "audio"}])
    assert _bound(plan, "audio_main", ".opus") == "My Conference.opus"


def test_pdf_of_a_primary_output_stays_bare():
    _, plan = _naming_plan(
        [
            {"id": "md", "type": "markdown"},
            {"id": "pdf", "type": "pdf", "from_outputs": ["md"]},
        ]
    )
    assert _bound(plan, "md", ".md") == "My Conference.md"
    assert _bound(plan, "pdf", ".pdf") == "My Conference.pdf"


def test_duplicate_type_qualifies_by_output_id():
    _, plan = _naming_plan(
        [
            {"id": "video_hd", "type": "video"},
            {"id": "video_sd", "type": "video"},
        ]
    )
    assert _bound(plan, "video_hd", ".mp4") == "My Conference.mp4"
    assert _bound(plan, "video_sd", ".mp4") == "My Conference - video_sd.mp4"


def test_title_with_separator_is_sanitized_not_refused():
    _, plan = _naming_plan(
        [{"id": "video_main", "type": "video"}],
        title="Artist - Song / Official Video",
    )
    assert _bound(plan, "video_main", ".mp4") == ("Artist - Song - Official Video.mp4")


def test_client_filename_names_the_family_not_the_literal_file():
    """`delivery.filename` is the base name of the artifact *family*: the
    primary artifact gets the literal `<filename>.<ext>`, sidecars keep their
    qualifiers and language suffixes (contract §3; a future contract major may
    rename the field `base_name`)."""
    _, plan = _naming_plan(
        [
            {
                "id": "video_main",
                "type": "video",
                "delivery": {"filename": "Chosen Name"},
            },
            {
                "id": "subs",
                "type": "subtitles",
                "options": {"languages": ["en"]},
                "delivery": {"filename": "Chosen Name"},
            },
        ]
    )
    assert _bound(plan, "video_main", ".mp4") == "Chosen Name.mp4"
    assert (
        _bound(plan, "subs", ".srt", language="en")
        == "Chosen Name - subtitles - en.srt"
    )


def test_fallback_chain_file_stem_then_provider_id_then_output_id():
    # No title: a file source's stem wins.
    _, plan = _naming_plan(
        [{"id": "video_main", "type": "video"}],
        title="",
        sources=[{"id": "s0", "type": "file", "path": "/in/Holiday Cut.mov"}],
    )
    assert _bound(plan, "video_main", ".mp4") == "Holiday Cut.mp4"
    # No title, no file name: the provider resource id.
    _, plan = _naming_plan([{"id": "video_main", "type": "video"}], title="")
    assert _bound(plan, "video_main", ".mp4") == "prov123.mp4"
    # Nothing at all (no analysis): the output id — the previous behaviour.
    request = GenerationRequest.model_validate(minimal_payload())
    plan = resolve_naming_plan(request, None)
    assert (
        bind_filename(
            plan.for_output("audio_main"), output_id="audio_main", extension=".m4a"
        )
        == "audio_main.m4a"
    )


def test_bind_without_naming_plan_degrades_to_output_id():
    # Plans snapshotted before ADR 0017 carry no naming; binding still works.
    assert (
        bind_filename(None, output_id="video_main", extension=".mp4")
        == "video_main.mp4"
    )


def test_numbering_only_when_cardinality_requires_it():
    _, plan = _naming_plan([{"id": "kf", "type": "keyframes"}])
    one = _bound(plan, "kf", ".jpg")
    many = [_bound(plan, "kf", ".jpg", item_index=i, item_count=3) for i in (1, 2, 3)]
    assert one == "My Conference.jpg"
    assert many == [
        "My Conference - 01.jpg",
        "My Conference - 02.jpg",
        "My Conference - 03.jpg",
    ]
    # Language-addressed siblings are already distinct — no numbers. (A
    # subtitles-only request makes subtitles primary: bare base + language.)
    _, plan = _naming_plan(
        [{"id": "subs", "type": "subtitles", "options": {"languages": ["en"]}}]
    )
    assert (
        _bound(plan, "subs", ".srt", language="en", item_index=1, item_count=2)
        == "My Conference - en.srt"
    )


def test_each_item_uses_the_entry_title():
    request = GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [{"id": "s0", "type": "url", "uri": "https://x/playlist"}],
            "outputs": [{"id": "video_items", "type": "video", "scope": "each_item"}],
        }
    )
    analysis = ResourceAnalysis(
        analysis_id="an_pl",
        created_at="2026-08-07T00:00:00Z",
        sources=[
            SourceAnalysis(
                source_id="s0",
                resource=NormalizedResource(
                    resource_type="collection", title="Fake playlist"
                ),
                entries=[
                    {"id": "v1", "title": "First", "url": "https://x/v1"},
                    {"id": "v2", "title": "", "url": "https://x/v2"},
                ],
            )
        ],
    )
    plan = resolve_naming_plan(request, analysis)
    naming = plan.for_output("video_items")
    label_1 = item_slug("First", 1)
    assert (
        bind_filename(
            naming, output_id="video_items", extension=".mp4", item_label=label_1
        )
        == "001 - First.mp4"
    )
    # An untitled member keeps its ordinal and falls back to the collection's
    # own base, so ordering survives even when a member has no title of its own.
    label_2 = item_slug("v2", 2)
    assert (
        bind_filename(
            naming, output_id="video_items", extension=".mp4", item_label=label_2
        )
        == "002 - Fake playlist.mp4"
    )


# --- through the real pipeline -------------------------------------------------


@pytest.fixture
def pipeline(store, providers, settings):
    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def submit_and_run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=analysis_service,
        )
        claimed = store.claim_next_queued()
        assert claimed is not None and claimed["id"] == result.job_id
        executor.execute(claimed)
        return result.job_id

    return submit_and_run


def test_artifacts_carry_display_filenames_end_to_end(pipeline, store, settings):
    job_id = pipeline(
        minimal_payload(
            outputs=[
                {"id": "audio_main", "type": "audio"},
                {
                    "id": "subs",
                    "type": "subtitles",
                    "options": {"languages": ["en", "fr"]},
                },
            ]
        )
    )
    assert store.get_job(job_id)["status"] == "succeeded"
    by_display = {
        artifact["display_filename"]: artifact
        for artifact in store.list_artifacts(job_id)
    }
    assert set(by_display) == {
        "Fake conference.m4a",
        "Fake conference - subtitles - en.srt",
        "Fake conference - subtitles - fr.srt",
    }
    # The physical job-store name stays technical (ADR 0017: naming is
    # metadata, not a storage layout).
    audio = by_display["Fake conference.m4a"]
    assert audio["filename"].startswith("audio_main")

    # The NamingPlan is visible in the plan snapshot.
    snapshot = json.loads(
        (settings.data_dir / "jobs" / job_id / "snapshots" / "plan.json").read_text()
    )
    resolved = {entry["output_id"]: entry for entry in snapshot["naming"]["outputs"]}
    assert resolved["audio_main"]["base"] == "Fake conference"
    assert resolved["subs"]["qualifier"] == "subtitles"


def test_api_download_serves_the_display_name(pipeline, store, settings, providers):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    job_id = pipeline(minimal_payload())
    artifact = store.list_artifacts(job_id)[0]
    app = create_app(settings, providers=providers, start_worker=False)
    with TestClient(app) as client:
        detail = client.get(f"/api/v1/artifacts/{artifact['id']}").json()
        assert detail["display_filename"] == "Fake conference.m4a"
        response = client.get(f"/api/v1/artifacts/{artifact['id']}/content")
        assert response.status_code == 200
        # RFC 5987 encoding: the display name (with its space) is the download
        # name, not the technical audio_main.m4a.
        assert "Fake%20conference.m4a" in response.headers["content-disposition"]


def test_suggest_base_name_is_the_sanitized_title_then_provider_id():
    from content.naming.engine import suggest_base_name

    assert (
        suggest_base_name(NormalizedResource(title="Artist - Song / Official Video"))
        == "Artist - Song - Official Video"
    )
    assert (
        suggest_base_name(NormalizedResource(title="", provider_id="dQw4w9WgXcQ"))
        == "dQw4w9WgXcQ"
    )
    assert suggest_base_name(NormalizedResource()) == ""
