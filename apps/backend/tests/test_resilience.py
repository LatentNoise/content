"""Operational resilience: cancellation mid-run, startup recovery, missing
tools, satisfiable constraints (docs/domain.md §4, docs/architecture.md §6)."""

import sqlite3
import sys

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.execution.executor import JobExecutor
from content.execution.process import run_process
from content.identity import LOCAL_OWNER
from content.planning.planner import build_plan
from tests.conftest import make_request, minimal_payload


@pytest.fixture
def submit(store, providers, settings):
    analysis_service = AnalysisService(store, providers, settings)

    def _submit(payload: dict) -> str:
        request = make_request(payload)
        return submit_generation(
            LOCAL_OWNER,
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=analysis_service,
        ).job_id

    return _submit


def test_cancel_between_steps_cancels_job_and_remaining_steps(
    submit, store, providers, settings
):
    job_id = submit(
        minimal_payload(
            outputs=[
                {"id": "audio", "type": "audio"},
                {"id": "meta", "type": "metadata"},
            ]
        )
    )
    claimed = store.claim_next_queued()

    # Simulate a cancel request arriving while the job is running: the flag is
    # already set when the executor starts its loop.
    store.request_cancel(LOCAL_OWNER, job_id)
    assert (
        store.get_job(LOCAL_OWNER, job_id)["status"] == "running"
    )  # not queued anymore

    JobExecutor(store, settings, providers).execute(claimed)

    job = store.get_job(LOCAL_OWNER, job_id)
    assert job["status"] == "cancelled"
    statuses = {
        s["step_id"]: s["status"] for s in store.list_steps(LOCAL_OWNER, job_id)
    }
    assert set(statuses.values()) == {"cancelled"}
    events = [e["type"] for e in store.list_events(LOCAL_OWNER, job_id)]
    assert events[-1] == "job.cancelled"
    assert store.list_artifacts(LOCAL_OWNER, job_id) == []


def test_orphaned_running_jobs_are_requeued_on_startup(submit, store):
    job_id = submit(minimal_payload())
    claimed = store.claim_next_queued()
    assert claimed["id"] == job_id
    assert store.get_job(LOCAL_OWNER, job_id)["status"] == "running"

    # Simulate a crash: the worker never finished. Startup recovery requeues.
    assert store.requeue_running() == 1
    job = store.get_job(LOCAL_OWNER, job_id)
    assert job["status"] == "queued"
    assert job["started_at"] is None


def test_missing_binary_is_a_normalized_failure(tmp_path):
    result = run_process(
        ["definitely-not-a-real-binary-xyz"],
        cwd=tmp_path,
        timeout_seconds=5,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
    )
    assert result.returncode == 127
    assert not result.ok
    assert "failed to start" in (tmp_path / "err.log").read_text()


def test_local_only_constraints_are_satisfiable_in_v1(store, providers, settings):
    # V1 executes everything locally: forbidding cloud providers and remote
    # processing must not fail feasibility.
    payload = minimal_payload(
        constraints={
            "privacy": {"allow_cloud_providers": False},
            "network": {"allow_remote_processing": False},
        }
    )
    request = make_request(payload)
    analysis = AnalysisService(store, providers, settings).analyze_sources(
        LOCAL_OWNER, list(request.sources)
    )
    plan = build_plan(request, analysis, providers, settings)
    assert [s.provider for s in plan.steps] == ["ytdlp"]  # local provider only


def test_plan_steps_come_out_in_topological_order(store, providers, settings):
    payload = minimal_payload(
        outputs=[
            {"id": "meta", "type": "metadata"},
            {"id": "audio", "type": "audio"},
            {"id": "thumb", "type": "thumbnail", "required": False},
        ]
    )
    request = make_request(payload)
    analysis = AnalysisService(store, providers, settings).analyze_sources(
        LOCAL_OWNER, list(request.sources)
    )
    plan = build_plan(request, analysis, providers, settings)
    ordered = plan.ordered_steps()
    seen: set[str] = set()
    for step in ordered:
        assert set(step.depends_on) <= seen
        seen.add(step.id)
    # Deterministic: same request twice, same order.
    plan_b = build_plan(request, analysis, providers, settings)
    assert [s.id for s in plan_b.ordered_steps()] == [s.id for s in ordered]


def _quiet_sleeper(seconds: float) -> list[str]:
    """A child that outputs nothing, so only the watchdog can end it.

    Silence is the point: `run_process` reads stdout on the main thread, so a
    process that never writes leaves the watchdog as the only thing enforcing
    either the deadline or a cancellation.
    """
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_a_failing_cancel_check_does_not_disable_the_timeout(tmp_path, caplog):
    """`cancel_check` is `store.is_cancel_requested`, polled every 0.3 s.

    It is the engine's most frequent database read, it runs on a bare daemon
    thread for the whole life of every download and transcode, and nothing used
    to guard it. One exception ended the watchdog, and with it the "mandatory
    timeout" this module's docstring promises — leaving an unbounded process
    and an uncancellable job behind a stderr traceback nobody reads.
    """

    def exploding_check() -> bool:
        raise sqlite3.OperationalError("database is locked")

    with caplog.at_level("ERROR", logger="content.process"):
        result = run_process(
            _quiet_sleeper(5),
            cwd=tmp_path,
            timeout_seconds=0.5,
            stdout_log=tmp_path / "out.log",
            stderr_log=tmp_path / "err.log",
            cancel_check=exploding_check,
        )

    assert result.timed_out, "the deadline stopped being enforced"
    assert not result.ok
    assert [r for r in caplog.records if "cancel check failed" in r.getMessage()]


def test_a_cancel_check_that_recovers_still_cancels(tmp_path):
    """Read as "not cancelled" while it cannot answer, honoured once it can."""
    answers = [sqlite3.OperationalError("database is locked"), False, True]

    def flaky_check() -> bool:
        answer = answers.pop(0) if answers else True
        if isinstance(answer, Exception):
            raise answer
        return answer

    result = run_process(
        _quiet_sleeper(5),
        cwd=tmp_path,
        timeout_seconds=30,
        stdout_log=tmp_path / "out.log",
        stderr_log=tmp_path / "err.log",
        cancel_check=flaky_check,
    )

    assert result.cancelled
    assert not result.timed_out
    assert not result.ok
