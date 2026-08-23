"""A long transcript is summarised from its first half — and now says so.

Measured on the real deployment (Ollama 0.32, gemma3:4b): a 32 400-word
transcript and a 90 000-word one both came back with `prompt_eval_count` of
exactly 16 387 — the daemon's default 16 384-token window plus the chat
scaffolding. The prompt is **cut, not refused**. The job succeeds, the artifact
looks right, and a summary of the first 20 minutes is presented as a summary of
two hours.

Roughly 12 000 words is where that begins. Below it nothing is wrong, which is
why this is a warning on a successful step and not a failure: refusing a job
that would have produced a perfectly good summary is the worse error.

The detection is deliberately one-sided. It compares `prompt_eval_count`
against a *lower bound* on the prompt's tokens (8 characters per token, where
prose runs nearer 4), so it can miss a truncation but can never invent one.
"""

from __future__ import annotations

from content.providers.ollama import _warn_if_truncated


class _Ctx:
    def __init__(self):
        self.warnings = []

    def on_warning(self, code, message, details):
        self.warnings.append((code, message, details))


def _payload(read):
    return {"prompt_eval_count": read, "message": {"content": "a summary"}}


def test_a_truncated_prompt_is_reported():
    """The measured case: ~196 000 characters in, 16 387 tokens read."""
    ctx = _Ctx()
    _warn_if_truncated("x" * 196_000, _payload(16_387), "gemma3:4b", ctx)

    assert len(ctx.warnings) == 1
    code, message, details = ctx.warnings[0]
    assert code == "partial_output"
    assert "16387" in message and "silently" in message
    assert details["prompt_eval_count"] == 16_387
    assert details["prompt_tokens_at_least"] == 24_500
    # The remedy is named, since the ceiling is the daemon's, not the engine's.
    assert "OLLAMA_CONTEXT_LENGTH" in message


def test_a_prompt_that_fitted_says_nothing():
    """The other measured case: ~48 000 characters, 12 136 tokens read — whole.

    This is the assertion that matters most. A check that warns here would
    attach a caveat to every summary the engine produces, and a caveat that is
    always present is one nobody reads.
    """
    ctx = _Ctx()
    _warn_if_truncated("x" * 48_000, _payload(12_136), "gemma3:4b", ctx)
    assert ctx.warnings == []


def test_the_bound_is_conservative_enough_for_dense_text():
    """CJK runs near one character per token, so the bound under-counts wildly
    and the check stays silent. Missing a warning is the acceptable failure
    here; crying wolf on every Chinese transcript is not."""
    ctx = _Ctx()
    _warn_if_truncated("字" * 10_000, _payload(10_000), "qwen3:4b", ctx)
    assert ctx.warnings == []


def test_a_daemon_that_reports_nothing_is_not_guessed_at():
    ctx = _Ctx()
    for payload in ({}, {"prompt_eval_count": None}, {"prompt_eval_count": 0}):
        _warn_if_truncated("x" * 900_000, payload, "m", ctx)
    assert ctx.warnings == []


def test_the_proportion_is_stated_as_an_upper_bound():
    """90 000 words: the honest claim is "at most a fraction reached it", never
    a precise percentage the estimate cannot support."""
    ctx = _Ctx()
    _warn_if_truncated("x" * 545_000, _payload(16_387), "gemma3:4b", ctx)
    _, message, _ = ctx.warnings[0]
    assert "at most 24%" in message


# --- the warning has to reach the caller, not just the log ----------------------


def test_a_step_warning_travels_to_the_artifact_and_the_event_stream(
    store, settings, providers
):
    """The whole point: a caveat that stays inside the executor is worth
    nothing. It has to arrive where the two questions get asked — "what
    happened during this job" (the events) and "can I trust this file" (the
    artifact, read months later).

    Driven through a real submitted job so the wiring under test is the wiring
    that ships, not a hand-built context.
    """
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from content.execution.executor import JobExecutor
    from tests.conftest import make_request, minimal_payload

    fake = providers.for_source(make_request(minimal_payload()).sources[0])
    original = fake.execute

    def execute_with_warning(step, ctx):
        ctx.on_warning(
            "partial_output",
            "read only part of the source",
            {"prompt_eval_count": 16387},
        )
        return original(step, ctx)

    fake.execute = execute_with_warning

    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)
    payload = minimal_payload()
    result = submit_generation(
        payload,
        make_request(payload),
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=analysis_service,
    )
    executor.execute(store.claim_next_queued())

    job_id = result.job_id
    assert store.get_job(job_id)["status"] == "succeeded", "a warning is not a failure"

    # 1. on the artifact, where a caller holding the file will look
    artifact = store.list_artifacts(job_id)[0]
    warnings = artifact["provenance"]["warnings"]
    assert [w["code"] for w in warnings] == ["partial_output"]
    assert warnings[0]["details"]["prompt_eval_count"] == 16387

    # 2. in the event stream, where "what happened" is answered
    events = [e for e in store.list_events(job_id) if e["type"] == "step.warning"]
    assert len(events) == 1
    assert events[0]["data"]["code"] == "partial_output"
    assert events[0]["data"]["step_id"]


def test_an_unwarned_step_leaves_the_artifact_clean(store, settings, providers):
    """No caveat on the overwhelming majority of artifacts — the field is there
    and empty, so a caller can test it without special-casing its absence."""
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from content.execution.executor import JobExecutor
    from tests.conftest import make_request, minimal_payload

    payload = minimal_payload()
    result = submit_generation(
        payload,
        make_request(payload),
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=AnalysisService(store, providers, settings),
    )
    JobExecutor(store, settings, providers).execute(store.claim_next_queued())

    assert store.list_artifacts(result.job_id)[0]["provenance"]["warnings"] == []


def test_a_warning_does_not_leak_into_the_next_job(store, settings, providers):
    """The regression a real run caught and the unit tests above could not.

    One `JobExecutor` is built at startup and shared by every worker thread
    (`content/api/app.py:320`), while a step id is derived from the output id —
    so two unrelated jobs asking for an output called `sum` both run a step
    called `summarize_sum`. Warnings held on the *executor* and keyed by step
    id therefore surfaced on the next job's artifact: a two-page transcript
    inheriting "at most 70% of the source reached the model" from a two-hour
    one. They belong to the run, not to the worker — and with threads sharing
    that executor, "the next job" can also be a concurrent one.
    """
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from content.execution.executor import JobExecutor
    from tests.conftest import make_request, minimal_payload

    executor = JobExecutor(store, settings, providers)
    fake = providers.for_source(make_request(minimal_payload()).sources[0])
    original = fake.execute
    warn = {"on": True}

    def execute(step, ctx):
        if warn["on"]:
            ctx.on_warning("partial_output", "read only part of the source", {})
        return original(step, ctx)

    fake.execute = execute

    def run() -> str:
        payload = minimal_payload()
        result = submit_generation(
            payload,
            make_request(payload),
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=AnalysisService(store, providers, settings),
        )
        executor.execute(store.claim_next_queued())
        return result.job_id

    noisy = run()
    warn["on"] = False
    quiet = run()

    assert store.list_artifacts(noisy)[0]["provenance"]["warnings"] != []
    assert store.list_artifacts(quiet)[0]["provenance"]["warnings"] == []
