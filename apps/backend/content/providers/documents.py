"""DocumentProvider — plain text and Markdown documents as sources.

The `file` and `text` supply modes existed in the contract but only ever reached
ffmpeg, so a `.txt` or `.md` file was `source_type_not_supported`. This provider
completes the non-media vertical on the local side, alongside the web-page
reader.

**PDF is deliberately not implemented.** Extracting text from a PDF well needs a
real dependency (layout, encodings, ligatures, columns), and a shallow attempt
would produce plausible-looking garbage. A `.pdf` is therefore recognised and
answered with `output_type_not_supported` — "valid but not implemented" is a
different answer from "invalid" (INV-014), and it keeps the door open without
lying about what works today.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from content.config import uploads_root
from content.domain import errors as codes
from content.domain.analysis import (
    AnalysisError,
    NormalizedResource,
    SourceAnalysis,
    TextFacts,
)
from content.domain.errors import ValidationIssue
from content.domain.plan import PlanStep
from content.domain.request import FileSource, SourceDescriptor, TextSource
from content.planning import transformations as T
from content.providers.base import (
    AnalysisContext,
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)
from content.providers.ffmpeg import check_path_allowed

READER_VERSION = "1"
MAX_BYTES = 10 * 1024 * 1024

MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {".txt", ".text"}
READABLE_SUFFIXES = MARKDOWN_SUFFIXES | TEXT_SUFFIXES
# Recognised, understood, and honestly refused until a real reader is warranted.
DEFERRED_SUFFIXES = {".pdf", ".docx", ".epub", ".odt", ".rtf"}


def _title_from(markdown: str, fallback: str) -> str:
    """A Markdown document's title is its first heading, when it has one."""
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
        if line.strip():
            break
    return fallback


class DocumentProvider:
    """Reads local text/Markdown documents and inline `text` sources."""

    name = "document"
    # Ahead of ffmpeg (20), which claims *every* file: the narrower claim wins,
    # so a .md is read directly instead of paying a failed ffprobe first. ffmpeg
    # still follows as a fallback if this reader cannot make sense of the file.
    analysis_priority = 15
    operations = (T.TEXT_EXTRACT,)
    location = "local"

    def __init__(self) -> None:
        self.tool_version = f"document-reader/{READER_VERSION}"

    def supports(self, source: SourceDescriptor) -> bool:
        if isinstance(source, TextSource):
            return True
        if isinstance(source, FileSource):
            suffix = Path(source.path).suffix.lower()
            # Deferred formats are claimed on purpose: refusing them here with a
            # reason beats letting them fall through to "no provider at all".
            return suffix in READABLE_SUFFIXES or suffix in DEFERRED_SUFFIXES
        return False

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        if isinstance(source, TextSource):
            digest = hashlib.sha256(
                f"{source.content}:{READER_VERSION}".encode()
            ).hexdigest()
            return f"{self.name}:text:{digest}"
        assert isinstance(source, FileSource)
        path = check_path_allowed(
            source.path,
            ctx.settings.allowed_input_roots,
            engine_roots=(uploads_root(ctx.settings),),
        )
        try:
            stat = path.stat()
            # Content identity without reading the file: size + mtime is enough
            # for a local document and keeps the probe cheap.
            fingerprint = f"{path}:{stat.st_size}:{stat.st_mtime_ns}:{READER_VERSION}"
        except OSError as exc:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message=f"Cannot read '{source.path}': {exc}.",
                    details={"provider": self.name},
                )
            ) from exc
        return f"{self.name}:file:{hashlib.sha256(fingerprint.encode()).hexdigest()}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        if isinstance(source, TextSource):
            body = source.content
            return self._analysis(
                source.id, body, title="", rtype="text", mime="text/plain"
            )

        assert isinstance(source, FileSource)
        path = check_path_allowed(
            source.path,
            ctx.settings.allowed_input_roots,
            engine_roots=(uploads_root(ctx.settings),),
        )
        suffix = path.suffix.lower()
        if suffix in DEFERRED_SUFFIXES:
            # Terminal: without it the chain fell through to ffmpeg, which
            # answered "ffprobe could not analyze the file" — technically true,
            # useless to the caller, and hiding the real answer (D-27).
            raise AnalysisError(
                ValidationIssue(
                    code=codes.SOURCE_TYPE_NOT_SUPPORTED,
                    message=(
                        f"'{suffix}' documents are recognised but not yet readable "
                        "by this installation."
                    ),
                    details={"provider": self.name, "suffix": suffix},
                ),
                terminal=True,
            )
        body = self._read(path)
        is_markdown = suffix in MARKDOWN_SUFFIXES
        return self._analysis(
            source.id,
            body,
            title=_title_from(body, path.stem) if is_markdown else path.stem,
            rtype="document",
            mime="text/markdown" if is_markdown else "text/plain",
            size=path.stat().st_size,
        )

    def _read(self, path: Path) -> str:
        try:
            if path.stat().st_size > MAX_BYTES:
                raise AnalysisError(
                    ValidationIssue(
                        code=codes.ANALYSIS_FAILED,
                        message=f"Document exceeds the {MAX_BYTES} byte limit.",
                        details={"provider": self.name, "limit_bytes": MAX_BYTES},
                    )
                )
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AnalysisError(
                ValidationIssue(
                    code=codes.ANALYSIS_FAILED,
                    message=f"Cannot read the document: {exc}.",
                    details={"provider": self.name},
                )
            ) from exc

    def _analysis(
        self,
        source_id: str,
        body: str,
        *,
        title: str,
        rtype: str,
        mime: str,
        size: int | None = None,
    ) -> SourceAnalysis:
        words = len(body.split())
        return SourceAnalysis(
            source_id=source_id,
            resource=NormalizedResource(
                resource_type=rtype,
                title=title,
                mime_type=mime,
                size_bytes=size,
                detected_provider=self.name,
            ),
            text=TextFacts(
                has_text=words > 0,
                word_count=words,
                extractor=self.tool_version,
            ),
        )

    # --- execution -------------------------------------------------------------

    def execute(self, step: PlanStep, ctx: ExecutionContext) -> list[ProducedFile]:
        if step.operation != T.TEXT_EXTRACT:
            raise StepExecutionError(
                "operation_not_supported",
                f"Provider '{self.name}' cannot execute '{step.operation}'.",
            )
        inline = step.params.get("content")
        if inline is not None:
            body, title = inline, ""
        else:
            path = check_path_allowed(
                step.params.get("path", ""), ctx.settings.allowed_input_roots
            )
            body = path.read_text(encoding="utf-8", errors="replace")
            title = (
                _title_from(body, path.stem)
                if path.suffix.lower() in MARKDOWN_SUFFIXES
                else path.stem
            )
        if not body.strip():
            raise StepExecutionError("no_output", "The document is empty.")

        # A .md file is already the canonical form; a .txt asked for as markdown
        # is wrapped with its title rather than invented structure.
        want_markdown = step.params.get("format", "markdown") == "markdown"
        if want_markdown:
            out = (
                body
                if body.lstrip().startswith("#") or not title
                else f"# {title}\n\n{body}"
            )
            suffix, media_type = ".md", "text/markdown"
        else:
            from content.providers.webpage import to_plain_text

            out, suffix, media_type = to_plain_text(body), ".txt", "text/plain"

        ctx.workdir.mkdir(parents=True, exist_ok=True)
        target = ctx.workdir / f"text-{step.id}{suffix}"
        target.write_text(out, encoding="utf-8")
        return [
            ProducedFile(
                path=target,
                media_type=media_type,
                attributes={"title": title, "word_count": len(body.split())},
            )
        ]
