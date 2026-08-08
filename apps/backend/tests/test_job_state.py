"""Job/step state machines and terminal aggregation rules."""

import pytest

from content.domain.job import (
    InvalidTransition,
    aggregate_final_status,
    ensure_job_transition,
    ensure_step_transition,
)


def test_nominal_job_path_is_legal():
    path = ["created", "validating", "planning", "queued", "running", "succeeded"]
    for current, target in zip(path, path[1:]):
        ensure_job_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("created", "running"),
        ("queued", "succeeded"),
        ("succeeded", "running"),
        ("failed", "queued"),
        ("cancelled", "running"),
        ("running", "queued"),
    ],
)
def test_illegal_job_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        ensure_job_transition(current, target)


def test_cancellation_allowed_from_every_non_terminal_state():
    for state in ("created", "validating", "planning", "queued", "running"):
        ensure_job_transition(state, "cancelled")


def test_step_skip_only_before_running():
    ensure_step_transition("pending", "skipped")
    ensure_step_transition("ready", "skipped")
    with pytest.raises(InvalidTransition):
        ensure_step_transition("running", "skipped")


@pytest.mark.parametrize(
    ("policy", "required_missing", "optional_missing", "any_artifact", "expected"),
    [
        ("required_only", False, False, True, "succeeded"),
        ("required_only", False, True, True, "partially_succeeded"),
        ("required_only", True, False, True, "failed"),
        ("required_only", True, True, False, "failed"),
        ("fail_fast", True, False, True, "failed"),
        ("fail_fast", False, False, True, "succeeded"),
        ("best_effort", True, False, True, "partially_succeeded"),
        ("best_effort", True, True, False, "failed"),
        ("best_effort", False, False, True, "succeeded"),
    ],
)
def test_final_status_aggregation(
    policy, required_missing, optional_missing, any_artifact, expected
):
    assert (
        aggregate_final_status(policy, required_missing, optional_missing, any_artifact)
        == expected
    )
