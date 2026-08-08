"""WebPageProvider — reading an article, for the first non-media vertical.

"A source is not necessarily a video" had been a principle with no
implementation: every analysis provider spoke media, so an article URL got
`source_type_not_supported`. This provider is the other half.

**Routing is decided by what the analysis finds, not by a regex.** Every URL is
offered to yt-dlp first (`analysis_priority = 10`); this provider sits last
(`analysis_priority = 900`) and only sees URLs yt-dlp did not recognise as
media. A news article that embeds a video therefore stays a video — correctly,
because yt-dlp found one.

Extraction is deliberately dependency-free: a small readability-style pass over
`html.parser` that drops chrome (nav, script, style, header/footer) and keeps
the densest block of prose. It produces **Markdown**, because the structure —
title, headings, links — is what makes the `markdown` artifact faithful and is
unrecoverable once flattened to plain text.

Limits, all deliberate and documented:

* **No JavaScript.** The fetched HTML is what is parsed; a page that renders its
  body client-side yields little text and says so honestly (`has_text=False`)
  rather than inventing content.
* **Bounded.** Response size and read time are capped, so a hostile or endless
  page cannot exhaust the host.
* **SSRF-guarded.** This provider fetches the URL *itself* — a new outbound path
  that does not inherit yt-dlp's guard, so it calls the same check explicitly.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from content.domain import errors as codes
from content.domain.analysis import (
    AnalysisError,
    NormalizedResource,
    SourceAnalysis,
    TextFacts,
)
from content.domain.errors import ValidationIssue
from content.domain.plan import PlanStep
from content.domain.request import SourceDescriptor, UrlSource
from content.planning import transformations as T
from content.providers.base import (
    AnalysisContext,
    ExecutionContext,
    ProducedFile,
    StepExecutionError,
)
from content.providers.ytdlp import check_url_allowed

# Bump when the extraction changes the bytes produced for the same page: it is
# part of the cache identity, so a better reader invalidates stale readings.
EXTRACTOR_VERSION = "1"

MAX_BYTES = 5 * 1024 * 1024  # a page that large is not an article
FETCH_TIMEOUT_SECONDS = 20.0

_HTML_TYPES = ("text/html", "application/xhtml+xml")
# Chrome: never part of the article, and the main source of extraction noise.
_DROP = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "svg",
    "iframe",
    "template",
    "button",
}
_BLOCK = {"p", "div", "section", "article", "li", "br", "tr", "blockquote", "pre"}
_HEADINGS = {
    "h1": "#",
    "h2": "##",
    "h3": "###",
    "h4": "####",
    "h5": "#####",
    "h6": "######",
}
_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


class _Reader(HTMLParser):
    """Collects the document's prose as Markdown, plus its head metadata.

    Not a general HTML→Markdown converter: it keeps what an article is made of
    (headings, paragraphs, list items, links) and discards the rest. Anything it
    cannot represent degrades to its text, never to raw markup.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._link: str | None = None
        self._link_text: list[str] = []

    # --- metadata --------------------------------------------------------------

    def _record_meta(self, attrs: dict[str, str]) -> None:
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        value = (attrs.get("content") or "").strip()
        if key and value and key not in self.meta:
            self.meta[key] = value

    # --- structure -------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self._record_meta(attributes)
            return
        if tag == "html" and attributes.get("lang"):
            self.meta.setdefault("_lang", attributes["lang"])
        if tag in _DROP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self._parts.append(f"\n\n{_HEADINGS[tag]} ")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag == "a":
            href = attributes.get("href", "")
            # Only real destinations become links; anchors and JS handlers are
            # noise in a reading and would produce dead markdown.
            if href and not href.startswith(("#", "javascript:")):
                self._link = href
                self._link_text = []
        elif tag in _BLOCK:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._link is not None:
            text = "".join(self._link_text).strip()
            self._parts.append(f"[{text}]({self._link})" if text else "")
            self._link, self._link_text = None, []
        elif tag in _HEADINGS or tag in _BLOCK:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = _WS.sub(" ", data)
        if not text.strip():
            return
        if self._link is not None:
            self._link_text.append(text)
        else:
            self._parts.append(text)

    # --- result ----------------------------------------------------------------

    def markdown(self) -> str:
        body = "".join(self._parts)
        body = "\n".join(line.strip() for line in body.splitlines())
        return _BLANKS.sub("\n\n", body).strip()


def to_plain_text(markdown: str) -> str:
    """Flatten Markdown to prose: drop heading markers and list bullets, keep a
    link's text and discard its target. One-way by design."""
    out = []
    for line in markdown.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^-\s+", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        out.append(line)
    return _BLANKS.sub("\n\n", "\n".join(out)).strip()


def extract(html: str) -> tuple[str, str, dict[str, str]]:
    """``(markdown, title, metadata)`` for a page. Never raises on bad HTML —
    `html.parser` is lenient and a partial reading beats an exception."""
    reader = _Reader()
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 - malformed markup yields what was parsed
        pass
    meta = reader.meta
    title = (
        meta.get("og:title") or meta.get("twitter:title") or reader.title or ""
    ).strip()
    return reader.markdown(), unescape(title), meta


def fetch(url: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> tuple[str, str]:
    """``(html, content_type)``. Raises AnalysisError with a normalized issue."""
    request = urllib.request.Request(
        url,
        headers={
            "accept": "text/html,application/xhtml+xml",
            # Identify honestly rather than impersonating a browser.
            "user-agent": "content/webpage-reader",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get("content-type") or "").lower()
            raw = response.read(MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnalysisError(
            ValidationIssue(
                code=codes.ANALYSIS_FAILED,
                message=f"Could not fetch the page: {exc}.",
                details={"provider": "webpage"},
            )
        ) from exc
    if len(raw) > MAX_BYTES:
        raise AnalysisError(
            ValidationIssue(
                code=codes.ANALYSIS_FAILED,
                message=f"Page exceeds the {MAX_BYTES} byte reading limit.",
                details={"provider": "webpage", "limit_bytes": MAX_BYTES},
            )
        )
    return raw.decode(charset, errors="replace"), content_type


class WebPageProvider:
    """Reads a web page a media extractor did not claim."""

    name = "webpage"
    # Last resort for a URL: everything specific gets first refusal.
    analysis_priority = 900
    operations = (T.TEXT_EXTRACT,)
    location = "local"

    def __init__(self) -> None:
        self.tool_version = f"webpage-reader/{EXTRACTOR_VERSION}"

    def supports(self, source: SourceDescriptor) -> bool:
        return isinstance(source, UrlSource)

    def resource_key(self, source: SourceDescriptor, ctx: AnalysisContext) -> str:
        assert isinstance(source, UrlSource)
        # The extractor version is part of the identity: improving the reader
        # must invalidate readings produced by the old one.
        digest = hashlib.sha256(
            f"{source.uri}:{EXTRACTOR_VERSION}".encode()
        ).hexdigest()
        return f"{self.name}:url:{digest}"

    def analyze(self, source: SourceDescriptor, ctx: AnalysisContext) -> SourceAnalysis:
        assert isinstance(source, UrlSource)
        # This provider performs the request itself, so it does not inherit the
        # guard applied on yt-dlp's behalf — it must call it explicitly.
        check_url_allowed(source.uri, ctx.settings.allow_private_networks)
        html, content_type = fetch(source.uri)
        if content_type and not any(t in content_type for t in _HTML_TYPES):
            raise AnalysisError(
                ValidationIssue(
                    code=codes.SOURCE_TYPE_NOT_SUPPORTED,
                    message=(
                        f"'{content_type.split(';')[0]}' is not a readable web page."
                    ),
                    details={"provider": self.name, "content_type": content_type},
                )
            )
        markdown, title, meta = extract(html)
        words = len(markdown.split())
        return SourceAnalysis(
            source_id=source.id,
            resource=NormalizedResource(
                resource_type="webpage",
                title=title,
                description=(
                    meta.get("og:description") or meta.get("description") or ""
                )[:500],
                author=meta.get("author") or meta.get("article:author") or "",
                published_at=meta.get("article:published_time")
                or meta.get("date")
                or "",
                languages=[meta["_lang"].split("-")[0]] if meta.get("_lang") else [],
                mime_type="text/html",
                canonical_url=meta.get("og:url") or source.uri,
                thumbnail_url=meta.get("og:image") or "",
                detected_provider=self.name,
            ),
            text=TextFacts(
                # An empty reading is a fact, not a failure: a JS-rendered page
                # genuinely has no server-side text, and saying so is honest.
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
        uri = step.params.get("uri", "")
        check_url_allowed(uri, ctx.settings.allow_private_networks)
        ctx.on_progress(5.0, f"reading {uri}")
        html, _ = fetch(uri, timeout=min(FETCH_TIMEOUT_SECONDS, ctx.timeout_seconds))
        markdown, title, _ = extract(html)
        if not markdown.strip():
            raise StepExecutionError(
                "no_output",
                "The page yielded no readable text (it may render client-side).",
            )
        return [_write(ctx.workdir, step, markdown, title)]


def _write(workdir: Path, step: PlanStep, markdown: str, title: str) -> ProducedFile:
    """Serialize the reading in the requested format. Markdown is canonical;
    `text` is a flattening of it, never a separate extraction."""
    want_markdown = step.params.get("format", "markdown") == "markdown"
    # The extraction usually already opens with the title as an <h1>; only add
    # one when it does not, or the reading starts by repeating itself.
    has_heading = markdown.lstrip().startswith("#")
    full = markdown if has_heading or not title else f"# {title}\n\n{markdown}"
    if want_markdown:
        body, suffix, media_type = full, ".md", "text/markdown"
    else:
        body, suffix, media_type = to_plain_text(full), ".txt", "text/plain"
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / f"text-{step.id}{suffix}"
    target.write_text(body, encoding="utf-8")
    return ProducedFile(
        path=target,
        media_type=media_type,
        attributes={"title": title, "word_count": len(markdown.split())},
    )
