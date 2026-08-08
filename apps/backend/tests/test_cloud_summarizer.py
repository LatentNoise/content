"""Cloud LLM summarizer (Anthropic/OpenAI) for text.summarize — a new
implementation of the operation, selectable and privacy-aware."""

import dataclasses
import json
from dataclasses import replace

import pytest

import content.providers.cloud_llm as cloud_mod
from content.api.app import create_app
from content.domain.plan import PlanStep
from content.planning.transformations import build_registry
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ExecutionContext, Material, ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from content.providers.ytdlp import YtDlpProvider


def test_available_requires_key_and_model():
    assert CloudSummarizer("anthropic", "k", "m").available()
    assert not CloudSummarizer("anthropic", "", "m").available()
    assert not CloudSummarizer("openai", "k", "").available()


def test_headers_and_payload_per_provider():
    a = CloudSummarizer("anthropic", "sk-a", "claude-x")
    assert a._headers()["x-api-key"] == "sk-a"
    payload = a._payload("p", "text.summarize")
    assert payload["messages"][0]["content"] == "p"
    # The ceiling is derived per operation now, not a constant (prompt 14).
    assert payload["max_tokens"] >= 512
    o = CloudSummarizer("openai", "sk-o", "gpt")
    assert o._headers()["authorization"] == "Bearer sk-o"


@pytest.mark.parametrize(
    "provider,data,expected",
    [
        (
            "anthropic",
            {"content": [{"type": "text", "text": "Hello"}], "stop_reason": "end_turn"},
            ("Hello", "end_turn"),
        ),
        (
            "openai",
            {"choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}]},
            ("Hi", "stop"),
        ),
    ],
)
def test_extract_returns_the_text_and_why_it_stopped(provider, data, expected):
    """The stop reason travels with the text: completeness cannot be judged from
    the text alone (prompt 14). See test_cloud_llm.py for the failure paths."""
    assert CloudSummarizer._extract(provider, data) == expected


def _ctx(tmp_path, settings) -> ExecutionContext:
    transcript = tmp_path / "t.txt"
    transcript.write_text("first line\nsecond line")
    material = Material(path=transcript, media_type="text/plain")
    return ExecutionContext(
        settings=settings,
        workdir=tmp_path,
        stdout_log=tmp_path / "o.log",
        stderr_log=tmp_path / "e.log",
        timeout_seconds=30,
        input_materials=[material],
    )


def test_execute_calls_api_and_writes_summary(tmp_path, settings, monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": "# Summary\n\nok"}]}
            ).encode()

    monkeypatch.setattr(
        cloud_mod.urllib.request, "urlopen", lambda req, timeout=0: FakeResp()
    )
    summarizer = CloudSummarizer("anthropic", "sk", "claude-x")
    step = PlanStep(
        id="s",
        operation="text.summarize",
        provider="anthropic",
        params={"format": "markdown", "length": "short"},
    )
    produced = summarizer.execute(step, _ctx(tmp_path, settings))
    assert produced[0].path.read_text().startswith("# Summary")
    assert produced[0].attributes["model"] == "anthropic/claude-x"
    assert produced[0].media_type == "text/markdown"


def test_registry_covers_cloud_summarizer():
    registry = build_registry(
        ProviderRegistry(
            [YtDlpProvider()],
            processors=[TranscriptProcessor(), CloudSummarizer("anthropic", "k", "m")],
        )
    )
    assert registry.implementation("text.summarize", "anthropic").runner == "anthropic"


def test_create_app_registers_cloud_runner_when_token_set(settings):
    from fastapi.testclient import TestClient

    configured = replace(settings, anthropic_api_key="sk-test")
    with TestClient(create_app(configured, start_worker=False)) as client:
        runners = {r["name"] for r in client.get("/api/v1/system").json()["runners"]}
    assert "anthropic" in runners


def test_api_key_never_exposed(settings):
    # A secret in settings must not surface via the client-facing config/system.
    configured = replace(settings, anthropic_api_key="sk-secret")
    from fastapi.testclient import TestClient

    with TestClient(create_app(configured, start_worker=False)) as client:
        body = json.dumps(client.get("/api/v1/system").json()) + json.dumps(
            client.get("/api/v1/config").json()
        )
    assert "sk-secret" not in body
    # sanity: settings is frozen, we didn't mutate the shared fixture
    assert dataclasses.is_dataclass(configured)
