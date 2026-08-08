"""Advanced yt-dlp extra arguments (04): a documented, guarded escape hatch.

Server-trusted args come from CONTENT_YTDLP_EXTRA_ARGS; per-request args ride on
a source's provider_args and are filtered against unsafe flags.
"""

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.domain.request import UrlSource
from content.planning.planner import build_plan
from content.providers.ytdlp import extra_args
from tests.conftest import make_request, minimal_payload

# --- contract guard ------------------------------------------------------------


def test_provider_args_accepts_safe_flags():
    src = UrlSource(
        id="s", type="url", uri="https://x", provider_args=["--proxy", "http://p"]
    )
    assert src.provider_args == ["--proxy", "http://p"]


@pytest.mark.parametrize(
    "bad",
    [
        ["--exec", "rm -rf /"],
        ["-o", "/etc/passwd"],
        ["--output=/tmp/x"],
        ["--cookies", "/etc/shadow"],
        ["--config-location", "/x"],
        ["--load-info-json", "x.json"],
    ],
)
def test_provider_args_rejects_unsafe_flags(bad):
    with pytest.raises(ValidationError):
        UrlSource(id="s", type="url", uri="https://x", provider_args=bad)


def test_provider_args_blank_tokens_dropped():
    src = UrlSource(
        id="s",
        type="url",
        uri="https://x",
        provider_args=["  ", "--geo-verification-proxy", ""],
    )
    assert src.provider_args == ["--geo-verification-proxy"]


# --- provider helper -----------------------------------------------------------


class _Settings:
    ytdlp_extra_args = ("--proxy", "http://server")


def test_extra_args_merges_server_then_source():
    args = extra_args(_Settings(), {"provider_args": ["--limit-rate", "1M"]})
    assert args == ["--proxy", "http://server", "--limit-rate", "1M"]


def test_extra_args_empty_without_config():
    class Empty:
        ytdlp_extra_args = ()

    assert extra_args(Empty(), {}) == []


# --- planner threading ---------------------------------------------------------


def test_provider_args_threaded_into_acquisition_params(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    payload = minimal_payload(
        sources=[
            {
                "id": "main",
                "type": "url",
                "uri": "https://example.com/v",
                "provider_args": ["--limit-rate", "2M"],
            }
        ],
        outputs=[{"id": "audio_main", "type": "audio"}],
    )
    request = make_request(payload)
    plan = build_plan(
        request, service.analyze_sources(list(request.sources)), providers, settings
    )
    assert plan.steps[0].params["provider_args"] == ["--limit-rate", "2M"]


# --- config --------------------------------------------------------------------


def test_config_parses_extra_args_with_shlex(monkeypatch):
    from content.config import settings_from_env

    monkeypatch.setenv(
        "CONTENT_YTDLP_EXTRA_ARGS", '--proxy "http://p:8080" --limit-rate 1M'
    )
    settings = settings_from_env()
    assert settings.ytdlp_extra_args == (
        "--proxy",
        "http://p:8080",
        "--limit-rate",
        "1M",
    )
