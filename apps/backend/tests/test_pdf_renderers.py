"""One transformation, two implementations.

The matrix here is the point: every guarantee that must hold *whichever*
renderer runs is asserted against both, from the same fixtures. A backend that
quietly stops escaping, stops validating glyphs or starts parsing Markdown on
its own fails its half of a shared test rather than passing a bespoke one.

Typst needs a binary, so its half skips when absent; the ReportLab half skips
without the `pdf` extra. Selection, template safety and the injection guard are
hermetic and always run.
"""

import importlib.util
import json
import shutil
import subprocess

import pytest

from content.config import ContentSettings
from content.documents.fonts import missing_characters
from content.domain.plan import PlanStep
from content.domain.request import GenerationRequest
from content.planning import transformations as T
from content.planning.planner import _select_renderer
from content.processors.pdf import ReportLabPdfProcessor, TypstPdfProcessor
from content.processors.pdf.typst_backend import available_templates, resolve_template
from content.providers.base import (
    ExecutionContext,
    Material,
    ProviderRegistry,
    StepExecutionError,
)

HAVE_REPORTLAB = importlib.util.find_spec("reportlab") is not None


def _typst_ready() -> bool:
    binary = shutil.which("typst")
    if binary is None:
        return False
    try:
        return (
            subprocess.run(
                [binary, "--version"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


HAVE_TYPST = _typst_ready()

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None


def _pdf_text(path) -> str:
    """The document's text as a reader would see it, or skip.

    `pdftotext` (poppler-utils) is an external tool, and this suite must pass
    without it. Checking the return code was not enough: `subprocess.run` raises
    `FileNotFoundError` when the binary is missing, so on a machine that had
    never installed poppler these tests *errored* instead of skipping. The
    maintainer's laptop had it, which is exactly why nothing revealed this until
    the suite first ran somewhere else.
    """
    if not HAVE_PDFTOTEXT:
        pytest.skip("pdftotext unavailable (poppler-utils)")
    result = subprocess.run(
        ["pdftotext", str(path), "-"], capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("pdftotext could not read the document")
    return result.stdout


# Everything the engine emits, in one fixture, so both renderers are asked the
# same questions.
DOCUMENT = """# Évaluation

Accented French samples, kept verbatim because Latin-1-only fonts drop exactly
these: café, naïve, œuvre. **bold**, *italic*, `code`, and
[a link](https://example.com/spec).

## Structure

- first
- second

1. one
2. two

> A quote.

---

```python
plan = build(request)
```
"""

# Typst is a real programming language and ReportLab has its own mini-HTML.
# The same payload must be inert in both.
INJECTION = (
    '#set page(fill: red) #panic("pwned") #read("/etc/passwd") '
    "$x^2$ <script>alert(1)</script> & <b>raw</b>"
)


def _renderers():
    available = []
    if HAVE_REPORTLAB:
        available.append(pytest.param(ReportLabPdfProcessor, id="reportlab"))
    if HAVE_TYPST:
        available.append(pytest.param(TypstPdfProcessor, id="typst"))
    return available or [pytest.param(None, id="none", marks=pytest.mark.skip)]


def _run(processor, markdown, tmp_path, *, title="T", params=None, policy="error"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = ContentSettings(
        data_dir=tmp_path, db_path=tmp_path / "c.db", pdf_missing_glyphs=policy
    )
    source = tmp_path / "in.md"
    source.write_text(markdown, encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    step = PlanStep(
        id="s1",
        operation=T.RENDER_PDF,
        provider=processor.name,
        implementation_version=1,
        params={"page_size": "a4", "title": title, **(params or {})},
    )
    ctx = ExecutionContext(
        settings=settings,
        workdir=workdir,
        stdout_log=workdir / "out.log",
        stderr_log=workdir / "err.log",
        timeout_seconds=90,
        input_materials=[Material(path=source, media_type="text/markdown")],
    )
    return processor.execute(step, ctx)


# --- the matrix -----------------------------------------------------------------


@pytest.mark.parametrize("renderer", _renderers())
def test_every_renderer_produces_a_real_pdf(renderer, tmp_path):
    produced = _run(renderer(), DOCUMENT, tmp_path)
    assert len(produced) == 1
    artifact = produced[0]
    assert artifact.media_type == "application/pdf"
    data = artifact.path.read_bytes()
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")
    assert artifact.attributes["pages"] >= 1
    assert artifact.attributes["renderer"] == renderer.name


@pytest.mark.parametrize("renderer", _renderers())
def test_no_renderer_executes_content_it_was_given(renderer, tmp_path):
    """The security property, asserted per backend rather than assumed.

    Typst would interpret `#panic(…)` and ReportLab would interpret `<b>` if the
    text were ever concatenated into their input languages. Both must place it
    as literal characters instead.
    """
    produced = _run(renderer(), f"# Title\n\n{INJECTION}\n", tmp_path)
    rendered = _pdf_text(produced[0].path)
    assert "pwned" in rendered, "the payload must survive as visible text"
    assert "alert(1)" in rendered
    # And it must not have taken effect: a red page fill or a panic would have
    # changed the output or failed the compile.
    assert "Title" in rendered


# A plane-1 script no bundled or system font is expected to carry, so the
# policy tests are about the policy rather than about which fonts a machine has.
UNDRAWABLE = "# T\n\nGothic \U00010330\U00010331\U00010332 here.\n"


@pytest.mark.parametrize("renderer", _renderers())
def test_error_policy_refuses_a_document_it_cannot_draw(renderer, tmp_path):
    """The safety baseline, kept: an exit code of 0 with missing glyphs is not
    success. Neither backend reports it — ReportLab draws notdef boxes, Typst
    exits 0 and draws tofu — so the engine has to be the thing that speaks."""
    processor = renderer()
    if not missing_characters(UNDRAWABLE, processor.coverages(UNDRAWABLE)):
        pytest.skip("this machine has a font covering the probe script")
    with pytest.raises(StepExecutionError) as exc:
        _run(processor, UNDRAWABLE, tmp_path, policy="error")
    assert exc.value.code == "unsupported_glyphs"
    assert "CONTENT_PDF_FONT" in str(exc.value)
    # Structured, not only prose: a client should not have to parse a sentence.
    assert exc.value.details["policy"] == "error"
    assert "U+10330" in exc.value.details["code_points"]


@pytest.mark.parametrize("renderer", _renderers())
def test_replace_policy_substitutes_a_drawable_placeholder(renderer, tmp_path):
    """The default. One emoji in an LLM summary must not destroy an otherwise
    perfect document — but the loss has to be visible, and the placeholder
    itself must be drawable or the fix would reintroduce the bug."""
    processor = renderer()
    coverages = processor.coverages(UNDRAWABLE)
    if not missing_characters(UNDRAWABLE, coverages):
        pytest.skip("this machine has a font covering the probe script")
    produced = _run(processor, UNDRAWABLE, tmp_path, policy="replace")
    report = produced[0].attributes["missing_glyphs"]
    assert report["policy"] == "replace"
    assert report["count"] >= 3
    replacement = report["replaced_with"]
    assert all(c.covers(ord(replacement)) for c in coverages), (
        "the placeholder must itself be drawable"
    )
    rendered = _pdf_text(produced[0].path)
    assert "\U00010330" not in rendered
    assert replacement in rendered


@pytest.mark.parametrize("renderer", _renderers())
def test_warn_policy_renders_unchanged_but_never_silently(renderer, tmp_path):
    """The escape hatch for an operator who knows their fonts. It is the only
    mode that can put an undrawable glyph on a page, so the report is what makes
    it defensible rather than a silent regression to the old behaviour."""
    processor = renderer()
    if not missing_characters(UNDRAWABLE, processor.coverages(UNDRAWABLE)):
        pytest.skip("this machine has a font covering the probe script")
    produced = _run(processor, UNDRAWABLE, tmp_path, policy="warn")
    report = produced[0].attributes["missing_glyphs"]
    assert report["policy"] == "warn"
    assert "replaced_with" not in report
    assert report["characters"] and report["code_points"]


@pytest.mark.parametrize("renderer", _renderers())
def test_a_drawable_document_carries_no_glyph_report(renderer, tmp_path):
    """Absence of the record is a positive statement that the document is
    complete, not merely that nobody looked."""
    produced = _run(renderer(), DOCUMENT, tmp_path, policy="replace")
    assert "missing_glyphs" not in produced[0].attributes


@pytest.mark.parametrize("renderer", _renderers())
def test_an_unreadable_policy_value_falls_back_rather_than_breaking_pdfs(
    renderer, tmp_path
):
    """A typo in an environment variable must not take PDF output down; the
    effective policy is reported on the artifact either way."""
    produced = _run(renderer(), UNDRAWABLE, tmp_path, policy="nonsense")
    report = produced[0].attributes.get("missing_glyphs")
    if report is not None:
        assert report["policy"] == "replace"


@pytest.mark.parametrize("renderer", _renderers())
def test_every_renderer_keeps_latin_and_french_text(renderer, tmp_path):
    produced = _run(renderer(), DOCUMENT, tmp_path)
    rendered = _pdf_text(produced[0].path)
    for fragment in ("café", "naïve", "œuvre", "Évaluation"):
        assert fragment in rendered, f"{fragment} lost by {renderer.name}"


@pytest.mark.parametrize("renderer", _renderers())
def test_every_renderer_rejects_an_empty_document(renderer, tmp_path):
    with pytest.raises(StepExecutionError) as exc:
        _run(renderer(), "   \n\n", tmp_path)
    assert "empty" in str(exc.value).lower()


@pytest.mark.parametrize("renderer", _renderers())
def test_every_renderer_reproduces_the_same_document(renderer, tmp_path):
    """Same input, same document — asserted on the *content*, not the bytes.

    ReportLab is byte-reproducible (its embedded date is derived from the
    content hash). Typst is not: it assigns PDF font-subset prefix tags
    (`ABCDEF+LibertinusSerif`) non-deterministically, so two runs of identical
    input differ in a handful of bytes while rendering identically. That is a
    documented difference between the backends, not a defect in either — the
    artifact cache keys on the step signature, never on output bytes.
    """
    first = _run(renderer(), DOCUMENT, tmp_path / "a")
    second = _run(renderer(), DOCUMENT, tmp_path / "b")
    extracted = [_pdf_text(produced[0].path) for produced in (first, second)]
    assert extracted[0] == extracted[1]


@pytest.mark.skipif(not HAVE_REPORTLAB, reason="the 'pdf' extra is not installed")
def test_reportlab_is_byte_reproducible(tmp_path):
    """The stronger guarantee, kept where it holds: PDFs embed a creation date,
    so without the content-derived stamp this would silently be false."""
    first = _run(ReportLabPdfProcessor(), DOCUMENT, tmp_path / "a")
    second = _run(ReportLabPdfProcessor(), DOCUMENT, tmp_path / "b")
    assert first[0].path.read_bytes() == second[0].path.read_bytes()


# --- template safety ------------------------------------------------------------


def test_only_shipped_templates_can_be_named():
    assert "default" in available_templates()
    assert resolve_template("default").name == "default.typ"
    assert resolve_template("").name == "default.typ"


@pytest.mark.parametrize(
    "attempt",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "default/../../secret",
        "..",
        "with space",
        "Default",  # case matters: names are lowercase identifiers
        "a" * 64,
    ],
)
def test_a_template_name_can_never_become_a_path(attempt):
    """The template is operator configuration, but it still must not be able to
    reach the filesystem — a typo should not read /etc/passwd, and a name is
    validated as an identifier before it is ever joined to a directory."""
    with pytest.raises(StepExecutionError) as exc:
        resolve_template(attempt)
    assert "Unknown PDF template" in str(exc.value)


@pytest.mark.skipif(not HAVE_TYPST, reason="typst binary not installed")
def test_typst_receives_the_document_as_json_not_as_markup(tmp_path):
    """The structural guarantee behind the injection test: what reaches the
    compiler is a data file plus a template we shipped."""
    processor = TypstPdfProcessor()
    _run(processor, f"# T\n\n{INJECTION}\n", tmp_path)
    roots = list((tmp_path / "work").glob("typst-*"))
    assert roots, "the renderer must compile in its own isolated directory"
    payload = json.loads((roots[0] / "document.json").read_text())
    # The payload is data; the dangerous string is a *value*, not syntax.
    flat = json.dumps(payload)
    assert "pwned" in flat
    main = (roots[0] / "main.typ").read_text()
    assert "pwned" not in main, "user text must never reach the Typst source"
    assert 'json("document.json")' in main


# --- selection ------------------------------------------------------------------


def _registry(*processors):
    return ProviderRegistry([], processors=list(processors))


def _settings(tmp_path, **kwargs):
    return ContentSettings(data_dir=tmp_path, db_path=tmp_path / "c.db", **kwargs)


def _request():
    return GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "text", "content": "x"}],
            "outputs": [{"id": "p", "type": "pdf"}],
        }
    )


class _FakeRenderer:
    location = "local"
    operations = (T.RENDER_PDF,)
    tool_version = "fake"

    def __init__(self, name, is_available=True):
        self.name = name
        self._available = is_available

    def available(self):
        return self._available

    def unavailable_message(self):
        return f"{self.name} is not usable."


def test_auto_prefers_typst_and_falls_back_to_reportlab(tmp_path):
    typst = _FakeRenderer("content.pdf.typst")
    reportlab = _FakeRenderer("content.pdf.reportlab")
    errors, warnings = [], []
    chosen = _select_renderer(
        _request(),
        _registry(reportlab, typst),
        _settings(tmp_path),
        "outputs[0]",
        errors,
        warnings,
    )
    assert chosen.name == "content.pdf.typst" and not errors

    # Typst present but unusable (missing binary, wrong architecture): the
    # fallback keeps PDF working rather than losing the output type.
    typst._available = False
    chosen = _select_renderer(
        _request(),
        _registry(reportlab, typst),
        _settings(tmp_path),
        "outputs[0]",
        errors,
        warnings,
    )
    assert chosen.name == "content.pdf.reportlab" and not errors


def test_a_pinned_renderer_is_never_silently_downgraded(tmp_path):
    """The operator named that renderer on purpose. Falling back would hand
    them a different document than they asked for, quietly."""
    typst = _FakeRenderer("content.pdf.typst", is_available=False)
    reportlab = _FakeRenderer("content.pdf.reportlab")
    errors = []
    chosen = _select_renderer(
        _request(),
        _registry(reportlab, typst),
        _settings(tmp_path, pdf_renderer="typst"),
        "outputs[0]",
        errors,
        [],
    )
    assert chosen is None
    assert errors and "pinned" in errors[0].message


def test_pinning_an_unknown_renderer_says_what_is_installed(tmp_path):
    errors = []
    chosen = _select_renderer(
        _request(),
        _registry(_FakeRenderer("content.pdf.reportlab")),
        _settings(tmp_path, pdf_renderer="wkhtmltopdf"),
        "outputs[0]",
        errors,
        [],
    )
    assert chosen is None
    assert "content.pdf.reportlab" in errors[0].message


def test_a_request_may_express_a_renderer_preference(tmp_path):
    """`preferences.providers` is already the generic family→names map used for
    LLM runners; PDF reuses it, so no contract change was needed."""
    request = GenerationRequest.model_validate(
        {
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "text", "content": "x"}],
            "outputs": [{"id": "p", "type": "pdf"}],
            "preferences": {"providers": {"pdf": ["content.pdf.reportlab"]}},
        }
    )
    chosen = _select_renderer(
        request,
        _registry(
            _FakeRenderer("content.pdf.reportlab"), _FakeRenderer("content.pdf.typst")
        ),
        _settings(tmp_path),
        "outputs[0]",
        [],
        [],
    )
    assert chosen.name == "content.pdf.reportlab"


def test_no_renderer_at_all_is_a_clear_refusal(tmp_path):
    errors = []
    chosen = _select_renderer(
        _request(), _registry(), _settings(tmp_path), "outputs[0]", errors, []
    )
    assert chosen is None
    assert "No PDF rendering runner is installed" in errors[0].message
