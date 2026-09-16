"""What a refused person is offered — and why the engine does not say it.

`application/quotas.py` says *"this installation allows 60"* on purpose: the
overwhelming majority of installations are one person's homelab, where "the
free tier", "upgrade" and "our server" are nonsense. So the offer is not in the
engine's messages, it is configuration the engine merely *reports* — empty
unless an operator filled it in, and the surfaces show the plain refusal when
it is.

It lives on `/config` rather than in each UI's environment for the reason
ADR 0015/0034/0038 give: a surface is configured about the engine and nothing
else.
"""

from dataclasses import replace

from fastapi.testclient import TestClient

from content.api.app import create_app
from content.config import quota_wall_of, settings_from_env

COMMAND = "docker run -p 8501:8501 ghcr.io/example/hometube:latest"
DOCS = "https://example.test/#installation"


# --- the setting ----------------------------------------------------------------


def test_nothing_is_offered_by_default(settings):
    assert quota_wall_of(settings) == {
        "self_host_command": "",
        "docs_url": "",
        "cta_label": "",
        "cta_url": "",
    }


def test_an_operator_who_configures_it_is_reported_verbatim(settings):
    configured = replace(
        settings,
        quota_wall_self_host_command=COMMAND,
        quota_wall_docs_url=DOCS,
        quota_wall_cta_label="Email me when it's ready",
        quota_wall_cta_url="https://example.test/waiting-list",
    )
    wall = quota_wall_of(configured)
    assert wall["self_host_command"] == COMMAND
    assert wall["cta_label"] == "Email me when it's ready"


def test_the_environment_fills_it_in(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_QUOTA_WALL_SELF_HOST_COMMAND", f"  {COMMAND}  ")
    monkeypatch.setenv("CONTENT_QUOTA_WALL_DOCS_URL", DOCS)
    loaded = settings_from_env()
    # Surrounding whitespace goes; what is inside a multi-line shell snippet is
    # the operator's formatting and stays.
    assert loaded.quota_wall_self_host_command == COMMAND
    assert loaded.quota_wall_docs_url == DOCS
    assert loaded.quota_wall_cta_label == ""


def test_a_multi_line_command_keeps_its_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CONTENT_QUOTA_WALL_SELF_HOST_COMMAND",
        "docker run -p 8501:8501 \\\n  -v $PWD/videos:/data/videos \\\n  image",
    )
    command = settings_from_env().quota_wall_self_host_command
    assert command.count("\n") == 2
    assert "  -v $PWD/videos" in command


# --- what a client reads --------------------------------------------------------


def test_config_reports_an_unconfigured_wall_as_empty(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as http:
        body = http.get("/api/v1/config").json()
    assert body["quota_wall"] == {
        "self_host_command": "",
        "docs_url": "",
        "cta_label": "",
        "cta_url": "",
    }


def test_config_reports_a_configured_wall(settings, store, providers):
    configured = replace(
        settings, quota_wall_self_host_command=COMMAND, quota_wall_docs_url=DOCS
    )
    app = create_app(configured, store=store, providers=providers, start_worker=False)
    with TestClient(app) as http:
        wall = http.get("/api/v1/config").json()["quota_wall"]
    assert wall["self_host_command"] == COMMAND
    assert wall["docs_url"] == DOCS


def test_config_reports_the_retention_window(settings, store, providers):
    """The wall's closing reassurance names a number of days; it has to be the
    instance's own, never a constant compiled into a UI."""
    configured = replace(settings, retention_days=15.0)
    app = create_app(configured, store=store, providers=providers, start_worker=False)
    with TestClient(app) as http:
        assert http.get("/api/v1/config").json()["retention"] == {"days": 15.0}


def test_retention_is_zero_when_nothing_expires(settings, store, providers):
    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as http:
        assert http.get("/api/v1/config").json()["retention"] == {"days": 0.0}
