"""A pdf or markdown from a source that carries no text of its own (ADR 0028).

The engine could always *execute* "summarize this video, render that as a PDF"
— the composition with `from_outputs` proved it — but the capability resolver
and the feasibility gate judged the source's materials alone, and answered
"unavailable" for the plain form. These tests pin the repaired judgement end to
end: the resolver announces the derivation, the planner inserts it, an explicit
reference still wins, the artifact name says what the file contains, and a
source where no derivation exists is still refused — with the missing piece
named.
"""

import pathlib

import pytest

from content.capabilities.catalog import capability
from content.capabilities.facts import facts_from_analysis
from content.capabilities.policy import EffectivePolicy
from content.capabilities.resolver import CapabilityResolver, select_variant
from content.config import ContentSettings
from content.domain.analysis import NormalizedResource, SourceAnalysis, SubtitleTrack
from content.planning import transformations as T
from content.planning.transformations import build_registry
from content.processors.pdf import ReportLabPdfProcessor
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.documents import DocumentProvider
from content.providers.ffmpeg import FfmpegProvider


@pytest.fixture
def settings(tmp_path):
    return ContentSettings(data_dir=tmp_path, db_path=tmp_path / "content.db")


def _providers(*, with_summarizer=True):
    from tests.conftest import FakeProvider, FakeSummarizer

    processors = [TranscriptProcessor(), ReportLabPdfProcessor()]
    if with_summarizer:
        processors.append(FakeSummarizer())
    return ProviderRegistry(
        [DocumentProvider(), FfmpegProvider(), FakeProvider()],
        processors=processors,
    )


def _build_plan(settings, providers, outputs, source=None):
    from content.analysis.service import AnalysisService
    from content.domain.request import GenerationRequest
    from content.persistence.store import Store
    from content.planning.planner import build_plan

    request = GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [
                source or {"id": "main", "type": "url", "uri": "https://x/talk"}
            ],
            "outputs": outputs,
        }
    )
    store = Store(settings.db_path)
    analysis = AnalysisService(store, providers, settings).analyze_sources(
        request.sources
    )
    return build_plan(request, analysis, providers, settings)


def _api(settings, providers):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    return TestClient(create_app(settings, providers=providers, start_worker=False))


def _step(plan, step_id):
    return next(s for s in plan.steps if s.id == step_id)


def _bound(plan, output_id):
    binding = next(
        b for b in plan.output_bindings if b.artifact_request_id == output_id
    )
    return _step(plan, binding.produced_by)


# --- the resolver announces the derivation (A) ----------------------------------


def _video_with_subs() -> SourceAnalysis:
    return SourceAnalysis(
        source_id="main",
        resource=NormalizedResource(
            resource_type="video", title="A talk", duration_seconds=120
        ),
        subtitles=[SubtitleTrack(language="en", origin="manual")],
    )


def _image_only() -> SourceAnalysis:
    """A source that can produce no text at all: no text layer, no subtitles,
    no audio. The regression anchor — for it, nothing may change."""
    return SourceAnalysis(
        source_id="main",
        resource=NormalizedResource(
            resource_type="image",
            title="A poster",
            thumbnail_url="https://img/poster.jpg",
        ),
    )


def _resolved(analysis, providers):
    resolver = CapabilityResolver(build_registry(providers), providers)
    facts = facts_from_analysis(analysis)
    return {c.id: c for c in resolver.resolve(facts, EffectivePolicy())}


def test_documents_are_derivable_from_a_video_through_the_summary():
    caps = _resolved(_video_with_subs(), _providers())
    assert caps["pdf.render"].status == "derivable"
    assert caps["pdf.render"].selected_variant == "pdf.render.via_summary"
    assert caps["pdf.render"].derived_from == ["subtitles"]
    assert caps["markdown.export"].status == "derivable"
    assert caps["markdown.export"].selected_variant == "markdown.export.via_summary"
    assert caps["markdown.export"].derived_from == ["subtitles"]


def test_text_extract_still_means_text_the_source_itself_carries():
    """`text.extract` is exempt on purpose: extracting is not deriving, and a
    video still has no text to extract."""
    caps = _resolved(_video_with_subs(), _providers())
    assert caps["text.extract"].status == "unavailable"
    assert caps["text.extract"].reason.code == "missing_material"
    assert caps["text.extract"].reason.missing_materials == ["text"]


def test_the_derivation_chain_says_what_the_document_will_contain():
    """The public answer for "derived how?": the whole material chain, read
    from the registry's own declarations — the difference between "a PDF from
    subtitles" and the truth, a PDF *of the summary* (ADR 0028)."""
    caps = _resolved(_video_with_subs(), _providers())
    assert caps["pdf.render"].derivation == [
        "subtitles",
        "transcript",
        "summary",
        "pdf",
    ]
    assert caps["markdown.export"].derivation == [
        "subtitles",
        "transcript",
        "summary",
    ]
    assert caps["summary.generate"].derivation == [
        "subtitles",
        "transcript",
        "summary",
    ]
    assert caps["transcript.generate"].derivation == ["subtitles", "transcript"]
    # A direct capability's chain collapses to its own material: nothing to say.
    assert caps["subtitles.download"].derivation == ["subtitles"]
    # And a blocked capability has no selected variant, so no chain to claim.
    assert caps["text.extract"].derivation == []


def test_the_derivation_chain_reaches_the_public_capability_feed():
    """Clients read /capabilities, not the resolver — the chain must survive
    the trip so a UI can render "subtitles → transcript → summary → pdf"."""
    import tempfile

    from content.config import ContentSettings

    with tempfile.TemporaryDirectory() as tmp:
        settings = ContentSettings(
            data_dir=pathlib.Path(tmp), db_path=pathlib.Path(tmp) / "c.db"
        )
        with _api(settings, _providers()) as client:
            response = client.post(
                "/api/v1/capabilities",
                json={"sources": [{"id": "d", "type": "url", "uri": "https://x/talk"}]},
            )
    assert response.status_code == 200, response.text
    caps = {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}
    assert caps["pdf.render"]["derivation"] == [
        "subtitles",
        "transcript",
        "summary",
        "pdf",
    ]


def test_planner_builds_exactly_the_variant_the_resolver_selected():
    """R3 for the new variants: what /capabilities announces is what the
    planner will construct."""
    providers = _providers()
    facts = facts_from_analysis(_video_with_subs())
    chosen = select_variant(
        capability("pdf.render"),
        facts,
        build_registry(providers),
        providers,
        EffectivePolicy(),
    )
    assert chosen is not None and chosen.id == "pdf.render.via_summary"


def test_a_source_with_no_text_producing_path_stays_unavailable():
    caps = _resolved(_image_only(), _providers())
    for cap_id in ("pdf.render", "markdown.export", "text.extract"):
        assert caps[cap_id].status == "unavailable", cap_id
        assert caps[cap_id].reason.code == "missing_material", cap_id
    # The derivation variants widen what the reason names: the source lacks
    # every material a document could be built from, and the message says so.
    assert caps["pdf.render"].reason.missing_materials == [
        "text",
        "subtitles",
        "audio",
    ]


# --- the planner inserts the derivation (B) -------------------------------------


def test_a_plain_pdf_on_a_video_derives_the_summary(settings):
    plan = _build_plan(settings, _providers(), [{"id": "doc", "type": "pdf"}])
    assert [s.operation for s in plan.steps] == [
        T.ACQUIRE_SUBTITLES,
        T.SUBTITLES_TO_TRANSCRIPT,
        T.TEXT_SUMMARIZE,
        T.RENDER_PDF,
    ]
    render = _bound(plan, "doc")
    assert render.operation == T.RENDER_PDF
    summarize = next(s for s in plan.steps if s.operation == T.TEXT_SUMMARIZE)
    assert render.depends_on == [summarize.id]
    # The summary feeding the renderer is Markdown, so headings and lists
    # survive into the layout — same reason `_pdf_from_source` insists on it.
    assert summarize.params["format"] == "markdown"


def test_a_plain_markdown_on_a_video_is_the_summary_in_markdown(settings):
    plan = _build_plan(settings, _providers(), [{"id": "md", "type": "markdown"}])
    assert [s.operation for s in plan.steps] == [
        T.ACQUIRE_SUBTITLES,
        T.SUBTITLES_TO_TRANSCRIPT,
        T.TEXT_SUMMARIZE,
    ]
    bound = _bound(plan, "md")
    assert bound.operation == T.TEXT_SUMMARIZE
    assert bound.params["format"] == "markdown"


def test_plain_pdf_and_markdown_share_one_summarization(settings):
    plan = _build_plan(
        settings,
        _providers(),
        [{"id": "md", "type": "markdown"}, {"id": "doc", "type": "pdf"}],
    )
    summarize_steps = [s for s in plan.steps if s.operation == T.TEXT_SUMMARIZE]
    assert len(summarize_steps) == 1
    assert _bound(plan, "md").id == summarize_steps[0].id
    assert _bound(plan, "doc").depends_on == [summarize_steps[0].id]


def test_a_plain_pdf_prefers_the_summary_the_request_declares(settings):
    """One summarization, not two: the pdf renders the sibling summary instead
    of deriving a default one beside it — so its options (length, language)
    are honoured by the rendered document."""
    plan = _build_plan(
        settings,
        _providers(),
        [
            {"id": "s", "type": "summary", "options": {"length": "long"}},
            {"id": "doc", "type": "pdf"},
        ],
    )
    summarize_steps = [s for s in plan.steps if s.operation == T.TEXT_SUMMARIZE]
    assert len(summarize_steps) == 1
    assert summarize_steps[0].params["length"] == "long"
    assert _bound(plan, "doc").depends_on == [_bound(plan, "s").id]


def test_a_declared_reference_always_outranks_the_implicit_one(settings):
    """The precise form stays precise: `from_outputs` names the transcript, so
    the pdf renders the transcript — even with a summary sitting right there."""
    plan = _build_plan(
        settings,
        _providers(),
        [
            {"id": "t", "type": "transcript", "options": {"format": "text"}},
            {"id": "s", "type": "summary"},
            {"id": "doc", "type": "pdf", "from_outputs": ["t"]},
        ],
    )
    assert _bound(plan, "doc").depends_on == [_bound(plan, "t").id]


def test_the_pdf_can_come_before_the_summary_it_renders(settings):
    """Declaration order must not matter: the implicit reference orders the
    planning like a declared one would."""
    plan = _build_plan(
        settings,
        _providers(),
        [{"id": "doc", "type": "pdf"}, {"id": "s", "type": "summary"}],
    )
    assert _bound(plan, "doc").depends_on == [_bound(plan, "s").id]


# --- the artifact says what it contains (naming) --------------------------------


def test_an_implicitly_derived_document_is_named_for_its_content(settings):
    """ "A talk - summary.pdf", exactly as the declared composition names it —
    same artifact, same name — and never the bare resource name, which would
    claim the pdf *is* the talk."""
    plan = _build_plan(
        settings,
        _providers(),
        [{"id": "doc", "type": "pdf"}, {"id": "md", "type": "markdown"}],
    )
    assert plan.naming.for_output("doc").qualifier == "summary"
    assert plan.naming.for_output("md").qualifier == "summary"


def test_a_pdf_of_the_sources_own_text_keeps_the_bare_name(settings, tmp_path):
    """The article-to-PDF case is untouched: a pdf of the page *is* the page."""
    root = (tmp_path / "docs").resolve()
    root.mkdir()
    (root / "note.md").write_text("# A note\n\nBody.\n")
    file_settings = ContentSettings(
        data_dir=tmp_path,
        db_path=tmp_path / "content.db",
        allowed_input_roots=(root,),
    )
    plan = _build_plan(
        file_settings,
        _providers(),
        [{"id": "doc", "type": "pdf"}],
        source={"id": "main", "type": "file", "path": str(root / "note.md")},
    )
    assert [s.operation for s in plan.steps] == [T.TEXT_EXTRACT, T.RENDER_PDF]
    assert plan.naming.for_output("doc").qualifier == ""


# --- what is impossible is still refused, in user terms (C, D) ------------------


def test_no_summarizer_refuses_the_plain_pdf_naming_the_missing_piece(settings):
    with _api(settings, _providers(with_summarizer=False)) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "main", "type": "url", "uri": "https://x/talk"}],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
    assert response.status_code == 422
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "capability_unavailable"
    assert "text.summarize" in error["message"]


def test_no_subtitles_and_no_stt_refuses_naming_the_missing_piece(settings):
    """Audio exists, so the summary path is reachable in principle — what is
    missing is this installation's speech-to-text, and the refusal says so
    instead of the old bare "cannot be produced"."""
    with _api(settings, _providers()) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "main", "type": "url", "uri": "https://x/nosubs"}],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
    assert response.status_code == 422
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "capability_unavailable"
    assert "audio.transcribe" in error["message"]
