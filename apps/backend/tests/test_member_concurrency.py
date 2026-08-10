"""Bounded concurrency for collection members (ADR 0019).

The executor dispatches a run of consecutive ``collection.member`` steps to a
small thread pool sized by ``CONTENT_COLLECTION_MEMBER_CONCURRENCY`` (default
2 — a politeness bound toward the provider, not a throughput feature). What
these tests pin down is that everything the sequential executor guaranteed
survives two members in flight: really-concurrent dispatch, isolated
workdirs, attributable progress, prompt cancellation, unchanged
failure-policy semantics, and one step log per member.

The overlap proofs use a ``threading.Barrier`` both members must reach
*inside* the provider: it can only be crossed when the members are in flight
at the same time, so a silently-sequential executor breaks the barrier
instead of silently passing.
"""

import dataclasses
import threading
import time

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.domain.analysis import CollectionEntry
from content.execution.executor import JobExecutor
from content.providers.base import StepExecutionError
from tests.conftest import FakeProvider, make_request, minimal_payload

_BARRIER_TIMEOUT = 10.0  # generous; only ever waited out by a real regression


def _each_item_video_payload(**overrides) -> dict:
    return minimal_payload(
        sources=[
            {"id": "main", "type": "url", "uri": "https://example.com/playlist?list=X"}
        ],
        outputs=[{"id": "vid", "type": "video", "scope": "each_item"}],
        **overrides,
    )


def _pipeline(store, providers, settings):
    """submit → claim → execute, like the API worker would."""
    service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=service,
        )
        executor.execute(store.claim_next_queued())
        return result.job_id

    return run


def test_two_members_really_run_at_the_same_time(
    store, providers, settings, monkeypatch
):
    """The barrier is crossed only if both members are in flight together —
    and their workdirs must be distinct while they are (the prerequisite that
    made concurrency safe, now exercised on the concurrent path itself)."""
    barrier = threading.Barrier(2)
    workdirs: dict[str, object] = {}
    original = FakeProvider.execute

    def meet_in_the_middle(self, step, ctx):
        workdirs[step.params.get("uri", "")] = ctx.workdir
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        return original(self, step, ctx)

    monkeypatch.setattr(FakeProvider, "execute", meet_in_the_middle)

    job_id = _pipeline(store, providers, settings)(_each_item_video_payload())

    assert store.get_job(job_id)["status"] == "succeeded"
    assert len(store.list_artifacts(job_id)) == 2
    assert len(set(workdirs.values())) == 2, "concurrent members shared a workdir"


def test_a_limit_of_one_keeps_members_strictly_sequential(
    store, providers, settings, monkeypatch
):
    """Setting the bound to 1 must restore the sequential executor exactly:
    the two members' execution intervals may not overlap."""
    sequential = dataclasses.replace(settings, collection_member_concurrency=1)
    intervals: list[tuple[float, float]] = []
    original = FakeProvider.execute

    def record_interval(self, step, ctx):
        start = time.monotonic()
        try:
            return original(self, step, ctx)
        finally:
            intervals.append((start, time.monotonic()))

    monkeypatch.setattr(FakeProvider, "execute", record_interval)

    job_id = _pipeline(store, providers, sequential)(_each_item_video_payload())

    assert store.get_job(job_id)["status"] == "succeeded"
    assert len(intervals) == 2
    first, second = sorted(intervals)
    assert first[1] <= second[0], "members overlapped despite a limit of 1"


def test_progress_stays_attributable_with_members_in_flight(
    store, providers, settings, monkeypatch
):
    """Interleaved progress must still say which member it belongs to: every
    ``step.progress`` event carries the member step's id, and no event of one
    member reports another member's message ("3/6 · Title · 73%" depends on
    exactly this)."""
    barrier = threading.Barrier(2)
    original = FakeProvider.execute

    def report_progress(self, step, ctx):
        uri = step.params.get("uri", "")
        barrier.wait(timeout=_BARRIER_TIMEOUT)  # both members mid-flight
        ctx.on_progress(33.0, f"downloading {uri}")
        ctx.on_progress(66.0, f"downloading {uri}")
        return original(self, step, ctx)

    monkeypatch.setattr(FakeProvider, "execute", report_progress)

    job_id = _pipeline(store, providers, settings)(_each_item_video_payload())

    progress = [
        event["data"]
        for event in store.list_events(job_id)
        if event["type"] == "step.progress"
    ]
    by_step: dict[str, set[str]] = {}
    for data in progress:
        by_step.setdefault(data["step_id"], set()).add(data["message"])
    assert len(by_step) == 2, "each member must report under its own step id"
    for messages in by_step.values():
        uris = {message.removeprefix("downloading ") for message in messages}
        assert len(uris) == 1, f"one step id carries two members' progress: {messages}"


def test_cancellation_reaches_members_in_flight(
    store, providers, settings, monkeypatch
):
    """A cancel requested while both members are downloading stops them
    through their own ``cancel_check`` — the job finishes cancelled, and no
    member's work is registered as an artifact."""
    barrier = threading.Barrier(2)
    job_ref: dict[str, str] = {}

    def cancel_midway(self, step, ctx):
        barrier.wait(timeout=_BARRIER_TIMEOUT)  # both members are in flight
        # Both members flip the flag (idempotent), exactly as an API cancel
        # arriving mid-download would look to each of them.
        store.request_cancel(job_ref["id"])
        deadline = time.monotonic() + _BARRIER_TIMEOUT
        while not ctx.cancel_check():
            assert time.monotonic() < deadline, "cancel never reached the member"
            time.sleep(0.01)
        raise StepExecutionError("cancelled", "stopped by request")

    monkeypatch.setattr(FakeProvider, "execute", cancel_midway)

    # Submit and execute in two beats so the job id is known to the provider
    # closure before any member starts.
    service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)
    payload = _each_item_video_payload()
    result = submit_generation(
        payload,
        make_request(payload),
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=service,
    )
    job_ref["id"] = result.job_id
    executor.execute(store.claim_next_queued())

    assert store.get_job(result.job_id)["status"] == "cancelled"
    assert store.list_artifacts(result.job_id) == []
    statuses = {step["status"] for step in store.list_steps(result.job_id)}
    assert statuses == {"cancelled"}, statuses


def test_fail_fast_lets_running_members_finish_and_starts_no_new_ones(
    store, providers, settings, monkeypatch
):
    """The decided semantics for ``fail_fast`` under concurrency: a failing
    required member stops members that have not started, while a member
    already downloading runs to completion — its artifact is valid work and is
    kept. With three members and a bound of 2: member 1 fails, member 2 (in
    flight when the failure lands) still succeeds, member 3 never starts."""
    barrier = threading.Barrier(2)
    original_execute = FakeProvider.execute
    original_analyze = FakeProvider.analyze

    def analyze_with_three_members(self, source, ctx):
        analysis = original_analyze(self, source, ctx)
        if analysis.resource.resource_type == "collection":
            analysis.entries.append(
                CollectionEntry(id="v3", title="Third", url="https://x/v3")
            )
        return analysis

    def first_fails_while_second_runs(self, step, ctx):
        uri = step.params.get("uri", "")
        if uri.endswith("/v1"):
            barrier.wait(timeout=_BARRIER_TIMEOUT)  # member 2 is in flight
            raise StepExecutionError("provider_error", "simulated member failure")
        if uri.endswith("/v2"):
            barrier.wait(timeout=_BARRIER_TIMEOUT)
            time.sleep(0.05)  # still running when member 1's failure lands
        return original_execute(self, step, ctx)

    monkeypatch.setattr(FakeProvider, "analyze", analyze_with_three_members)
    monkeypatch.setattr(FakeProvider, "execute", first_fails_while_second_runs)

    job_id = _pipeline(store, providers, settings)(
        _each_item_video_payload(execution={"failure_policy": "fail_fast"})
    )

    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1, "the member already in flight keeps its artifact"
    assert artifacts[0]["provenance"]["attributes"]["member_index"] == 2

    events = store.list_events(job_id)
    failed = [e["data"] for e in events if e["type"] == "step.failed"]
    skipped = [e["data"] for e in events if e["type"] == "step.skipped"]
    assert len(failed) == 1
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "skipped by fail_fast policy"


def test_each_member_writes_its_own_step_log(store, providers, settings, monkeypatch):
    """A member's inner steps log into the *member's* step log (keyed by the
    outer step id). SponsorBlock's SBCUT parsing reads exactly that file, so
    two members in flight must never share one — prove the files are distinct
    and uncontaminated while both members really overlap."""
    barrier = threading.Barrier(2)
    logs: dict[str, object] = {}
    original = FakeProvider.execute

    def log_a_marker(self, step, ctx):
        uri = step.params.get("uri", "")
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        with open(ctx.stdout_log, "a", encoding="utf-8") as handle:
            handle.write(f"MARKER {uri}\n")
        logs[uri] = ctx.stdout_log
        return original(self, step, ctx)

    monkeypatch.setattr(FakeProvider, "execute", log_a_marker)

    job_id = _pipeline(store, providers, settings)(_each_item_video_payload())

    assert store.get_job(job_id)["status"] == "succeeded"
    assert len(set(logs.values())) == 2, "two members shared a step log"
    for uri, path in logs.items():
        content = path.read_text(encoding="utf-8")
        assert content == f"MARKER {uri}\n", f"foreign lines in {path.name}"


def test_plain_jobs_and_lone_members_never_touch_the_pool(
    store, providers, settings, monkeypatch
):
    """An ordinary single-video job and a one-member collection must not pay
    for the pool: the executor runs them inline on the worker's own thread
    (the pool's threads are recognizable by their job-id prefix)."""
    thread_names: list[str] = []
    original_execute = FakeProvider.execute
    original_analyze = FakeProvider.analyze

    def record_thread(self, step, ctx):
        thread_names.append(threading.current_thread().name)
        return original_execute(self, step, ctx)

    def analyze_with_one_member(self, source, ctx):
        analysis = original_analyze(self, source, ctx)
        if analysis.resource.resource_type == "collection":
            del analysis.entries[1:]
        return analysis

    monkeypatch.setattr(FakeProvider, "execute", record_thread)
    monkeypatch.setattr(FakeProvider, "analyze", analyze_with_one_member)

    run = _pipeline(store, providers, settings)
    plain = run(minimal_payload())  # a plain single-source audio job
    lone = run(_each_item_video_payload())  # a collection of exactly one
    assert store.get_job(plain)["status"] == "succeeded"
    assert store.get_job(lone)["status"] == "succeeded"
    assert thread_names, "nothing executed?"
    assert all("member" not in name for name in thread_names), thread_names
