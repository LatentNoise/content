"""CLI shortcuts normalize to a valid canonical GenerationRequest, and command
dispatch calls the API client correctly (no engine logic in the CLI)."""

import json

import httpx
from content.domain.request import GenerationRequest  # backend contract (installed)
from content_cli import cli
from content_cli.builders import audio_request, video_request
from content_cli.cli import run
from content_sdk import ContentClient

# --- builders produce valid contract -------------------------------------------


def test_video_request_is_valid_and_maps_options():
    req = video_request(
        "https://x", height=1080, codec="av1", container="mkv", subtitles="en,fr"
    )
    GenerationRequest.model_validate(req)  # must not raise
    out = req["outputs"][0]
    assert out["type"] == "video"
    assert out["options"]["selection"]["max_height"] == 1080
    assert out["options"]["selection"]["video_codec"] == {
        "mode": "prefer",
        "value": "av1",
    }
    assert out["options"]["container"] == "mkv"
    assert out["options"]["processing"]["embed_subtitles"] == ["en", "fr"]


def test_video_playlist_sets_each_item_scope():
    req = video_request("https://x", playlist=True)
    GenerationRequest.model_validate(req)
    assert req["outputs"][0]["scope"] == "each_item"


def test_audio_request_format_delivery_and_sponsorblock():
    req = audio_request(
        "https://x", fmt="opus", folder="music", name="track", sponsorblock="default"
    )
    GenerationRequest.model_validate(req)
    out = req["outputs"][0]
    assert out["options"]["format"] == "opus"
    assert out["options"]["sponsorblock"]["remove"]
    assert out["delivery"] == {"folder": "music", "filename": "track"}


def test_credential_becomes_source_auth():
    req = audio_request("https://x", credential="youtube")
    assert req["sources"][0]["auth"] == {"credential_id": "youtube"}


# --- command dispatch (real SDK client over MockTransport, no network) ----------


class _Recorder:
    """A MockTransport handler that records requests and returns canned bodies."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/v1/analyses":
            return httpx.Response(
                200,
                json={
                    "analysis_id": "ana_x",
                    "created_at": "t",
                    "sources": [
                        {
                            "source_id": "main",
                            "resource": {"resource_type": "video", "title": "T"},
                        }
                    ],
                },
            )
        if path == "/api/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "analysis_id": "ana_x",
                    "sources": [
                        {
                            "source_id": "main",
                            "capabilities": [
                                {"id": "audio.download", "status": "available"}
                            ],
                        }
                    ],
                },
            )
        if path == "/api/v1/jobs":
            return httpx.Response(201, json={"job_id": "job_x", "status": "queued"})
        return httpx.Response(400, json={"detail": "unhandled"})


def _client(rec: _Recorder) -> ContentClient:
    http = httpx.Client(transport=httpx.MockTransport(rec.handler))
    return ContentClient("http://testserver", http_client=http)


def test_video_command_builds_and_submits():
    rec = _Recorder()
    rc = run(["video", "https://x", "--container", "mp4"], _client(rec))
    assert rc == 0
    submitted = next(b for m, p, b in rec.requests if p == "/api/v1/jobs")
    assert submitted["outputs"][0]["options"]["container"] == "mp4"


def test_analyze_command_calls_client():
    rec = _Recorder()
    rc = run(["analyze", "https://x", "--credential", "youtube"], _client(rec))
    assert rc == 0
    analyzed = next(b for m, p, b in rec.requests if p == "/api/v1/analyses")
    assert analyzed["sources"][0]["uri"] == "https://x"
    assert analyzed["sources"][0]["auth"] == {"credential_id": "youtube"}


# --- error paths: the half that had no coverage at all ---------------------------


class _Boom:
    """A client that fails the way the SDK really fails."""

    def __init__(self, error):
        self._error = error

    def health(self):
        raise self._error

    def __getattr__(self, _name):
        def fail(*_args, **_kwargs):
            raise self._error

        return fail


def test_an_unreachable_engine_says_so_instead_of_a_traceback(capsys, monkeypatch):
    """The most common failure of all: the engine is not running.

    `main` caught `APIError` only, but a refused connection raises
    `TransportError`, so this printed sixty lines of httpx/httpcore stack and
    exited 1. Both derive from `ContentError`.
    """
    from content_sdk.errors import TransportError

    monkeypatch.setattr(
        cli, "ContentClient", lambda _url: _Boom(TransportError("[Errno 61] refused"))
    )
    code = cli.main(["health"])
    err = capsys.readouterr().err
    assert code == 2
    assert "Traceback" not in err
    assert "cannot reach the engine" in err


def test_a_rejection_prints_one_readable_line_per_problem(capsys, monkeypatch):
    """The contract has exactly one error shape since the 422s were unified, so
    there is no reason to print a raw Python dict at somebody."""
    from content_sdk.errors import ValidationError

    body = {
        "detail": {
            "valid": False,
            "phase": "schema",
            "errors": [
                {
                    "code": "schema_violation",
                    "path": "sources",
                    "message": "Input should be a valid list",
                }
            ],
        }
    }
    monkeypatch.setattr(
        cli, "ContentClient", lambda _url: _Boom(ValidationError(422, body))
    )
    code = cli.main(["health"])
    err = capsys.readouterr().err
    assert code == 2
    assert "Input should be a valid list" in err
    assert "at sources" in err
    assert "schema_violation" in err
    assert "{'detail'" not in err, "the raw dict must not be shown"


def test_api_url_without_a_value_is_a_message_not_an_indexerror(capsys):
    code = cli.main(["--api-url"])
    assert code == 2
    assert "--api-url needs a value" in capsys.readouterr().err


# --- the engine the command names is the engine it reaches ----------------------


class _Quiet:
    """A client that answers everything with nothing, so only the URL matters."""

    def health(self):
        return {}

    def __getattr__(self, _name):
        return lambda *_a, **_k: {}


def _urls_reached(monkeypatch) -> list:
    """Record the base URL every `main()` call builds its client with."""
    reached: list = []

    def build(url):
        reached.append(url)
        return _Quiet()

    monkeypatch.setattr(cli, "ContentClient", build)
    return reached


def test_api_url_is_honoured_in_both_spellings(monkeypatch, capsys):
    """`--api-url=URL` parsed cleanly and was then thrown away.

    The flag was registered on the parser and read nowhere — only the
    hand-peeled `--api-url URL` form reached the client — so the `=` spelling
    every other flag accepts left the CLI talking to `CONTENT_API_URL`, or to
    localhost, while reporting success. Targeting the wrong installation
    silently is the one failure a flag that names the engine must not have.
    """
    reached = _urls_reached(monkeypatch)
    assert cli.main(["--api-url=http://engine:8010", "health"]) == 0
    assert cli.main(["--api-url", "http://engine:8010", "health"]) == 0
    capsys.readouterr()
    assert reached == ["http://engine:8010", "http://engine:8010"]


def test_api_url_is_peeled_after_the_subcommand_too(monkeypatch, capsys):
    """Wherever it sits, it is a global flag: the client exists before the
    command does, so argparse never gets to see it."""
    reached = _urls_reached(monkeypatch)
    assert cli.main(["health", "--api-url=http://engine:8010"]) == 0
    assert cli.main(["health", "--api-url", "http://engine:8010"]) == 0
    capsys.readouterr()
    assert reached == ["http://engine:8010", "http://engine:8010"]


def test_an_empty_api_url_is_refused_rather_than_ignored(monkeypatch, capsys):
    """`--api-url="$ENGINE"` with `ENGINE` unset is the likely way to get one.

    The SDK reads an empty base URL as "not given" and falls back to the
    environment, so accepting it would reach an engine the command did not
    name. Both spellings answer the same way as a missing value.
    """
    reached = _urls_reached(monkeypatch)
    for argv in (["--api-url=", "health"], ["--api-url", "  ", "health"]):
        assert cli.main(argv) == 2
        assert "--api-url needs a value" in capsys.readouterr().err
    assert reached == [], "no client may be built against the wrong engine"


def test_no_api_url_leaves_the_sdk_to_resolve_it(monkeypatch, capsys):
    """Absent is not empty: the SDK's own CONTENT_API_URL/default resolution
    stays the one place that decides, and the CLI passes None through."""
    reached = _urls_reached(monkeypatch)
    assert cli.main(["health"]) == 0
    capsys.readouterr()
    assert reached == [None]


# --- the exit codes of a watched job (ADR 0021) ---------------------------------


class _Watched:
    """A job that is already terminal, watched through the polling fallback."""

    def __init__(self, status: str):
        self._status = status

    def stream_events(self, _job_id, **_kw):
        raise RuntimeError("no stream here")

    def events(self, _job_id, **_kw):
        return []

    def get_job(self, _job_id):
        return type("Job", (), {"status": self._status})()


def test_a_watched_job_exits_with_its_outcome(monkeypatch, capsys):
    """The three codes the README publishes, which nothing exercised.

    `2` is deliberately distinct from `1`: a playlist that yielded five videos
    of six is not a success, and a script that reads it as one moves on with
    missing files.
    """
    for status, expected in (
        ("succeeded", 0),
        ("partially_succeeded", 2),
        ("failed", 1),
        ("cancelled", 1),
    ):
        monkeypatch.setattr(cli, "ContentClient", lambda _url, s=status: _Watched(s))
        assert cli.main(["watch", "job_x"]) == expected, status
        out = capsys.readouterr()
        assert f"→ {status}" in out.out
        # A refused stream is said out loud rather than silently degraded.
        assert "event stream unavailable" in out.err
