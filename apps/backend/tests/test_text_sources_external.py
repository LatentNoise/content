"""Opt-in real run of the non-media vertical (`-m external`).

Uses a real HTTP server on localhost and a real socket — no Internet, but a
genuine request through `urllib`, which the hermetic suite stubs. It proves the
fetch path itself works, not just the extraction.
"""

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from content.config import ContentSettings
from content.domain.request import UrlSource
from content.providers.base import AnalysisContext
from content.providers.webpage import WebPageProvider

pytestmark = pytest.mark.external

PAGE = """<html lang="en"><head>
<meta property="og:title" content="A Real Fetch">
<meta name="author" content="Integration">
</head><body><nav>skip me</nav><article>
<h1>A Real Fetch</h1><p>Served over a real socket.</p>
<p>With <a href="https://example.com/x">a link</a>.</p>
</article></body></html>"""


@pytest.fixture
def served(tmp_path):
    (tmp_path / "page.html").write_text(PAGE)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/page.html"
    server.shutdown()


def test_a_real_http_fetch_produces_a_clean_reading(served, tmp_path):
    settings = ContentSettings(
        data_dir=tmp_path,
        db_path=tmp_path / "db",
        # The server is on loopback, which the SSRF guard blocks by default.
        allow_private_networks=True,
    )
    analysis = WebPageProvider().analyze(
        UrlSource(id="a", type="url", uri=served),
        AnalysisContext(settings, tmp_path / "w"),
    )
    assert analysis.resource.resource_type == "webpage"
    assert analysis.resource.title == "A Real Fetch"
    assert analysis.resource.author == "Integration"
    assert analysis.text.has_text


def test_the_ssrf_guard_blocks_the_same_real_server(served, tmp_path):
    """Same reachable URL, guard on: the request must not be made at all."""
    from content.domain.analysis import AnalysisError

    settings = ContentSettings(data_dir=tmp_path, db_path=tmp_path / "db")
    with pytest.raises(AnalysisError) as exc:
        WebPageProvider().analyze(
            UrlSource(id="a", type="url", uri=served),
            AnalysisContext(settings, tmp_path / "w"),
        )
    assert exc.value.issue.code == "url_not_allowed"
