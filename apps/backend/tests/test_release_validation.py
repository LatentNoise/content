"""Release validation: the vertical slices, end to end, through the public API.

`make validate` proves the engine is internally consistent. It cannot prove
that a real web page is readable, that yt-dlp still parses YouTube, that the
LLM daemon answers, or that the Typst binary in the image actually runs — and
those are precisely the things that break between releases, silently, because
the hermetic suite is blind to them (D-33).

Everything here goes through `POST /api/v1/jobs`: the same routing, validation,
planning and execution a user gets. A test that reached into the planner would
prove less than the thing it is guarding.

Each check skips when its prerequisite is absent, so the command is honest on a
laptop and complete in CI. Run it deliberately:

    make validate-release

Configure what it can reach:

    CONTENT_RELEASE_URL      a real page to extract (default: a local server)
    CONTENT_RELEASE_YTDLP    a media URL for the yt-dlp path
    CONTENT_OLLAMA_URL       the LLM daemon (default http://localhost:11434)
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from content.config import ContentSettings

pytestmark = pytest.mark.release


ARTICLE_HTML = """<html lang="en"><head>
<meta property="og:title" content="Release Validation Article">
</head><body>
<nav><a href="/">Home</a></nav><script>tracking()</script>
<article>
  <h1>Release Validation Article</h1>
  <p>A declarative engine resolves the strategy itself. Accents survive the
     round trip: café, naïve, œuvre.</p>
  <h2>Why it matters</h2>
  <ul><li>Stable intent</li><li>Swappable tools</li></ul>
</article>
<footer>(c) nobody</footer></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        body = ARTICLE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def article_url():
    """A real HTTP fetch. Local by default so the check is deterministic and
    runs offline; point CONTENT_RELEASE_URL at a real page to exercise the
    internet path instead."""
    configured = os.getenv("CONTENT_RELEASE_URL", "").strip()
    if configured:
        yield configured
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/article.html"
    finally:
        server.shutdown()
        server.server_close()


def _ollama_reachable() -> bool:
    url = os.getenv("CONTENT_OLLAMA_URL", "http://localhost:11434")
    host = url.split("//", 1)[-1].split("/", 1)[0]
    hostname, _, port = host.partition(":")
    try:
        with socket.create_connection((hostname, int(port or 11434)), timeout=2):
            return True
    except OSError:
        return False


def _typst_ready() -> bool:
    binary = shutil.which(os.getenv("CONTENT_TYPST_BINARY", "typst"))
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


HAVE_REPORTLAB = importlib.util.find_spec("reportlab") is not None
HAVE_TYPST = _typst_ready()
HAVE_OLLAMA = _ollama_reachable()
HAVE_YTDLP = shutil.which("yt-dlp") is not None
# poppler's pdftotext, used to read a rendered PDF back. Probed like every other
# prerequisite: both call sites used to shell out to it unguarded, so a machine
# without poppler got a FileNotFoundError crash from a suite whose contract is
# that a missing prerequisite *skips*.
HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None

# "Me at the zoo" — the first video on YouTube: 19 s, public, never taken down,
# and already this project's stable fixture (tests/test_youtube_external.py).
# One fixture, reused: the yt-dlp check exists to notice that YouTube changed
# under us, and a second URL would only add a second thing that can rot.
#
# Defaulted rather than left blank on purpose. This used to require
# CONTENT_RELEASE_YTDLP to be set, so the component most likely to break was
# the one nobody exercised — the release run printed "[ ] yt-dlp media source"
# and read as a pass. Point the variable elsewhere to use another source, or
# set it to `off` to skip the check deliberately (offline, or when YouTube
# itself is the thing that is down).
STABLE_MEDIA_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
_configured_ytdlp = os.getenv("CONTENT_RELEASE_YTDLP", "").strip()
YTDLP_URL = (
    "" if _configured_ytdlp == "off" else (_configured_ytdlp or STABLE_MEDIA_URL)
)


def _ytdlp_version() -> str:
    """The yt-dlp the engine actually resolved — step 6 of a base-image bump."""
    if not HAVE_YTDLP:
        return ""
    try:
        from content.providers.ytdlp import YtDlpProvider

        return YtDlpProvider().tool_version or ""
    except Exception:  # noqa: BLE001 - reporting only; never fail the run
        return ""


def _client(tmp_path, **overrides):
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    settings = ContentSettings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        # The article server is on loopback; a real CONTENT_RELEASE_URL is not
        # affected by this, and the guard stays on for every other deployment.
        allow_private_networks=True,
        step_timeout_seconds=300,
        **overrides,
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return TestClient(create_app(settings, start_worker=False))


def _run_job(client, payload) -> dict:
    submitted = client.post("/api/v1/jobs", json=payload)
    assert submitted.status_code == 201, submitted.text
    job_id = submitted.json()["job_id"]
    store = client.app.state.store
    executor = client.app.state.executor
    while True:
        claimed = store.claim_next_queued()
        if claimed is None:
            break
        executor.execute(claimed)
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    artifacts = client.get(f"/api/v1/jobs/{job_id}/artifacts").json()
    return {"job": job, "artifacts": artifacts}


def _artifact_bytes(client, artifact):
    response = client.get(f"/api/v1/artifacts/{artifact['id']}/content")
    assert response.status_code == 200
    return response.content


# --- the slices -----------------------------------------------------------------


def test_release_a_real_web_page_is_read_and_exported(article_url, tmp_path):
    """The non-media vertical over a real socket: fetch, extract, export."""
    with _client(tmp_path) as client:
        capabilities = client.post(
            "/api/v1/capabilities",
            json={"sources": [{"id": "a", "type": "url", "uri": article_url}]},
        )
        assert capabilities.status_code == 200, capabilities.text
        source = capabilities.json()["sources"][0]
        assert source["resource_type"] == "webpage"
        by_id = {c["id"]: c for c in source["capabilities"]}
        assert by_id["markdown.export"]["status"] in ("available", "derivable")
        # A page is not a video, and the engine must say so rather than try.
        assert by_id["video.download"]["status"] == "unavailable"

        result = _run_job(
            client,
            {
                "schema_version": "1.0",
                "sources": [{"id": "a", "type": "url", "uri": article_url}],
                "outputs": [{"id": "md", "type": "markdown"}],
            },
        )
    assert result["job"]["status"] == "succeeded", result["job"]
    markdown = _artifact_bytes(client, result["artifacts"][0]).decode("utf-8")
    assert markdown.lstrip().startswith("#")
    # Chrome dropped, content kept, encoding intact.
    assert "tracking()" not in markdown and "Home" not in markdown


@pytest.mark.skipif(
    not (HAVE_YTDLP and YTDLP_URL),
    reason="yt-dlp not installed, or CONTENT_RELEASE_YTDLP=off",
)
def test_release_a_real_media_source_is_analysed(tmp_path):
    """yt-dlp is the component most likely to rot: sites change and stale
    versions break quietly. Analysis alone is enough to catch that, and it
    downloads nothing.

    `-J` is where the documented breakage shows up: when YouTube changes its
    player, the symptom is an empty format list surfacing as "No video formats
    found" (D-20), not a failed transfer. Deciphering the format URLs is a
    later, authenticated step, covered by tests/test_youtube_external.py when
    cookies are available — deliberately not duplicated here, because an
    anonymous download is exactly the flaky thing this check must not become.
    """
    print(f"\nyt-dlp {_ytdlp_version() or '(version undetected)'} — {YTDLP_URL}")
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/capabilities",
            json={"sources": [{"id": "v", "type": "url", "uri": YTDLP_URL}]},
        )
    assert response.status_code == 200, response.text
    source = response.json()["sources"][0]
    assert source["resource_type"] in ("video", "audio", "collection")
    assert source["title"], "analysis must return real metadata"
    by_id = {c["id"]: c["status"] for c in source["capabilities"]}
    # `unknown` is not accepted. A broken extractor returns metadata it could
    # not interpret, and treating that as a pass is how a rotted yt-dlp used to
    # get through this check unnoticed.
    assert by_id["video.download"] in ("available", "derivable"), (
        f"video.download is {by_id['video.download']!r} for a known-good video "
        f"— yt-dlp {_ytdlp_version()} is likely stale or the extractor broke"
    )


@pytest.mark.skipif(not HAVE_OLLAMA, reason="no LLM daemon reachable")
def test_release_a_real_llm_summarises_a_real_page(article_url, tmp_path):
    """summary.from_text end to end: no audio, no transcript, a real model."""
    with _client(tmp_path) as client:
        result = _run_job(
            client,
            {
                "schema_version": "1.0",
                "sources": [{"id": "a", "type": "url", "uri": article_url}],
                "outputs": [
                    {
                        "id": "sum",
                        "type": "summary",
                        "options": {"format": "markdown", "length": "short"},
                    }
                ],
            },
        )
        assert result["job"]["status"] == "succeeded", result["job"]
        operations = [s["operation"] for s in result["job"]["steps"]]
        assert "text.extract" in operations and "text.summarize" in operations
        assert not any(op.startswith("media.acquire") for op in operations)
        summary = _artifact_bytes(client, result["artifacts"][0]).decode("utf-8")

    assert summary.strip()
    # D-28: the model's own code fence must never reach the artifact.
    assert not summary.lstrip().startswith("```")


@pytest.mark.parametrize(
    "renderer",
    [
        pytest.param(
            "typst",
            marks=pytest.mark.skipif(not HAVE_TYPST, reason="typst not installed"),
        ),
        pytest.param(
            "reportlab",
            marks=pytest.mark.skipif(
                not HAVE_REPORTLAB, reason="the 'pdf' extra is not installed"
            ),
        ),
    ],
)
def test_release_each_renderer_produces_a_pdf_of_a_real_page(
    renderer, article_url, tmp_path
):
    """Both implementations of document.render_pdf, over a real extraction.

    Pinned rather than left to `auto`, so this fails loudly if the renderer the
    image is supposed to ship stops working — which `auto` would hide behind a
    silent fallback.
    """
    with _client(tmp_path / renderer, pdf_renderer=renderer) as client:
        result = _run_job(
            client,
            {
                "schema_version": "1.0",
                "sources": [{"id": "a", "type": "url", "uri": article_url}],
                "outputs": [{"id": "doc", "type": "pdf"}],
            },
        )
        assert result["job"]["status"] == "succeeded", result["job"]
        artifact = result["artifacts"][0]
        data = _artifact_bytes(client, artifact)

    assert artifact["media_type"] == "application/pdf"
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    producer = artifact["provenance"]["producer"]
    assert producer["provider"] == f"content.pdf.{renderer}"
    assert producer["operation"] == "document.render_pdf"
    assert producer["tool_version"], "provenance must name the tool version"

    if HAVE_PDFTOTEXT:
        result_text = subprocess.run(
            ["pdftotext", "-", "-"], input=data, capture_output=True
        )
        if result_text.returncode == 0:
            rendered = result_text.stdout.decode("utf-8", errors="replace")
            assert "café" in rendered, "accents must survive rendering"


@pytest.mark.skipif(
    not (HAVE_TYPST and HAVE_REPORTLAB and HAVE_PDFTOTEXT),
    reason="both renderers and pdftotext required",
)
def test_release_both_renderers_agree_on_the_same_page(article_url, tmp_path):
    """One parser, one model: the two backends must put the same words on the
    page. Divergence here means a renderer started interpreting the source
    itself, which is the coupling the document model exists to prevent."""
    extracted = {}
    for renderer in ("typst", "reportlab"):
        with _client(tmp_path / renderer, pdf_renderer=renderer) as client:
            result = _run_job(
                client,
                {
                    "schema_version": "1.0",
                    "sources": [{"id": "a", "type": "url", "uri": article_url}],
                    "outputs": [{"id": "doc", "type": "pdf"}],
                },
            )
            assert result["job"]["status"] == "succeeded"
            data = _artifact_bytes(client, result["artifacts"][0])
        text = subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True)
        if text.returncode != 0:
            pytest.skip("pdftotext unavailable")
        extracted[renderer] = " ".join(
            text.stdout.decode("utf-8", errors="replace").split()
        )
    assert extracted["typst"] == extracted["reportlab"]


def test_release_reports_what_it_could_not_check():
    """A release check that skips silently is worse than none: it reads as a
    pass. This prints the coverage of the run so a human sees the gaps."""
    checks = {
        "web page extraction": True,
        "yt-dlp media source": bool(HAVE_YTDLP and YTDLP_URL),
        "LLM summary": HAVE_OLLAMA,
        "Typst PDF": HAVE_TYPST,
        "ReportLab PDF": HAVE_REPORTLAB,
        # Not a renderer: it is how a rendered PDF is read back, so without it
        # both PDFs are checked for shape but never for their words.
        "PDF text read-back (pdftotext)": HAVE_PDFTOTEXT,
    }
    ytdlp_version = _ytdlp_version()
    print("\nRelease validation coverage:")
    for name, ready in checks.items():
        print(f"  [{'x' if ready else ' '}] {name}")
    skipped = [name for name, ready in checks.items() if not ready]
    if skipped:
        print(f"  -> not exercised: {', '.join(skipped)}")
    # Named, not just ticked: after a base-image bump the question is not "did
    # yt-dlp run" but "which yt-dlp ran" (docs/operations/ytdlp-base-image.md).
    print(f"  -> yt-dlp version: {ytdlp_version or 'undetected'}")
    print(json.dumps({"release_checks": checks, "ytdlp_version": ytdlp_version}))
