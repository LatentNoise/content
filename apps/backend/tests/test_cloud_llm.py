"""Cloud LLM runners: never deliver an answer that is not the whole answer.

The failure this covers is quiet by nature. A truncated summary reads perfectly
well right up to where it stops, and a refusal arrives as HTTP 200 — both used
to be written out as artifacts and reported as success. The tests drive the real
`_call` path with a fake HTTP layer in both vendors' response shapes, because
the bug lived in the gap between "the request succeeded" and "the answer is
complete".
"""

import json
import urllib.error

import pytest

from content.providers.base import StepExecutionError
from content.providers.cloud_llm import CloudSummarizer


def _anthropic(text="a summary", stop_reason="end_turn"):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "model": "claude-sonnet-5",
    }


def _openai(text="a summary", finish_reason="stop", refusal=None):
    message = {"content": text}
    if refusal is not None:
        message = {"content": None, "refusal": refusal}
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


@pytest.fixture
def responder(monkeypatch):
    """Replace the HTTP layer, keeping every layer above it real."""
    calls: list[dict] = []

    def _install(*responses):
        queue = list(responses)

        class _Resp:
            def __init__(self, payload):
                self._payload = json.dumps(payload).encode()

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append(json.loads(request.data.decode()))
            nonlocal queue
            outcome = queue.pop(0) if queue else queue
            if isinstance(outcome, Exception):
                raise outcome
            return _Resp(outcome)

        monkeypatch.setattr(
            "content.providers.cloud_llm.urllib.request.urlopen", fake_urlopen
        )
        monkeypatch.setattr("content.providers.cloud_llm.time.sleep", lambda _s: None)
        return calls

    return _install


# --- the answer is not the whole answer -----------------------------------------


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("anthropic", _anthropic("half a summ", stop_reason="max_tokens")),
        ("openai", _openai("half a summ", finish_reason="length")),
    ],
)
def test_a_truncated_answer_fails_instead_of_becoming_an_artifact(
    responder, provider, payload
):
    """The highest-value fix in the slice: a response that hit the output cap
    was returned as if it were complete."""
    responder(payload)
    runner = CloudSummarizer(provider, "key", "model")
    with pytest.raises(StepExecutionError) as exc:
        runner._call("prompt", timeout=10)
    assert exc.value.code == "output_truncated"
    assert "incomplete" in str(exc.value)
    assert exc.value.details["stop_reason"] in ("max_tokens", "length")


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("anthropic", _anthropic("", stop_reason="refusal")),
        ("openai", _openai(None, finish_reason="stop", refusal="I can't help")),
        ("openai", _openai("", finish_reason="content_filter")),
    ],
)
def test_a_refusal_fails_instead_of_becoming_an_empty_artifact(
    responder, provider, payload
):
    """HTTP 200 with no usable content is not success. It used to be written out
    as an empty summary."""
    responder(payload)
    runner = CloudSummarizer(provider, "key", "model")
    with pytest.raises(StepExecutionError) as exc:
        runner._call("prompt", timeout=10)
    assert exc.value.code == "provider_refused"


@pytest.mark.parametrize(
    "provider,payload",
    [("anthropic", _anthropic("all of it")), ("openai", _openai("all of it"))],
)
def test_a_complete_answer_passes_through(responder, provider, payload):
    responder(payload)
    assert CloudSummarizer(provider, "key", "model")._call("prompt", 10) == "all of it"


# --- the output ceiling ---------------------------------------------------------


def test_max_tokens_is_derived_from_the_operation_and_the_input():
    """One constant could only be wrong in both directions: 2000 truncates a
    long translation and is wasteful on a one-line summary."""
    runner = CloudSummarizer("anthropic", "key", "model")
    short, long = "x" * 400, "x" * 400_000

    assert runner.max_tokens_for("text.summarize", short) == 512  # the floor
    assert runner.max_tokens_for("text.summarize", long) == 8192  # the ceiling
    # A translation tracks its input; a summary is a fraction of it.
    assert runner.max_tokens_for("text.translate", long) > runner.max_tokens_for(
        "text.summarize", long
    )
    # Chapters are bounded by how many a video can sensibly have, not its length.
    assert runner.max_tokens_for("chapters.derive", long) < runner.max_tokens_for(
        "text.summarize", long
    )


def test_the_ceiling_is_sent_on_both_vendors(responder):
    """OpenAI previously had no cap at all — the opposite failure: a runaway
    generation billed and timed out rather than truncated."""
    for provider in ("anthropic", "openai"):
        calls = responder(_anthropic() if provider == "anthropic" else _openai())
        calls.clear()
        CloudSummarizer(provider, "key", "model")._call("x" * 40_000, 10)
        assert "max_tokens" in calls[0], provider
        assert calls[0]["max_tokens"] > 512


def test_an_unknown_operation_falls_back_to_the_summary_budget():
    runner = CloudSummarizer("anthropic", "key", "model")
    assert runner.max_tokens_for("something.new", "x" * 4000) == runner.max_tokens_for(
        "text.summarize", "x" * 4000
    )


# --- retries --------------------------------------------------------------------


def _http_error(code):
    return urllib.error.HTTPError("u", code, "e", {}, None)


def test_a_rate_limit_is_retried(responder):
    calls = responder(_http_error(429), _anthropic("recovered"))
    assert CloudSummarizer("anthropic", "k", "m")._call("p", 10) == "recovered"
    assert len(calls) == 2


def test_a_transport_blip_is_retried(responder):
    calls = responder(urllib.error.URLError("connection reset"), _anthropic("ok"))
    assert CloudSummarizer("anthropic", "k", "m")._call("p", 10) == "ok"
    assert len(calls) == 2


def test_a_bad_request_is_not_retried(responder):
    """Repeating a request we got wrong fails identically and costs latency —
    a 400 is our bug, not a blip."""
    calls = responder(_http_error(400), _anthropic("never reached"))
    with pytest.raises(StepExecutionError) as exc:
        CloudSummarizer("anthropic", "k", "m")._call("p", 10)
    assert exc.value.code == "provider_error"
    assert len(calls) == 1


def test_retries_are_bounded(responder):
    calls = responder(_http_error(503), _http_error(503), _http_error(503))
    with pytest.raises(StepExecutionError):
        CloudSummarizer("anthropic", "k", "m")._call("p", 10)
    assert len(calls) == 3


# --- secrets --------------------------------------------------------------------


def test_the_key_never_appears_in_an_error_message(responder):
    """INV-009. Provider detail is preserved, but an error that quoted the
    request would put the key into logs, events and snapshots."""
    responder(_http_error(401))
    runner = CloudSummarizer("anthropic", "sk-super-secret", "model")
    with pytest.raises(StepExecutionError) as exc:
        runner._call("prompt", 10)
    assert "sk-super-secret" not in str(exc.value)


def test_the_key_is_sent_as_a_header_not_in_the_body(responder):
    calls = responder(_anthropic())
    CloudSummarizer("anthropic", "sk-secret", "model")._call("prompt", 10)
    assert "sk-secret" not in json.dumps(calls[0])
