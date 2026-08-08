"""PDF as an output: rendering readable content into a paginated document.

The renderer is behind an optional dependency, so the tests split in two:
everything about the *contract* — planning, capabilities, references, refusals —
is hermetic and always runs, while the tests that assert on real PDF bytes skip
when the `pdf` extra is absent. That mirrors the speech-to-text split and keeps
the suite honest on an installation without reportlab.
"""

import importlib.util

import pytest

from content.capabilities.catalog import OUTPUT_CAPABILITY, capability
from content.config import ContentSettings
from content.documents.markdown import parse_markdown
from content.domain.plan import PlanStep
from content.domain.request import EXECUTABLE_OUTPUT_TYPES, RESERVED_OUTPUT_TYPES
from content.planning import transformations as T
from content.processors.pdf import ReportLabPdfProcessor
from content.processors.pdf.reportlab_backend import document_to_flowables
from content.providers.base import ExecutionContext, Material, ProviderRegistry
from content.providers.documents import DocumentProvider
from content.providers.ffmpeg import FfmpegProvider

HAVE_REPORTLAB = importlib.util.find_spec("reportlab") is not None
needs_reportlab = pytest.mark.skipif(
    not HAVE_REPORTLAB, reason="the optional 'pdf' extra is not installed"
)

ARTICLE = """# On Declarative Engines

Declaring **what** you want beats invoking *how*. See [the spec](https://x/spec).

## Why it matters

- Stable intent
- Swappable tools

1. First it works
2. Then it is fast

> A contract you cannot change is a contract you cannot fix.

```python
plan = build(request)
```
"""


@pytest.fixture
def docs_root(tmp_path):
    root = (tmp_path / "docs").resolve()
    root.mkdir()
    (root / "note.md").write_text(ARTICLE)
    (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return root


@pytest.fixture
def settings(docs_root):
    return ContentSettings(
        data_dir=docs_root.parent,
        db_path=docs_root.parent / "content.db",
        allowed_input_roots=(docs_root,),
    )


def _api(settings, providers):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    return TestClient(create_app(settings, providers=providers, start_worker=False))


def _providers(*, with_pdf=True, with_summarizer=True, media=False):
    """`media=True` adds the fake URL provider, whose sources are videos — the
    only way to exercise "a source with no readable text" hermetically (a stub
    .mp4 on disk is rejected by ffprobe long before the planner is reached)."""
    from content.processors.transcript import TranscriptProcessor
    from tests.conftest import FakeProvider, FakeSummarizer

    processors = [TranscriptProcessor()]
    if with_summarizer:
        processors.append(FakeSummarizer())
    if with_pdf:
        processors.append(ReportLabPdfProcessor())
    sources = [DocumentProvider(), FfmpegProvider()]
    if media:
        sources.append(FakeProvider())
    return ProviderRegistry(sources, processors=processors)


def _build_plan(settings, providers, body):
    """Build the plan in-process. The public job view deliberately omits step
    `inputs`/`params`, and those are exactly what says a PDF renders the summary
    rather than the source — so the wiring is asserted on the plan itself."""
    from content.analysis.service import AnalysisService
    from content.domain.request import GenerationRequest
    from content.persistence.store import Store
    from content.planning.planner import build_plan

    request = GenerationRequest.model_validate(body)
    store = Store(settings.db_path)
    analysis = AnalysisService(store, providers, settings).analyze_sources(
        request.sources
    )
    return build_plan(request, analysis, providers, settings)


# --- the contract ---------------------------------------------------------------


def test_pdf_moved_from_reserved_to_executable():
    """It was declared-but-refused; promoting it is a contract change, so the
    two lists must not both claim it."""
    assert "pdf" in EXECUTABLE_OUTPUT_TYPES
    assert "pdf" not in RESERVED_OUTPUT_TYPES


def test_the_three_pdf_naming_layers_stay_distinct():
    """The distinction this design rests on, pinned in one place.

        public capability      pdf.render
        logical transformation document.render_pdf
        implementations        content.pdf.typst, content.pdf.reportlab

    `pdf.render` was once *both* the capability id and the operation name. The
    collision quietly implied one capability meant one implementation, which is
    exactly what adding a second renderer had to undo. A future rename that
    collapses any two of these layers fails here rather than in a release.
    """
    from content.planning.transformations import DEFINITIONS
    from content.processors.pdf import ReportLabPdfProcessor, TypstPdfProcessor

    # 1. Public capability — the only one of the three a client ever sees.
    assert OUTPUT_CAPABILITY["pdf"] == "pdf.render"
    assert capability("pdf.render") is not None
    assert capability("pdf.render").output_type == "pdf"

    # 2. Logical transformation — internal, and never equal to the capability.
    assert T.RENDER_PDF == "document.render_pdf"
    operations = {definition.operation for definition in DEFINITIONS}
    assert "document.render_pdf" in operations
    assert "pdf.render" not in operations, "a capability id is not an operation"

    # 3. Implementations — both declare that one operation, and nothing else.
    assert TypstPdfProcessor.name == "content.pdf.typst"
    assert ReportLabPdfProcessor.name == "content.pdf.reportlab"
    for implementation in (TypstPdfProcessor, ReportLabPdfProcessor):
        assert implementation.operations == (T.RENDER_PDF,)
        assert implementation.name != "pdf.render"
        assert implementation.name != T.RENDER_PDF

    # The capability's variant chain references the operation, not itself.
    variant = capability("pdf.render").variants[0]
    assert T.RENDER_PDF in variant.operations
    assert "pdf.render" not in variant.operations


def test_pdf_render_is_declared_once_and_maps_to_the_pdf_output():
    assert OUTPUT_CAPABILITY["pdf"] == "pdf.render"
    cap = capability("pdf.render")
    assert cap is not None and cap.output_type == "pdf"
    # R1: the variant's chain is declared here and nowhere else.
    variant = cap.variants[0]
    assert variant.operations == (T.TEXT_EXTRACT, T.RENDER_PDF)
    assert variant.requires_materials == (T.TEXT,)


def test_the_registry_knows_pdf_render_consumes_every_readable_kind():
    from content.planning.transformations import DEFINITIONS

    definition = next(d for d in DEFINITIONS if d.operation == T.RENDER_PDF)
    assert definition.output_kinds == (T.PDF,)
    for kind in (T.TEXT, T.SUMMARY, T.TRANSCRIPT, T.TRANSLATION):
        assert kind in definition.input_kinds
    # Presentation cannot be turned back into content.
    assert definition.lossy


# --- markdown parsing (no PDF needed) -------------------------------------------


@needs_reportlab
def test_the_markdown_subset_the_engine_emits_is_understood():
    from reportlab.platypus import ListFlowable, Paragraph, Preformatted

    styles = ReportLabPdfProcessor()._styles(ARTICLE)
    flowables = document_to_flowables(parse_markdown(ARTICLE), styles)
    kinds = [type(f).__name__ for f in flowables]
    assert kinds.count("ListFlowable") == 2, "bullet and numbered lists are distinct"
    assert "Preformatted" in kinds, "fenced code is not reflowed as prose"
    assert any(
        isinstance(f, (Paragraph, ListFlowable, Preformatted)) for f in flowables
    )


@needs_reportlab
def test_markup_in_the_content_cannot_corrupt_the_page():
    """The text comes from a web page or an LLM. A stray angle bracket must be
    escaped, not interpreted as reportlab markup."""
    styles = ReportLabPdfProcessor()._styles("x")
    flowables = document_to_flowables(
        parse_markdown("A <script>alert(1)</script> & co"), styles
    )
    rendered = flowables[0].text
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


# --- planning -------------------------------------------------------------------


def test_a_pdf_of_a_summary_chains_extract_then_summarize_then_render(
    settings, docs_root
):
    """The requested feature end to end at plan level: readable source →
    summary → PDF, with no media step anywhere."""
    plan = _build_plan(
        settings,
        _providers(),
        {
            "schema_version": "1.0",
            "sources": [
                {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
            ],
            "outputs": [
                {"id": "sum", "type": "summary"},
                {"id": "doc", "type": "pdf", "from_outputs": ["sum"]},
            ],
        },
    )
    steps = plan.steps
    assert [s.operation for s in steps] == [
        T.TEXT_EXTRACT,
        T.TEXT_SUMMARIZE,
        T.RENDER_PDF,
    ]
    render = steps[-1]
    assert render.provider.startswith("content.pdf.")
    # It renders the summary, not the source text — the whole point of the
    # from_outputs composition.
    assert render.depends_on == [steps[1].id]
    assert not any(s.operation.startswith("media.acquire") for s in steps)


def test_a_pdf_of_the_source_itself_needs_no_summary(settings, docs_root):
    plan = _build_plan(
        settings,
        _providers(with_summarizer=False),
        {
            "schema_version": "1.0",
            "sources": [
                {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
            ],
            "outputs": [{"id": "doc", "type": "pdf"}],
        },
    )
    assert [s.operation for s in plan.steps] == [T.TEXT_EXTRACT, T.RENDER_PDF]
    # Markdown upstream, not flattened text: headings and lists have to survive
    # into the layout or the rendering is pointless.
    assert plan.steps[0].params["format"] == "markdown"


def test_a_pdf_of_a_media_output_is_refused_with_a_usable_message(settings, docs_root):
    """'A PDF of a video' has no meaning. Refusing beats rendering a file path
    onto a page."""
    with _api(settings, _providers(media=True)) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "d", "type": "url", "uri": "https://x/talk"}],
                "outputs": [
                    {"id": "v", "type": "video"},
                    {"id": "doc", "type": "pdf", "from_outputs": ["v"]},
                ],
            },
        )
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any(
        e["path"] == "outputs[1].from_outputs" and "readable content" in e["message"]
        for e in errors
    ), errors


def test_a_pdf_of_a_source_with_no_text_is_refused_like_every_other_output(
    settings,
):
    """R3: `pdf.render` needs TEXT, the resolver says a video has none, and the
    planner's shared feasibility gate refuses with the same message shape every
    other output type gets. Consistency here is the invariant — a bespoke error
    for PDF would mean the gate had been bypassed."""
    with _api(settings, _providers(media=True)) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "d", "type": "url", "uri": "https://x/talk"}],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
    assert response.status_code == 422
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "capability_unavailable"
    assert error["path"] == "outputs[0]"


def test_a_json_transcript_rendered_as_pdf_warns_rather_than_refuses(settings):
    """Valid but almost certainly not what was meant: the JSON serialization
    would land on the page verbatim. A warning keeps the request working and
    says so — refusing would be the engine second-guessing an explicit ask."""
    with _api(settings, _providers(media=True)) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [{"id": "d", "type": "url", "uri": "https://x/talk"}],
                "outputs": [
                    {
                        "id": "tr",
                        "type": "transcript",
                        "options": {"format": "json"},
                    },
                    {"id": "doc", "type": "pdf", "from_outputs": ["tr"]},
                ],
            },
        )
    assert response.status_code == 201, response.text
    warnings = response.json()["warnings"]
    assert any(
        w["path"] == "outputs[1].from_outputs" and "json" in w["message"]
        for w in warnings
    ), warnings


def test_without_the_optional_extra_the_refusal_names_the_remedy(settings, docs_root):
    """R8: a declared-but-uninstalled runner is 'unavailable', never a crash —
    and the message has to tell the operator what to install."""
    with _api(settings, _providers(with_pdf=False)) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
                ],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
    assert response.status_code == 422
    message = response.json()["detail"]["errors"][0]["message"]
    assert "pdf" in message.lower()


def test_capabilities_offer_pdf_render_on_a_readable_source(settings, docs_root):
    with _api(settings, _providers()) as client:
        response = client.post(
            "/api/v1/capabilities",
            json={
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
                ]
            },
        )
    assert response.status_code == 200, response.text
    caps = {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}
    assert caps["pdf.render"]["status"] in ("available", "derivable")
    assert caps["pdf.render"]["selected_variant"] == "pdf.render.from_text"


def test_capabilities_refuse_pdf_render_on_a_source_with_no_text(settings, docs_root):
    """R3: the resolver and the planner must agree. The planner refuses a PDF of
    a media source, so the catalog must not advertise one."""
    with _api(settings, _providers(media=True)) as client:
        response = client.post(
            "/api/v1/capabilities",
            json={"sources": [{"id": "d", "type": "url", "uri": "https://x/talk"}]},
        )
    assert response.status_code == 200, response.text
    caps = {c["id"]: c for c in response.json()["sources"][0]["capabilities"]}
    assert caps["pdf.render"]["status"] == "unavailable"
    assert caps["pdf.render"]["reason"]["code"] == "missing_material"


# --- real bytes -----------------------------------------------------------------


@needs_reportlab
def test_a_job_produces_a_real_pdf_artifact(settings, docs_root):
    from tests.test_api import run_queued_job

    with _api(settings, _providers()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
                ],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
        assert submitted.status_code == 201, submitted.text
        job_id = submitted.json()["job_id"]
        run_queued_job(client)
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        assert job["status"] == "succeeded", job
        artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact["media_type"] == "application/pdf"
        content = client.get(f"/api/v1/artifacts/{artifact['id']}/content").content

    assert content.startswith(b"%PDF-"), "a real PDF, not a text file named .pdf"
    assert content.rstrip().endswith(b"%%EOF")
    producer = artifact["provenance"]["producer"]
    assert producer["provider"].startswith("content.pdf.")
    assert producer["operation"] == T.RENDER_PDF


@needs_reportlab
def test_rendering_is_reproducible(tmp_path):
    """The registry declares pdf.render deterministic. PDFs embed a creation
    date by default, so this would silently be false without the fixed stamp —
    and a step whose bytes change every run is not cacheable."""
    processor = ReportLabPdfProcessor()
    first, second = tmp_path / "a.pdf", tmp_path / "b.pdf"
    document = parse_markdown(ARTICLE, title="T")
    for target in (first, second):
        processor.render(document, target, None, page_size="a4", title="T", params={})
    assert first.read_bytes() == second.read_bytes()


@needs_reportlab
def test_an_empty_material_fails_loudly_rather_than_emitting_a_blank_page(
    settings, tmp_path
):
    material = tmp_path / "empty.md"
    material.write_text("   \n\n")
    processor = ReportLabPdfProcessor()
    step = PlanStep(
        id="s1",
        operation=T.RENDER_PDF,
        provider="content.pdf.reportlab",
        implementation_version=1,
        params={},
    )
    ctx = ExecutionContext(
        settings=settings,
        workdir=tmp_path / "work",
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        timeout_seconds=30,
        input_materials=[Material(path=material, media_type="text/markdown")],
    )
    with pytest.raises(Exception) as exc:
        processor.execute(step, ctx)
    assert "empty" in str(exc.value).lower()


# --- LLM output hygiene ---------------------------------------------------------


def test_a_model_fence_never_reaches_the_document():
    """Regression, found only in a live run: asked for Markdown, the model
    answered ```markdown … ```. The fence survived into the .md artifact, and
    the renderer then — correctly — laid the whole summary out as monospace
    code with the syntax visible. Fixed at the source, not in the renderer."""
    from content.processors.summarize import strip_markdown_fence

    wrapped = "```markdown\n# Title\n\n- point\n```"
    assert strip_markdown_fence(wrapped) == "# Title\n\n- point"
    assert strip_markdown_fence("```\n# Title\n```") == "# Title"
    # Clean output is untouched.
    assert strip_markdown_fence("# Title\n\ntext") == "# Title\n\ntext"
    # A real language tag is content the model meant to include.
    assert strip_markdown_fence("```python\nx = 1\n```") == "```python\nx = 1\n```"
    # Unterminated: leave it rather than truncate the summary.
    assert strip_markdown_fence("```markdown\n# T") == "```markdown\n# T"
