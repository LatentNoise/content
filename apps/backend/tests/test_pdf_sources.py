"""Reading a `.pdf` file source (its text layer).

`.pdf` was recognised and refused: "recognised but not yet readable by this
installation". That was honest and it was also the single most common thing
people try first — "summarize this PDF" is the sentence an agent hears more
than any other. pypdf is BSD-3-Clause and a pure-Python wheel, which are the
two properties that let it ship by default.

What is read is the **text layer**. A scanned page holds an image of words and
no words, and gets its own answer rather than being reported as an empty
document: "you want OCR" and "this file is empty" send a reader to different
places.
"""

from __future__ import annotations

import pytest

from content.domain.analysis import AnalysisError
from content.domain.request import FileSource
from content.providers.base import AnalysisContext
from content.providers.documents import (
    DocumentProvider,
    extract_pdf_text,
    pdf_reader,
)

pytest.importorskip("reportlab", reason="the test needs to author a PDF")

TEXT = "Revenue grew 12 percent this quarter. Costs were flat."


def _pdf(path, lines=(TEXT,), pages=1):
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    for _ in range(pages):
        for index, line in enumerate(lines):
            pdf.drawString(72, 720 - index * 18, line)
        pdf.showPage()
    pdf.save()
    return path


def _empty_pdf(path, pages=2):
    """A page with no text at all — what a scan looks like to a text reader."""
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    for _ in range(pages):
        pdf.showPage()
    pdf.save()
    return path


@pytest.fixture
def ctx(settings, tmp_path):
    return AnalysisContext(settings=settings, workdir=tmp_path / "work")


@pytest.fixture
def allowing(settings, tmp_path):
    from dataclasses import replace

    return replace(settings, allowed_input_roots=(tmp_path,))


# --- the extractor --------------------------------------------------------------


def test_the_text_layer_comes_back(tmp_path):
    assert "Revenue grew 12 percent" in extract_pdf_text(_pdf(tmp_path / "r.pdf"))


def test_every_page_is_read(tmp_path):
    body = extract_pdf_text(_pdf(tmp_path / "multi.pdf", pages=3))
    assert body.count("Revenue grew") == 3


def test_a_scan_is_not_reported_as_an_empty_document(tmp_path):
    with pytest.raises(AnalysisError) as excinfo:
        extract_pdf_text(_empty_pdf(tmp_path / "scan.pdf"))
    issue = excinfo.value.issue
    assert issue.code == "source_type_not_supported"
    assert "scan" in issue.message.lower() and "ocr" in issue.message.lower()
    assert issue.details.get("pages") == 2


def test_a_file_that_is_not_a_pdf_fails_as_unreadable(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(AnalysisError) as excinfo:
        extract_pdf_text(broken)
    assert "could not be read" in excinfo.value.issue.message


# --- through the provider -------------------------------------------------------


def test_a_pdf_source_analyses_like_any_document(allowing, tmp_path):
    provider = DocumentProvider()
    source = FileSource(id="s", type="file", path=str(_pdf(tmp_path / "r.pdf")))
    assert provider.supports(source)

    ctx = AnalysisContext(settings=allowing, workdir=tmp_path / "work")
    analysis = provider.analyze(source, ctx)

    assert analysis.text.has_text
    assert analysis.text.word_count > 5
    assert analysis.resource.resource_type == "document"


def test_summary_and_markdown_become_producible_from_a_pdf(allowing, tmp_path):
    """The point of the feature: what a caller can now ask for."""
    from content.capabilities.facts import facts_from_analysis
    from content.planning.feasibility import output_feasibility
    from content.providers.base import ProviderRegistry

    provider = DocumentProvider()
    source = FileSource(id="s", type="file", path=str(_pdf(tmp_path / "r.pdf")))
    ctx = AnalysisContext(settings=allowing, workdir=tmp_path / "work")
    analysis = provider.analyze(source, ctx)

    facts = facts_from_analysis(analysis)
    assert facts.has_material("text")

    registry = ProviderRegistry([provider])
    assert output_feasibility("markdown", analysis, registry).status != "unavailable"


def test_without_pypdf_the_refusal_says_what_to_install(
    allowing, tmp_path, monkeypatch
):
    """The optional-runner shape every other capability uses: absent means a
    clear refusal naming the remedy, never a crash."""
    monkeypatch.setattr("content.providers.documents.pdf_reader", lambda: None)
    provider = DocumentProvider()
    source = FileSource(id="s", type="file", path=str(_pdf(tmp_path / "r.pdf")))
    ctx = AnalysisContext(settings=allowing, workdir=tmp_path / "work")

    with pytest.raises(AnalysisError) as excinfo:
        provider.analyze(source, ctx)
    message = excinfo.value.issue.message
    assert "pypdf" in message and "content-backend[read]" in message


def test_pypdf_is_installed_here():
    """A guard on the environment itself: `make install` carries the extra, and
    the published image ships it. If this fails, the feature silently degrades
    to a refusal for everybody."""
    assert pdf_reader() is not None


def test_execution_extracts_the_text_rather_than_reading_the_bytes(allowing, tmp_path):
    """The trap this exists for, found end to end and not by reading the code.

    Analysis and execution read the file through different lines. Teaching the
    analysis path about PDFs made a PDF *look* readable, and the artifact then
    contained the file's own bytes:

        # report
        %PDF-1.3 … /BaseFont /Helvetica /Encoding /WinAnsiEncoding …

    Nothing failed. The job succeeded, the agent summarised PostScript
    operators, and the only symptom was nonsense — which is worse than a
    refusal, because a refusal is actionable.
    """
    from content.domain.plan import PlanStep
    from content.planning import transformations as T
    from content.providers.base import ExecutionContext

    source = _pdf(tmp_path / "r.pdf")
    workdir = tmp_path / "work"
    workdir.mkdir()
    step = PlanStep(
        id="extract",
        operation=T.TEXT_EXTRACT,
        provider="document",
        params={"path": str(source), "format": "markdown"},
    )
    produced = DocumentProvider().execute(
        step,
        ExecutionContext(
            settings=allowing,
            workdir=workdir,
            stdout_log=workdir / "out.log",
            stderr_log=workdir / "err.log",
            timeout_seconds=30,
        ),
    )

    body = produced[0].path.read_text()
    assert "Revenue grew 12 percent" in body
    assert "%PDF" not in body and "BaseFont" not in body


def test_a_scan_fails_the_step_with_its_reason(allowing, tmp_path):
    """And the reader's refusal must survive the trip into execution as a step
    failure carrying the reason, not as a traceback."""
    from content.domain.plan import PlanStep
    from content.planning import transformations as T
    from content.providers.base import ExecutionContext, StepExecutionError

    workdir = tmp_path / "work"
    workdir.mkdir()
    step = PlanStep(
        id="extract",
        operation=T.TEXT_EXTRACT,
        provider="document",
        params={"path": str(_empty_pdf(tmp_path / "scan.pdf")), "format": "markdown"},
    )
    with pytest.raises(StepExecutionError) as excinfo:
        DocumentProvider().execute(
            step,
            ExecutionContext(
                settings=allowing,
                workdir=workdir,
                stdout_log=workdir / "out.log",
                stderr_log=workdir / "err.log",
                timeout_seconds=30,
            ),
        )
    assert "ocr" in str(excinfo.value).lower()
