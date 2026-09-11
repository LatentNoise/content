"""Running the API and the worker as separate deployments.

The same image and the same code do both jobs: what separates them is one
environment variable. This guards the wiring, because the day the API stops
answering during a transcode is the day someone flips this by hand.
"""

import dataclasses

from fastapi.testclient import TestClient

from content.api import app as app_module
from content.api.app import create_app
from content.config import describe_environment, settings_from_env


def test_the_worker_runs_by_default(monkeypatch):
    monkeypatch.delenv("CONTENT_WORKER_ENABLED", raising=False)
    assert settings_from_env().worker_enabled is True


def test_the_environment_can_turn_the_worker_off(monkeypatch):
    monkeypatch.setenv("CONTENT_WORKER_ENABLED", "false")
    assert settings_from_env().worker_enabled is False
    monkeypatch.setenv("CONTENT_WORKER_ENABLED", "true")
    assert settings_from_env().worker_enabled is True


def _queue_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    class Recording(app_module.JobQueue):
        async def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

    monkeypatch.setattr(app_module, "JobQueue", Recording)
    return calls


def test_an_api_only_process_never_starts_the_queue(monkeypatch, settings):
    calls = _queue_calls(monkeypatch)
    api_only = dataclasses.replace(settings, worker_enabled=False)
    with TestClient(create_app(api_only)) as client:
        assert client.get("/api/v1/health").status_code == 200
    assert calls == []


def test_a_worker_process_starts_and_stops_the_queue(monkeypatch, settings):
    calls = _queue_calls(monkeypatch)
    working = dataclasses.replace(settings, worker_enabled=True)
    with TestClient(create_app(working)):
        pass
    assert calls == ["start", "stop"]


def test_an_explicit_argument_still_wins_over_the_configuration(monkeypatch, settings):
    calls = _queue_calls(monkeypatch)
    working = dataclasses.replace(settings, worker_enabled=True)
    with TestClient(create_app(working, start_worker=False)):
        pass
    assert calls == []


def test_the_setting_is_reported_and_is_not_a_secret(settings):
    rows = {row["name"]: row for row in describe_environment(settings, environ={})}
    row = rows["CONTENT_WORKER_ENABLED"]
    assert row["secret"] is False
    assert row["value"] in {"true", "false"}
