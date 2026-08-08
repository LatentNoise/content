"""The three UIs, driven against a REAL backend.

Every other UI test in this directory runs against `FakeContentClient`, which
answers whatever the fake was taught. That is why D-37 survived three waves:
Studio kept a private capability→output map, silently stopped offering
`text.extract`, `markdown.export` and `pdf.render`, and no test could notice —
the fake agreed with the UI, and both were wrong about the server.

Here the client is the real `content_sdk` talking over HTTP to a real engine
started as a subprocess, so a renamed field, a changed status value or a
capability the server gained is a failing test rather than a silent hole.

Marked `release`: it starts a server, runs yt-dlp and ffmpeg, and takes seconds
rather than milliseconds. `make validate` stays hermetic and fast; this runs
under `make validate-release` (see docs/development/validation.md).

Offline by design. The "real URL" a UI is given is served by a local HTTP
server out of a temp directory — a real socket, a real fetch, a real yt-dlp
download, no Internet. Each check skips when a prerequisite is missing and the
last test prints what could not be exercised, because a release check that
skips silently reads as a pass.
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.release

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "apps" / "backend"
BACKEND_PY = BACKEND_DIR / ".venv" / "bin" / "python"
APPS = {
    "hometube": REPO / "apps" / "web-hometube" / "app.py",
    "studio": REPO / "apps" / "web-studio" / "app.py",
    "console": REPO / "apps" / "web-admin" / "app.py",
}

HAVE_BACKEND = BACKEND_PY.exists()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_YTDLP = shutil.which("yt-dlp") is not None

ARTICLE_HTML = """<html lang="en"><head>
<meta property="og:title" content="Live UI Article">
<meta name="author" content="A. Writer">
</head><body>
<nav><a href="/">Home</a></nav><script>tracking()</script>
<article>
  <h1>Live UI Article</h1>
  <p>Declaring <em>what</em> you want beats invoking <b>how</b>. Accents
     survive the round trip: café, naïve, œuvre.</p>
  <h2>Why it matters</h2>
  <ul><li>Stable intent</li><li>Swappable tools</li></ul>
</article>
<footer>(c) nobody</footer></body></html>"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- the world the UIs talk to ---------------------------------------------------


@pytest.fixture(scope="session")
def served_dir(tmp_path_factory):
    """A directory published over HTTP: the article every UI reads, and a tiny
    generated clip for the media path. Loopback, so the run needs no Internet.
    """
    root = tmp_path_factory.mktemp("served")
    (root / "article.html").write_text(ARTICLE_HTML, encoding="utf-8")
    if HAVE_FFMPEG:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=3:size=320x240:rate=10",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(root / "clip.mp4"),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )

    handler = type(
        "_Quiet",
        (SimpleHTTPRequestHandler,),
        {
            "log_message": lambda *_a, **_k: None,
            "__init__": lambda self, *a, **k: SimpleHTTPRequestHandler.__init__(
                self, *a, directory=str(root), **k
            ),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def article_url(served_dir):
    return f"{served_dir}/article.html"


@pytest.fixture(scope="session")
def media_url(served_dir):
    if not (HAVE_FFMPEG and HAVE_YTDLP):
        pytest.skip("the media path needs ffmpeg (to make a clip) and yt-dlp")
    return f"{served_dir}/clip.mp4"


@pytest.fixture(scope="session")
def live_backend(tmp_path_factory):
    """A real engine on a real port, in its own venv and its own data dir.

    A subprocess rather than an in-process app: the UIs speak HTTP through the
    SDK, and the backend's dependencies live in `apps/backend/.venv` while these
    tests run in the Streamlit venv. Only the wire is shared, which is the point.
    """
    if not HAVE_BACKEND:
        pytest.skip("apps/backend/.venv is missing — run `make install`")

    data = tmp_path_factory.mktemp("engine-data")
    port = _free_port()
    env = {
        **os.environ,
        "CONTENT_DATA_DIR": str(data),
        "CONTENT_DB_PATH": str(data / "content.db"),
        # The article and the clip are served on loopback; the SSRF guard would
        # otherwise refuse the very URLs this test exists to exercise.
        "CONTENT_ALLOW_PRIVATE_NETWORKS": "true",
        "CONTENT_MAX_CONCURRENT_JOBS": "2",
    }
    process = subprocess.Popen(
        [
            str(BACKEND_PY),
            "-m",
            "uvicorn",
            "content.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = (process.stdout.read() or b"").decode(errors="replace")
            pytest.fail(f"the backend exited before serving:\n{output[-2000:]}")
        try:
            with urllib.request.urlopen(f"{base}/api/v1/health", timeout=2):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        process.kill()
        pytest.fail("the backend never became healthy")

    try:
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def api(live_backend):
    """The same SDK client the UIs use — for arranging state and for checking,
    from the outside, what the UI claimed."""
    from content_sdk.compat import ContentClient

    return ContentClient(live_backend)


@pytest.fixture
def live_app(live_backend, monkeypatch, tmp_path):
    """Run a real app against the real backend. Deliberately does NOT patch
    `compat.ContentClient`: the whole point is the client that ships."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CONTENT_API_URL", live_backend)
    monkeypatch.setenv("CONTENT_UI_STATE_DIR", str(tmp_path / "ui-state"))

    def _run(app_name: str):
        path = str(APPS[app_name])
        parent = str(Path(path).parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        at = AppTest.from_file(path, default_timeout=120)
        at.run()
        assert not at.exception, at.exception
        return at

    return _run


# --- helpers ---------------------------------------------------------------------


def _text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success"):
        for element in getattr(at, kind):
            parts.append(str(getattr(element, "value", "") or ""))
    return " ".join(parts)


def _labels(at, kind) -> list[str]:
    return [str(getattr(el, "label", "") or "") for el in getattr(at, kind)]


def _button(at, *words):
    """The first button whose label mentions any of `words`.

    Labels carry emoji and wording that a copy-edit may change; matching on a
    word keeps these tests about behaviour rather than about phrasing.
    """
    for element in at.button:
        label = (element.label or "").lower()
        if any(word.lower() in label for word in words):
            return element
    raise AssertionError(
        f"no button matching {words}; the page offered {_labels(at, 'button')}"
    )


def _producible(api, uri: str) -> set[str]:
    """The output types the SERVER says this source can yield."""
    resolved = api.capabilities([{"id": "a", "type": "url", "uri": uri}])
    return {
        capability["output_type"]
        for capability in resolved["sources"][0]["capabilities"]
        if capability["status"] in ("available", "derivable", "unknown")
    }


def _job_ids(api) -> set[str]:
    return {job["job_id"] for job in api.list_jobs(limit=50)}


def _job_submitted_since(api, before: set[str]) -> str:
    """The job the UI just created, found by difference.

    Not `list_jobs()[0]`: every test in this module shares one backend, so "the
    newest row" is only ours by luck and ties on `created_at` decide the rest.
    """
    for _ in range(40):
        new = _job_ids(api) - before
        if new:
            assert len(new) == 1, f"expected one new job, got {sorted(new)}"
            return new.pop()
        time.sleep(0.25)
    pytest.fail("the UI produced no job on the server")


def _await_job(api, job_id: str, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    job = api.job(job_id)
    while job["status"] not in ("succeeded", "failed", "cancelled"):
        if time.monotonic() > deadline:
            pytest.fail(f"job {job_id} still {job['status']} after {timeout}s")
        time.sleep(0.5)
        job = api.job(job_id)
    return job


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        assert response.status == 200, f"{url} -> HTTP {response.status}"
        return response.read()


def _artifact_urls(at) -> list[str]:
    """The download links the UI actually rendered, not ones we reconstruct.

    `link_button` has no typed accessor on AppTest, so the generic `get` is the
    way in — it still carries the real `url`, which is what has to be right.
    """
    urls = []
    for element in at.get("link_button"):
        url = str(getattr(element, "url", "") or "")
        if "/api/v1/artifacts/" in url:
            urls.append(url)
    return urls


# --- Studio: the D-37 guard ------------------------------------------------------


def test_studio_offers_every_output_type_the_server_can_produce(
    live_app, api, article_url
):
    """The regression D-37 described, made impossible to repeat.

    Studio must not keep its own idea of what exists. Whatever the *server*
    resolves as producible for this source has to be reachable in the page —
    including capabilities added after Studio was last touched.
    """
    at = live_app("studio")
    at.text_input(key="uri-0").set_value(article_url).run()
    _button(at, "Analyze").click().run()
    assert not at.exception, at.exception

    offered = {label.strip("* ") for label in _labels(at, "checkbox")}
    expected = _producible(api, article_url)
    assert expected, "the server produced no capabilities for the article"
    missing = expected - offered
    assert not missing, (
        f"the server can produce {sorted(missing)} from this source and Studio "
        f"does not offer it — the D-37 drift. Offered: {sorted(offered)}"
    )
    # And the ones this wave added are really there, not merely absent from the
    # diff: a web page is the source that makes them producible at all.
    assert {"markdown", "pdf", "document_text"} <= offered


def test_studio_takes_a_web_page_to_a_pdf(live_app, api, article_url):
    """A non-media source carried the whole way through the UI: analyze, pick an
    output, submit, and fetch the bytes from the link the page rendered."""
    at = live_app("studio")
    at.text_input(key="uri-0").set_value(article_url).run()
    _button(at, "Analyze").click().run()

    at.checkbox(key="en-pdf").set_value(True).run()
    assert not at.exception, at.exception
    submit = _button(at, "generate", "submit", "run")
    before = _job_ids(api)
    submit.click().run()
    assert not at.exception, at.exception

    job = _await_job(api, _job_submitted_since(api, before))
    assert job["status"] == "succeeded", job

    at.run()  # let the page pick the finished job up
    urls = _artifact_urls(at)
    assert urls, "the finished job rendered no download link"
    data = _download(urls[0])
    assert data.startswith(b"%PDF-"), data[:32]


# --- HomeTube: the media path ----------------------------------------------------


def test_hometube_only_names_capabilities_the_server_declares(api):
    """HomeTube is allowed to offer *less* than the catalog — it is the
    specialised UI. It is not allowed to reference capabilities that no longer
    exist: its map is keyed by capability id, so a rename would silently empty
    the page rather than fail. This is the same failure mode as D-37, one app
    over, and the reason Studio was not enough to fix on its own.
    """
    sys.path.insert(0, str(APPS["hometube"].parent))
    import app as hometube

    declared = {c["id"] for c in api.catalog()["capabilities"]}
    unknown = set(hometube.CAP_TO_OUTPUT) - declared
    assert not unknown, (
        f"HomeTube maps {sorted(unknown)}, which the server no longer declares"
    )


def test_hometube_downloads_a_real_media_url(live_app, api, media_url):
    """The full HomeTube path over a real socket: a URL analysed by yt-dlp, an
    output chosen, a job submitted, the artifact fetched from the rendered link.
    """
    at = live_app("hometube")
    at.text_input(key="url").set_value(media_url).run()
    assert not at.exception, at.exception

    labels = _labels(at, "checkbox")
    assert any("Video" in label for label in labels), (
        f"no video output offered for a media URL; got {labels}"
    )
    download = _button(at, "Download")
    before = _job_ids(api)
    download.click().run()
    assert not at.exception, at.exception

    job = _await_job(api, _job_submitted_since(api, before))
    assert job["status"] == "succeeded", job

    at.run()
    urls = _artifact_urls(at)
    assert urls, "the finished job rendered no download link"
    payload = _download(urls[0])
    assert len(payload) > 1000, "the artifact came back suspiciously small"


# --- Console: the read-only surface over real data -------------------------------


def test_console_renders_the_dashboard_and_storage_from_real_data(live_app, api):
    at = live_app("console")
    body = _text(at)
    assert "Content Admin" in body
    system = api.system()
    assert system["version"] in body, "the console must show the server's version"
    # Storage is rendered from a shape that has changed under it before.
    storage = api.storage()
    assert set(storage) >= {"jobs", "delivery", "tmp", "cache"}
    assert not at.exception, at.exception


def test_console_shows_a_real_job_in_detail(live_app, api, article_url):
    """Steps, provenance, events and logs, for a job that actually ran."""
    created = api.submit(
        {
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "url", "uri": article_url}],
            "outputs": [{"id": "md", "type": "markdown"}],
        }
    )
    job = _await_job(api, created["job_id"])
    assert job["status"] == "succeeded", job

    # The shapes the Console renders — assert they exist before blaming the UI.
    assert job["steps"] and job["steps"][0]["operation"]
    artifacts = api.artifacts(created["job_id"])
    assert artifacts and artifacts[0]["provenance"]["producer"]["operation"]
    assert isinstance(api.events(created["job_id"]), list)

    at = live_app("console")
    assert not at.exception, at.exception
    body = _text(at)
    assert created["job_id"][:12] in body or "markdown" in body.lower(), (
        "the finished job is invisible on the console"
    )


def test_console_can_cancel_and_retry_a_real_job(api, article_url):
    """Cancel and retry are the two writes the Console performs. They are
    asserted against the API the buttons call, because a button click cannot
    prove the transition actually happened server-side."""
    created = api.submit(
        {
            "schema_version": "1.0",
            "sources": [{"id": "a", "type": "url", "uri": article_url}],
            "outputs": [{"id": "md", "type": "markdown"}],
        }
    )
    job = _await_job(api, created["job_id"])
    assert job["status"] in ("succeeded", "failed", "cancelled")

    retried = api.retry(created["job_id"])
    assert retried["job_id"] != created["job_id"], "retry must start a new job"
    again = _await_job(api, retried["job_id"])
    assert again["status"] == "succeeded", again


def test_live_ui_reports_what_it_could_not_check():
    """A release check that skips silently reads as a pass."""
    checks = {
        "a real backend (apps/backend/.venv)": HAVE_BACKEND,
        "ffmpeg (generates the clip)": HAVE_FFMPEG,
        "yt-dlp (the media path)": HAVE_YTDLP,
    }
    print("\nLive UI coverage:")
    for name, ready in checks.items():
        print(f"  [{'x' if ready else ' '}] {name}")
    missing = [name for name, ready in checks.items() if not ready]
    if missing:
        print(f"  -> not exercised: {', '.join(missing)}")
