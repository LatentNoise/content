"""PDF rendering: one transformation, two implementations.

``document.render_pdf`` is the logical transformation; ``pdf.render`` is the
public capability; these are the runners that implement it. Both consume the
neutral document model — neither parses Markdown — so the two backends are
guaranteed to lay out the same parse.

Selection happens in the planner, not here, so the ExecutionPlan records which
renderer will run and the artifact's provenance names it.
"""

from content.processors.pdf.base import DEFAULT_PAGE_SIZE, PAGE_SIZES
from content.processors.pdf.reportlab_backend import ReportLabPdfProcessor
from content.processors.pdf.typst_backend import (
    TypstPdfProcessor,
    available_templates,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "PAGE_SIZES",
    "ReportLabPdfProcessor",
    "TypstPdfProcessor",
    "available_templates",
]
