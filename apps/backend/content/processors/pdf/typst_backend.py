"""Typst implementation of ``document.render_pdf``.

The high-quality renderer: real typography (ligatures, hyphenation, justified
paragraphs, proper heading hierarchy) and byte-reproducible output, from a
17 MB static binary with no runtime dependencies.

**The document never becomes Typst source.** Typst is a full programming
language, so building `.typ` markup by concatenating summary text would let a
web page or an LLM decide what the compiler executes. Instead:

    Document → document.json ─┐
                              ├─► server-owned template.typ ─► typst ─► PDF
    page size, title → meta.json ─┘

The template is shipped with the engine and selected *by name*; the public
contract carries no template source, no path and no compiler options. Everything
from the outside world travels as JSON values that the template places with
`strong()`/`emph()`/`link()`.

Execution goes through the shared `run_process`: argument list, no shell,
mandatory timeout, cooperative cancellation, separated stdout/stderr logs and an
isolated per-step working directory. The finished PDF is promoted by the normal
artifact path, which is atomic.

Fonts are pinned deliberately: `--ignore-system-fonts` plus an explicit
`--font-path`, because Typst otherwise discovers whatever the host happens to
have installed and the same request would render differently on two machines.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from content.documents.fonts import FontCoverage, load_coverage
from content.documents.model import Document
from content.execution.process import run_process
from content.processors.pdf.base import BasePdfProcessor
from content.providers.base import ExecutionContext, StepExecutionError

RENDERER_NAME = "content.pdf.typst"

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "default"

# Where a deployment may drop extra fonts. Everything Typst is allowed to use
# comes from here plus its own embedded faces; the host's font book is ignored.
DEFAULT_FONT_PATHS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
)

# Typst embeds these and needs no files for them. Libertinus covers Latin,
# Greek and Cyrillic; there is no CJK, which is exactly the gap the coverage
# check has to catch because Typst itself reports nothing.
_EMBEDDED_RANGES = (
    (0x0020, 0x024F),  # ASCII, Latin-1 Supplement, Latin Extended-A/B
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x04FF),  # Cyrillic
    (0x2000, 0x206F),  # general punctuation
    (0x20A0, 0x20BF),  # currency
    (0x2190, 0x21FF),  # arrows
    (0x2200, 0x22FF),  # mathematical operators
)

# A template name is a server-side identifier, never a path. Anything outside
# this shape cannot reach the filesystem.
_TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def available_templates() -> list[str]:
    return sorted(path.stem for path in TEMPLATE_DIR.glob("*.typ"))


def resolve_template(name: str) -> Path:
    """Map a template *name* to the shipped file.

    Rejects anything that is not a plain identifier, then confirms the resolved
    path really is inside the template directory — so neither `../` nor an
    absolute path nor a symlink can escape, whatever the setting contains.
    """
    candidate = (name or DEFAULT_TEMPLATE).strip()
    if not _TEMPLATE_NAME.match(candidate):
        raise StepExecutionError(
            "provider_error",
            f"Unknown PDF template '{name}'. Available: "
            f"{', '.join(available_templates())}.",
        )
    path = (TEMPLATE_DIR / f"{candidate}.typ").resolve()
    if not path.is_file() or path.parent != TEMPLATE_DIR.resolve():
        raise StepExecutionError(
            "provider_error",
            f"Unknown PDF template '{candidate}'. Available: "
            f"{', '.join(available_templates())}.",
        )
    return path


class TypstPdfProcessor(BasePdfProcessor):
    name = RENDERER_NAME

    def __init__(
        self,
        binary: str = "typst",
        font_path: str = "",
        template: str = DEFAULT_TEMPLATE,
    ):
        super().__init__(font_path)
        self.binary = binary or "typst"
        self.template = template or DEFAULT_TEMPLATE
        self._available: bool | None = None

    # --- availability ----------------------------------------------------------

    def available(self) -> bool:
        """The binary exists and answers `--version`.

        Probing execution rather than presence on PATH: a file that cannot run
        on this architecture would otherwise be selected and fail every job.
        """
        if self._available is not None:
            return self._available
        resolved = shutil.which(self.binary)
        if resolved is None:
            self._available = False
            return False
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            self._available = False
            return False
        if result.returncode != 0:
            self._available = False
            return False
        self.tool_version = result.stdout.strip() or "typst"
        self._available = True
        return True

    def unavailable_message(self) -> str:
        return (
            f"PDF rendering with Typst requires the '{self.binary}' binary "
            "(set CONTENT_TYPST_BINARY, or use CONTENT_PDF_RENDERER=reportlab)."
        )

    # --- fonts -----------------------------------------------------------------

    def font_paths(self) -> list[Path]:
        configured = [Path(self.font_path)] if self.font_path else []
        return configured + [Path(p) for p in DEFAULT_FONT_PATHS]

    def coverages(self, text: str) -> list[FontCoverage]:
        """Typst's embedded faces plus every font file under the controlled
        font path. Nothing else: system fonts are switched off at compile time,
        so counting them would report coverage the render will not have."""
        found = [FontCoverage(name="typst-embedded", ranges=list(_EMBEDDED_RANGES))]
        for root in self.font_paths():
            if not root.is_dir():
                continue
            for pattern in ("*.ttf", "*.otf", "*.ttc"):
                for path in sorted(root.rglob(pattern))[:200]:
                    coverage = load_coverage(path)
                    if coverage is not None:
                        found.append(coverage)
        return found

    # --- rendering -------------------------------------------------------------

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
        # The plan records the template, so a replay renders what the plan says
        # rather than what this process happens to be configured with.
        template = resolve_template(str(params.get("template") or self.template))
        # An isolated directory per render: it becomes the Typst compile root,
        # so the template can read exactly the two JSON files we put there and
        # nothing else on the filesystem.
        root = ctx.workdir / f"typst-{target.stem}"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        (root / "document.json").write_text(
            json.dumps(document.as_dict(), ensure_ascii=False), encoding="utf-8"
        )
        (root / "meta.json").write_text(
            json.dumps({"title": title, "page_size": page_size}, ensure_ascii=False),
            encoding="utf-8",
        )
        shutil.copyfile(template, root / "main.typ")

        args = [
            self.binary,
            "compile",
            "--root",
            str(root),
            # Reproducibility: without this Typst picks up the host's fonts and
            # the same request renders differently on two machines.
            "--ignore-system-fonts",
        ]
        for path in self.font_paths():
            if path.is_dir():
                args += ["--font-path", str(path)]
        args += [str(root / "main.typ"), str(target)]

        result = run_process(
            args,
            cwd=root,
            timeout_seconds=ctx.timeout_seconds,
            stdout_log=ctx.stdout_log,
            stderr_log=ctx.stderr_log,
            cancel_check=ctx.cancel_check,
        )
        if result.cancelled:
            raise StepExecutionError("cancelled", "Step cancelled.")
        if result.timed_out:
            raise StepExecutionError("timeout", "Typst timed out.")
        if result.returncode != 0:
            detail = _last_error(ctx.stderr_log)
            raise StepExecutionError(
                "provider_error",
                f"Typst exited with code {result.returncode}"
                + (f": {detail}" if detail else "."),
            )
        return _page_count(target)


def _last_error(stderr_log: Path) -> str:
    """The tail of Typst's diagnostics, for a message that says what broke."""
    try:
        lines = [line.strip() for line in stderr_log.read_text().splitlines()]
    except OSError:
        return ""
    meaningful = [line for line in lines if line]
    return " ".join(meaningful[-3:])[:300]


def _page_count(pdf: Path) -> int:
    """Page count straight from the PDF, so it reflects what was produced
    rather than what we hoped for. Falls back to 1 rather than failing a
    successful render over a cosmetic attribute."""
    try:
        raw = pdf.read_bytes()
    except OSError:
        return 1
    counts = [int(m.group(1)) for m in re.finditer(rb"/Count\s+(\d+)", raw)]
    if counts:
        return max(counts)
    return max(len(re.findall(rb"/Type\s*/Page[^s]", raw)), 1)
