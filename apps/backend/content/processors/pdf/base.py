"""The shared skeleton every PDF renderer runs through.

One `document.render_pdf` transformation, several implementations. Everything
that must be true regardless of the backend lives here — reading the material,
parsing it *once*, resolving the title, validating glyph coverage — so a new
renderer supplies only what is genuinely renderer-specific and cannot
accidentally skip a guarantee.

The coverage gate is the reason this is a base class rather than a convention.
Neither backend reports missing glyphs on its own: ReportLab draws a notdef box,
Typst exits 0 and draws tofu. Validating here, before the renderer is invoked,
means a document that cannot be drawn fails loudly instead of being published as
a page of blank squares.
"""

from __future__ import annotations

from pathlib import Path

from content.documents.fonts import (
    POLICY_ERROR,
    POLICY_REPLACE,
    FontCoverage,
    choose_replacement,
    describe_missing,
    missing_characters,
    missing_glyph_report,
    normalize_policy,
)
from content.documents.markdown import parse_markdown
from content.documents.model import Document
from content.domain.plan import PlanStep
from content.planning import transformations as T
from content.providers.base import (
    ExecutionContext,
    Material,
    ProducedFile,
    StepExecutionError,
)

DEFAULT_PAGE_SIZE = "a4"
PAGE_SIZES = ("a4", "letter")

# Materials this renderer knows how to read. A transcript or chapters output
# asked for as JSON arrives with a .json suffix; it is still text, and the
# planner has already warned that the serialization will land on the page.
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text", ".json")


class BasePdfProcessor:
    """StepRunner skeleton. Subclasses provide `available`, `coverages` and
    `render`; the contract around them is fixed here."""

    name = "content.pdf"
    location = "local"
    operations = (T.RENDER_PDF,)

    def __init__(self, font_path: str = ""):
        self.font_path = font_path
        self.tool_version = ""

    # --- subclass hooks --------------------------------------------------------

    def available(self) -> bool:
        raise NotImplementedError

    def coverages(self, text: str) -> list[FontCoverage]:
        """Every font the backend could draw *text* with, so the base can tell
        whether anything is undrawable before spending a render on it."""
        raise NotImplementedError

    def render(
        self,
        document: Document,
        target: Path,
        ctx: ExecutionContext,
        *,
        page_size: str,
        title: str,
        params: dict,
    ) -> int:
        """Write the PDF to *target* and return the page count.

        *params* are the step's parameters, so a renderer reads what the plan
        recorded rather than what this process happens to be configured with —
        replaying a plan must reproduce the same document.
        """
        raise NotImplementedError

    def unavailable_message(self) -> str:
        raise NotImplementedError

    # --- the fixed contract ----------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != T.RENDER_PDF:
            raise StepExecutionError(
                "operation_not_supported",
                f"Processor '{self.name}' cannot execute '{step.operation}'.",
            )
        if not self.available():
            raise StepExecutionError("provider_error", self.unavailable_message())

        material = self._pick_material(ctx.input_materials)
        if material is None:
            raise StepExecutionError(
                "no_input", "No readable material was produced by the dependency step."
            )
        body = material.path.read_text(encoding="utf-8", errors="replace")
        if not body.strip():
            raise StepExecutionError("no_output", "The material is empty.")

        title = str(step.params.get("title") or material.attributes.get("title") or "")
        # Parsed exactly once, here: both renderers consume the model, so a
        # construct cannot lay out in one backend and vanish in the other.
        document = parse_markdown(body, title=title)
        if document.is_empty:
            raise StepExecutionError("no_output", "Nothing renderable in the material.")

        document, glyph_report = self._apply_missing_glyph_policy(document, ctx)

        page_size = str(step.params.get("page_size") or DEFAULT_PAGE_SIZE).lower()
        if page_size not in PAGE_SIZES:
            page_size = DEFAULT_PAGE_SIZE
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        target = ctx.workdir / f"document-{step.id}.pdf"
        pages = self.render(
            document,
            target,
            ctx,
            page_size=page_size,
            title=title,
            params=dict(step.params),
        )
        if not target.is_file() or target.stat().st_size == 0:
            raise StepExecutionError(
                "no_output", f"Renderer '{self.name}' produced no document."
            )
        return [
            ProducedFile(
                path=target,
                media_type="application/pdf",
                attributes={
                    "title": title,
                    "pages": pages,
                    # Provenance carries the runner name and tool version; this
                    # keeps the renderer visible on the artifact itself too.
                    "renderer": self.name,
                    # Present only when something could not be drawn, so its
                    # absence is a positive statement that the document is
                    # complete — not merely that nobody looked.
                    **({"missing_glyphs": glyph_report} if glyph_report else {}),
                },
            )
        ]

    def _apply_missing_glyph_policy(
        self, document: Document, ctx: ExecutionContext
    ) -> tuple[Document, dict | None]:
        """Resolve what to do about characters no available font can draw.

        A successful exit code is not a successful render: ReportLab draws
        notdef boxes and Typst exits 0 drawing tofu, so the engine has to decide
        rather than either backend. Deciding *here* is what makes the three
        policies mean exactly the same thing whichever renderer runs.

        - ``error``   refuse the step. The safety baseline, kept.
        - ``replace`` substitute a drawable placeholder and report. The default:
          one emoji in an LLM summary should not destroy an otherwise perfect
          document, but the loss must be visible on the page and on the artifact.
        - ``warn``    render unchanged and report. The escape hatch for an
          operator who knows their fonts; the only mode that can put an
          undrawable glyph on the page, and never silently.

        Returns the document to render plus a structured record, or ``None``
        when nothing was missing.
        """
        text = document.text_content()
        coverages = self.coverages(text)
        missing = missing_characters(text, coverages)
        if not missing:
            return document, None

        policy = normalize_policy(getattr(ctx.settings, "pdf_missing_glyphs", ""))
        if policy == POLICY_ERROR:
            raise StepExecutionError(
                "unsupported_glyphs",
                describe_missing(missing),
                details=missing_glyph_report(missing, policy),
            )

        if policy == POLICY_REPLACE:
            replacement = choose_replacement(coverages)
            undrawable = set(missing)
            document = document.map_text(
                lambda value: "".join(
                    replacement if char in undrawable else char for char in value
                )
            )
            report = missing_glyph_report(missing, policy, replacement)
        else:  # POLICY_WARN
            report = missing_glyph_report(missing, policy)

        ctx.on_progress(99.0, describe_missing(missing))
        return document, report

    def _pick_material(self, materials: list[Material]) -> Material | None:
        for material in materials:
            if material.path.suffix.lower() in TEXT_SUFFIXES:
                return material
        # Fall back to whatever the dependency produced: the planner only ever
        # binds this step behind a text-bearing one, so an unexpected suffix is
        # more likely a new text format than a genuine mismatch.
        return materials[0] if materials else None
