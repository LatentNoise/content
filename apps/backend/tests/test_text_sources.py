"""Web pages and documents as sources — the first non-media vertical.

Hermetic: no network. The web fetch is stubbed with real HTML fixtures, the
resolver is stubbed too (see `_fixed_resolver`), and the SSRF guard is exercised
directly on the new outbound path (it does not inherit yt-dlp's, because this
provider makes the request itself).
"""

import ipaddress
import socket

import pytest

from content.capabilities.facts import facts_from_analysis
from content.config import ContentSettings
from content.domain.analysis import AnalysisError, NormalizedResource, SourceAnalysis
from content.domain.request import FileSource, TextSource, UrlSource
from content.planning import transformations as T
from content.providers import webpage
from content.providers.base import AnalysisContext, ProviderRegistry
from content.providers.documents import DocumentProvider
from content.providers.ffmpeg import FfmpegProvider
from content.providers.webpage import WebPageProvider, extract, to_plain_text
from content.providers.ytdlp import YtDlpProvider

ARTICLE = """<html lang="en"><head><title>tab title</title>
<meta property="og:title" content="On Declarative Engines">
<meta name="author" content="A. Writer">
<meta property="article:published_time" content="2026-07-01">
<meta name="description" content="Why intent beats invocation.">
</head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<script>var tracking = "must not appear";</script>
<style>.x { color: red }</style>
<article>
  <h1>On Declarative Engines</h1>
  <p>Declaring <em>what</em> you want beats invoking <b>how</b>.</p>
  <h2>Why it matters</h2>
  <ul><li>Stable intent</li><li>Swappable tools</li></ul>
  <p>See <a href="https://example.com/spec">the spec</a>,
     or <a href="#top">skip</a>.</p>
</article>
<footer>© nobody</footer></body></html>"""


@pytest.fixture
def docs_root(tmp_path):
    root = (tmp_path / "docs").resolve()
    root.mkdir()
    (root / "note.md").write_text("# Release Notes\n\nThe engine got faster.\n")
    (root / "plain.txt").write_text("just some words in a file\n")
    (root / "paper.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return root


@pytest.fixture
def doc_settings(docs_root):
    return ContentSettings(
        data_dir=docs_root.parent,
        db_path=docs_root.parent / "content.db",
        allowed_input_roots=(docs_root,),
    )


@pytest.fixture
def ctx(doc_settings, tmp_path):
    return AnalysisContext(doc_settings, tmp_path / "work")


@pytest.fixture(autouse=True)
def _fixed_resolver(monkeypatch):
    """Answer every hostname with one public address, and every literal IP with
    itself.

    Stubbing `fetch` was not enough to make this module hermetic: the SSRF guard
    resolves the host *before* the fetch, so analysing `https://example.com/...`
    ran a real DNS query on every test. That also made the outcome depend on the
    answer — a resolver that maps unknown names onto a captive portal returns a
    private address, which the guard rightly refuses, and the reading tests would
    fail for a reason that has nothing to do with reading.

    Literal addresses must still answer with themselves, or the loopback test
    below would stop proving anything.
    """

    def getaddrinfo(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host))
            address = str(host)
        except ValueError:
            address = "93.184.216.34"  # public: the guard's happy path
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def _serve(monkeypatch, html: str, content_type: str = "text/html; charset=utf-8"):
    monkeypatch.setattr(
        webpage, "fetch", lambda url, timeout=None: (html, content_type)
    )


# --- routing: yt-dlp must never be shadowed ------------------------------------


def _registry():
    """The real registry, in a deliberately hostile construction order: the
    generic reader is listed first, so only `analysis_priority` can save the
    media extractor."""
    return ProviderRegistry(
        [WebPageProvider(), DocumentProvider(), FfmpegProvider(), YtDlpProvider()]
    )


def test_a_url_is_offered_to_ytdlp_before_the_generic_reader():
    """The regression this guards: `webpage` sorts before `ytdlp`
    alphabetically, so ordering by name alone would silently break YouTube."""
    source = UrlSource(id="v", type="url", uri="https://www.youtube.com/watch?v=abc")
    order = [p.name for p in _registry().candidates_for_source(source)]
    assert order[0] == "ytdlp", f"yt-dlp must get first refusal, got {order}"
    assert "webpage" in order, "the reader must still be a fallback"
    assert order.index("ytdlp") < order.index("webpage")


def test_an_article_url_reaches_the_reader_as_a_fallback():
    source = UrlSource(id="a", type="url", uri="https://example.com/an-article")
    order = [p.name for p in _registry().candidates_for_source(source)]
    # Same order for *any* URL: routing is decided by the analysis outcome,
    # never by inspecting the URL in advance.
    assert order == ["ytdlp", "webpage"]


def test_for_source_returns_the_highest_precedence_candidate():
    source = UrlSource(id="v", type="url", uri="https://vimeo.com/1")
    assert _registry().for_source(source).name == "ytdlp"


def test_a_media_file_still_routes_to_ffmpeg(docs_root):
    source = FileSource(id="f", type="file", path=str(docs_root / "clip.mp4"))
    assert [p.name for p in _registry().candidates_for_source(source)] == ["ffmpeg"]


def test_a_markdown_file_reaches_the_document_reader_first(docs_root):
    """ffmpeg claims every file, so without explicit precedence a .md would pay
    a failed ffprobe before anyone read it."""
    source = FileSource(id="f", type="file", path=str(docs_root / "note.md"))
    order = [p.name for p in _registry().candidates_for_source(source)]
    assert order[0] == "document", f"the narrower claim must win, got {order}"


# --- the SSRF guard on the new outbound path -----------------------------------


def test_the_reader_refuses_a_loopback_url(ctx):
    """This provider fetches the URL itself, so it does not inherit the guard
    applied on yt-dlp's behalf — it must enforce it on its own path."""
    source = UrlSource(id="a", type="url", uri="http://127.0.0.1:8000/admin")
    with pytest.raises(AnalysisError) as exc:
        WebPageProvider().analyze(source, ctx)
    assert exc.value.issue.code == "url_not_allowed"


def test_the_reader_refuses_a_non_http_scheme(ctx):
    source = UrlSource(id="a", type="url", uri="file:///etc/passwd")
    with pytest.raises(AnalysisError) as exc:
        WebPageProvider().analyze(source, ctx)
    assert exc.value.issue.code == "url_not_allowed"


def test_the_guard_can_be_relaxed_for_a_trusted_deployment(
    docs_root, tmp_path, monkeypatch
):
    settings = ContentSettings(
        data_dir=tmp_path,
        db_path=tmp_path / "db",
        allow_private_networks=True,
    )
    _serve(monkeypatch, ARTICLE)
    source = UrlSource(id="a", type="url", uri="http://127.0.0.1:9/page")
    analysis = WebPageProvider().analyze(
        source, AnalysisContext(settings, tmp_path / "w")
    )
    assert analysis.resource.resource_type == "webpage"


# --- extraction ----------------------------------------------------------------


def test_extraction_drops_chrome_and_keeps_the_article():
    markdown, title, meta = extract(ARTICLE)
    assert title == "On Declarative Engines"  # og:title wins over <title>
    assert "must not appear" not in markdown  # <script>
    assert "color: red" not in markdown  # <style>
    assert "Home" not in markdown and "About" not in markdown  # <nav>
    assert "nobody" not in markdown  # <footer>
    assert "Declaring what you want" in markdown


def test_extraction_preserves_structure_and_real_links():
    markdown, _, _ = extract(ARTICLE)
    assert "# On Declarative Engines" in markdown
    assert "## Why it matters" in markdown
    assert "- Stable intent" in markdown
    assert "[the spec](https://example.com/spec)" in markdown
    # A same-page anchor is not a destination; keeping it would be dead markdown.
    assert "#top" not in markdown


def test_plain_text_is_a_flattening_of_the_markdown():
    markdown, _, _ = extract(ARTICLE)
    plain = to_plain_text(markdown)
    assert "#" not in plain and "](" not in plain
    assert "the spec" in plain  # the link text survives, the target does not
    assert "https://example.com/spec" not in plain


def test_malformed_html_yields_what_could_be_parsed():
    markdown, _, _ = extract("<html><body><p>half a document<div><span>")
    assert "half a document" in markdown  # no exception, partial reading


def test_a_javascript_rendered_page_is_reported_as_having_no_text(ctx, monkeypatch):
    """Honest over helpful: an empty body is a fact, not an invented reading."""
    _serve(
        monkeypatch,
        "<html><head><title>App</title></head><body><div id='root'>"
        "</div><script>render()</script></body></html>",
    )
    analysis = WebPageProvider().analyze(
        UrlSource(id="a", type="url", uri="https://example.com/app"), ctx
    )
    assert analysis.text.has_text is False
    assert facts_from_analysis(analysis).material_state(T.TEXT) == "absent"


def test_a_non_html_response_is_refused_with_a_reason(ctx, monkeypatch):
    _serve(monkeypatch, "%PDF-1.4", content_type="application/pdf")
    with pytest.raises(AnalysisError) as exc:
        WebPageProvider().analyze(
            UrlSource(id="a", type="url", uri="https://example.com/x.pdf"), ctx
        )
    assert exc.value.issue.code == "source_type_not_supported"


def test_webpage_analysis_carries_the_public_metadata(ctx, monkeypatch):
    _serve(monkeypatch, ARTICLE)
    analysis = WebPageProvider().analyze(
        UrlSource(id="a", type="url", uri="https://example.com/an-article"), ctx
    )
    resource = analysis.resource
    assert resource.resource_type == "webpage"
    assert resource.title == "On Declarative Engines"
    assert resource.author == "A. Writer"
    assert resource.published_at == "2026-07-01"
    assert resource.languages == ["en"]
    assert analysis.text.has_text and analysis.text.word_count > 5
    assert analysis.text.extractor.startswith("webpage-reader/")


def test_the_resource_key_changes_with_the_extractor_version(ctx, monkeypatch):
    """A better reader must invalidate readings produced by the old one."""
    source = UrlSource(id="a", type="url", uri="https://example.com/a")
    before = WebPageProvider().resource_key(source, ctx)
    monkeypatch.setattr(webpage, "EXTRACTOR_VERSION", "99")
    assert WebPageProvider().resource_key(source, ctx) != before


# --- documents -----------------------------------------------------------------


def test_a_markdown_file_keeps_its_heading_as_the_title(docs_root, ctx):
    analysis = DocumentProvider().analyze(
        FileSource(id="d", type="file", path=str(docs_root / "note.md")), ctx
    )
    assert analysis.resource.resource_type == "document"
    assert analysis.resource.title == "Release Notes"
    assert analysis.resource.mime_type == "text/markdown"
    assert analysis.text.has_text


def test_an_inline_text_source_is_readable(ctx):
    analysis = DocumentProvider().analyze(
        TextSource(id="t", type="text", content="a few inline words"), ctx
    )
    assert analysis.resource.resource_type == "text"
    assert analysis.text.word_count == 4


def test_a_pdf_is_recognised_and_honestly_refused(docs_root, ctx):
    """'Valid but not implemented' is a different answer from 'invalid'
    (INV-014) — and a different answer from silently producing garbage."""
    source = FileSource(id="d", type="file", path=str(docs_root / "paper.pdf"))
    assert DocumentProvider().supports(source), "a PDF must be claimed, then refused"
    with pytest.raises(AnalysisError) as exc:
        DocumentProvider().analyze(source, ctx)
    assert exc.value.issue.code == "source_type_not_supported"
    assert ".pdf" in exc.value.issue.message
    assert exc.value.terminal, "a deliberate refusal must stop the fallback chain"


def test_a_document_outside_the_allowed_roots_is_refused(ctx):
    with pytest.raises(AnalysisError) as exc:
        DocumentProvider().analyze(
            FileSource(id="d", type="file", path="/etc/passwd"), ctx
        )
    assert exc.value.issue.code in ("path_not_allowed", "source_type_not_supported")


# --- facts ---------------------------------------------------------------------


def test_text_is_a_tri_state_material_like_every_other():
    readable = SourceAnalysis(
        source_id="s",
        resource=NormalizedResource(resource_type="webpage"),
        text={"has_text": True, "word_count": 100},
    )
    facts = facts_from_analysis(readable)
    assert facts.material_state(T.TEXT) == "present"
    # A readable resource has no media — and that is a *fact*, not an unknown.
    assert facts.material_state(T.VIDEO) == "absent"
    assert facts.material_state(T.AUDIO) == "absent"
    assert facts.conclusive


# --- capabilities and end-to-end jobs ------------------------------------------


def _api(settings, providers):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    return TestClient(create_app(settings, providers=providers, start_worker=False))


def _text_providers(with_summarizer=True):
    from tests.conftest import FakeSummarizer

    processors = [FakeSummarizer()] if with_summarizer else []
    return ProviderRegistry(
        [DocumentProvider(), FfmpegProvider()], processors=processors
    )


def test_capabilities_on_a_document_offer_text_and_refuse_media(
    doc_settings, docs_root
):
    """The DoD in one assertion: a readable source offers reading, and refuses
    video/audio with a structured reason rather than an error."""
    with _api(doc_settings, _text_providers()) as client:
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

    assert caps["text.extract"]["status"] in ("available", "derivable")
    assert caps["markdown.export"]["status"] in ("available", "derivable")
    assert caps["summary.generate"]["selected_variant"] == "summary.from_text"

    for media in ("video.download", "audio.download"):
        assert caps[media]["status"] == "unavailable"
        assert caps[media]["reason"]["code"] == "missing_material"


def test_the_pdf_refusal_survives_the_fallback_chain(doc_settings, docs_root):
    """Regression (D-27): the provider-level PDF test passed while the *API*
    answered "ffprobe could not analyze the file". DocumentProvider's deliberate
    refusal was overwritten by the next candidate in the chain, so the honest
    answer never reached the caller. The provider list here is the one that
    caused it — document, then ffmpeg."""
    with _api(doc_settings, _text_providers()) as client:
        response = client.post(
            "/api/v1/capabilities",
            json={
                "sources": [
                    {"id": "a", "type": "file", "path": str(docs_root / "paper.pdf")}
                ]
            },
        )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "source_type_not_supported"
    assert error["details"]["provider"] == "document"
    assert "ffprobe" not in error["message"]


def test_a_document_job_produces_text_and_markdown(doc_settings, docs_root):
    from tests.test_api import run_queued_job

    with _api(doc_settings, _text_providers()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
                ],
                "outputs": [
                    {
                        "id": "txt",
                        "type": "document_text",
                        "options": {"format": "text"},
                    },
                    {"id": "md", "type": "markdown"},
                ],
            },
        )
        assert submitted.status_code == 201, submitted.text
        job_id = submitted.json()["job_id"]
        run_queued_job(client)
        artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()

    by_request = {a["artifact_request_id"]: a for a in artifacts}
    assert by_request["txt"]["media_type"] == "text/plain"
    assert by_request["md"]["media_type"] == "text/markdown"
    # Provenance names the reader that produced it, not a generic "file".
    producer = by_request["md"]["provenance"]["producer"]
    assert producer["provider"] == "document"
    assert producer["operation"] == "text.extract"


def test_a_summary_of_a_document_never_touches_audio(doc_settings, docs_root):
    """`summary.from_text` is the point of the slice: an article is summarised
    without acquiring audio or subtitles."""
    with _api(doc_settings, _text_providers()) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "note.md")}
                ],
                "outputs": [{"id": "s", "type": "summary"}],
            },
        )
        assert response.status_code == 201, response.text
        job = client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()

    operations = [step["operation"] for step in job["steps"]]
    assert "text.extract" in operations
    assert "text.summarize" in operations
    assert not any(op.startswith("media.acquire") for op in operations), operations
    assert "audio.transcribe" not in operations


def test_an_empty_document_fails_the_step_rather_than_shipping_nothing(
    doc_settings, docs_root
):
    from tests.test_api import run_queued_job

    (docs_root / "empty.md").write_text("   \n\n")
    with _api(doc_settings, _text_providers()) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "d", "type": "file", "path": str(docs_root / "empty.md")}
                ],
                "outputs": [{"id": "txt", "type": "document_text"}],
            },
        )
        # Either refused at planning (no text material) or failed at execution —
        # both are honest; silently delivering an empty artifact is not.
        if submitted.status_code == 201:
            job_id = submitted.json()["job_id"]
            run_queued_job(client)
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            assert job["status"] == "failed"
        else:
            assert submitted.status_code == 422


def test_the_reading_never_repeats_its_own_title(ctx, monkeypatch, tmp_path):
    """The extraction already opens with the <h1>; adding the title again made
    the plain-text artifact start by saying it twice."""
    from content.domain.plan import PlanStep
    from content.providers.base import ExecutionContext

    _serve(monkeypatch, ARTICLE)
    step = PlanStep(
        id="s1",
        operation=T.TEXT_EXTRACT,
        provider="webpage",
        params={"uri": "https://example.com/a", "format": "text"},
    )
    work = tmp_path / "job"
    exec_ctx = ExecutionContext(
        settings=ctx.settings,
        workdir=work,
        stdout_log=work / "o.log",
        stderr_log=work / "e.log",
        timeout_seconds=30,
    )
    (produced,) = WebPageProvider().execute(step, exec_ctx)
    body = produced.path.read_text()
    assert body.count("On Declarative Engines") == 1, body[:200]


def test_a_url_output_binds_the_reader_not_the_first_candidate(
    doc_settings, monkeypatch
):
    """The regression a live run caught and the hermetic tests had missed: for a
    URL, `for_source` is yt-dlp, which cannot run `text.extract`."""
    from tests.conftest import FakeSummarizer

    _serve(monkeypatch, ARTICLE)
    providers = ProviderRegistry(
        [YtDlpProvider(), WebPageProvider()], processors=[FakeSummarizer()]
    )
    with _api(doc_settings, providers) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "schema_version": "1.0",
                "sources": [
                    {"id": "a", "type": "url", "uri": "https://example.com/article"}
                ],
                "outputs": [{"id": "md", "type": "markdown"}],
            },
        )
        assert response.status_code == 201, response.text
        job = client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()
    assert [(s["operation"], s["provider"]) for s in job["steps"]] == [
        ("text.extract", "webpage")
    ]
