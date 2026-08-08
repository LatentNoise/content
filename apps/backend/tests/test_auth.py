"""Source authentication (cookies via server-configured credentials).

Covers the pure resolver, planner/analysis feasibility, the yt-dlp cookies
argument builder, resource_key separation, the /config endpoint, and
non-regression when no auth is used. Hermetic — no network, no real yt-dlp.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from content.analysis.service import AnalysisService
from content.api.app import create_app
from content.domain.errors import RequestRejected
from content.planning.auth import resolve_source_credential
from content.planning.planner import build_plan
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.ytdlp import YtDlpProvider, cookies_args, prepare_cookies
from tests.conftest import FakeProvider, make_request, minimal_payload


def with_credentials(settings, **creds):
    return replace(settings, credentials={k: v for k, v in creds.items()})


def url_source(auth=None):
    src = {"id": "main", "type": "url", "uri": "https://example.com/v"}
    if auth is not None:
        src["auth"] = auth
    return src


# --- pure resolver --------------------------------------------------------------


def test_resolver_no_auth():
    request = make_request(minimal_payload())
    cred, issue = resolve_source_credential(request.sources[0], {"youtube"})
    assert cred is None and issue is None


def test_resolver_configured_credential():
    request = make_request(
        minimal_payload(sources=[url_source({"credential_id": "youtube"})])
    )
    cred, issue = resolve_source_credential(request.sources[0], {"youtube"})
    assert cred == "youtube" and issue is None


def test_resolver_unknown_credential():
    request = make_request(
        minimal_payload(sources=[url_source({"credential_id": "nope"})])
    )
    cred, issue = resolve_source_credential(request.sources[0], {"youtube"})
    assert cred is None
    assert issue.code == "credential_not_available"
    assert issue.details["credential_id"] == "nope"


def test_resolver_session_id_rejected():
    request = make_request(minimal_payload(sources=[url_source({"session_id": "s"})]))
    cred, issue = resolve_source_credential(request.sources[0], {"youtube"})
    assert cred is None
    assert issue.code == "auth_method_not_supported"


# --- cookies argument builder ---------------------------------------------------


def test_prepare_cookies_copies_to_writable_workdir(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("# Netscape HTTP Cookie File\n")
    settings = with_credentials(_bare_settings(tmp_path), youtube=src)
    workdir = tmp_path / "work"
    dest = prepare_cookies("youtube", settings, workdir)
    # yt-dlp rewrites the jar on exit, so it must get a writable copy, not the
    # (possibly read-only) configured file.
    assert dest == workdir / "cookies.txt"
    assert dest != src
    assert dest.read_text() == src.read_text()


def test_prepare_cookies_none_or_missing(tmp_path):
    settings = with_credentials(
        _bare_settings(tmp_path), youtube=tmp_path / "absent.txt"
    )
    assert prepare_cookies("youtube", settings, tmp_path / "w") is None  # missing
    assert prepare_cookies(None, settings, tmp_path / "w") is None
    assert prepare_cookies("unknown", settings, tmp_path / "w") is None


def test_cookies_args_from_path(tmp_path):
    assert cookies_args(None) == []
    p = tmp_path / "c.txt"
    assert cookies_args(p) == ["--cookies", str(p)]


def _bare_settings(tmp_path):
    from content.config import ContentSettings

    return ContentSettings(data_dir=tmp_path, db_path=tmp_path / "db.sqlite")


# --- resource_key separation ----------------------------------------------------


def test_resource_key_varies_with_credential(tmp_path):
    provider = YtDlpProvider(binary="yt-dlp-does-not-exist")  # no shelling out
    from content.providers.base import AnalysisContext

    ctx = AnalysisContext(_bare_settings(tmp_path), tmp_path)
    anon = make_request(minimal_payload(sources=[url_source()])).sources[0]
    authed = make_request(
        minimal_payload(sources=[url_source({"credential_id": "youtube"})])
    ).sources[0]
    assert provider.resource_key(anon, ctx) != provider.resource_key(authed, ctx)


# --- planner feasibility --------------------------------------------------------


@pytest.fixture
def plan(store, providers, settings):
    def _plan(payload, creds=None):
        s = with_credentials(settings, **(creds or {}))
        service = AnalysisService(store, providers, s)
        request = make_request(payload)
        analysis = service.analyze_sources(list(request.sources))
        return build_plan(request, analysis, providers, s)

    return _plan


def test_planner_threads_credential_into_step_params(plan, tmp_path):
    payload = minimal_payload(sources=[url_source({"credential_id": "youtube"})])
    result = plan(payload, creds={"youtube": tmp_path / "yt.txt"})
    audio_step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert audio_step.params["credential_id"] == "youtube"


def test_planner_no_auth_has_no_credential(plan):
    result = plan(minimal_payload())
    audio_step = next(s for s in result.steps if s.operation == "media.acquire_audio")
    assert "credential_id" not in audio_step.params


def test_planner_unknown_credential_rejected(plan):
    payload = minimal_payload(sources=[url_source({"credential_id": "nope"})])
    with pytest.raises(RequestRejected) as excinfo:
        plan(payload, creds={"youtube": "/x"})
    assert excinfo.value.result.errors[0].code == "credential_not_available"


def test_planner_session_id_rejected(plan):
    payload = minimal_payload(sources=[url_source({"session_id": "s"})])
    with pytest.raises(RequestRejected) as excinfo:
        plan(payload)
    assert excinfo.value.result.errors[0].code == "auth_method_not_supported"


# --- analysis feasibility -------------------------------------------------------


def test_analysis_rejects_unknown_credential(store, providers, settings):
    s = with_credentials(settings, youtube="/x")
    service = AnalysisService(store, providers, s)
    request = make_request(
        minimal_payload(sources=[url_source({"credential_id": "nope"})])
    )
    with pytest.raises(RequestRejected) as excinfo:
        service.analyze_sources(list(request.sources))
    assert excinfo.value.result.errors[0].code == "credential_not_available"


def test_analysis_rejects_session_id(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    request = make_request(minimal_payload(sources=[url_source({"session_id": "s"})]))
    with pytest.raises(RequestRejected) as excinfo:
        service.analyze_sources(list(request.sources))
    assert excinfo.value.result.errors[0].code == "auth_method_not_supported"


# --- /config endpoint -----------------------------------------------------------


def test_config_exposes_credential_ids_only(settings):
    s = with_credentials(settings, youtube="/secret/path/yt.txt", vimeo="/secret/v.txt")
    app = create_app(
        s,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/config").json()
    assert body["credentials"] == ["vimeo", "youtube"]  # sorted ids
    assert "/secret" not in str(body)  # never leak paths
    # ADR 0018: clients can see whether default delivery is on before submit.
    assert body["delivery"] == {"by_default": False}


def test_config_empty_when_no_credentials(settings):
    app = create_app(
        settings,
        providers=ProviderRegistry(
            [FakeProvider()], processors=[TranscriptProcessor()]
        ),
        start_worker=False,
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/config").json()
        assert body["credentials"] == []
        assert body["language"] == {
            "primary": "",
            "secondaries": [],
            "vo_first": True,
            "primary_include_subtitles": True,
        }
