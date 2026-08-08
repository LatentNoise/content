"""Job and step state machines, and the terminal-status aggregation rules.

All status changes go through :func:`ensure_job_transition` /
:func:`ensure_step_transition`; nothing else in the codebase mutates a status
string directly (docs/domain.md §4).
"""

from typing import Literal

JobStatus = Literal[
    "created",
    "validating",
    "planning",
    "queued",
    "running",
    "succeeded",
    "partially_succeeded",
    "failed",
    "cancelled",
]

StepStatus = Literal[
    "pending", "ready", "running", "succeeded", "failed", "skipped", "cancelled"
]

JOB_TERMINAL: frozenset[str] = frozenset(
    {"succeeded", "partially_succeeded", "failed", "cancelled"}
)

_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"validating", "cancelled"}),
    "validating": frozenset({"planning", "failed", "cancelled"}),
    "planning": frozenset({"queued", "failed", "cancelled"}),
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "partially_succeeded", "failed", "cancelled"}),
}

_STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "skipped", "cancelled"}),
    "ready": frozenset({"running", "skipped", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
}


class InvalidTransition(Exception):
    def __init__(self, kind: str, current: str, target: str):
        super().__init__(f"illegal {kind} transition: {current} -> {target}")
        self.current = current
        self.target = target


def ensure_job_transition(current: str, target: str) -> None:
    if target not in _JOB_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition("job", current, target)


def ensure_step_transition(current: str, target: str) -> None:
    if target not in _STEP_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition("step", current, target)


def aggregate_final_status(
    failure_policy: str,
    required_missing: bool,
    optional_missing: bool,
    any_artifact_produced: bool,
) -> JobStatus:
    """Terminal status of a run that was not cancelled (docs/domain.md §4).

    ``required_missing``: at least one required output produced no artifact.
    ``optional_missing``: at least one optional output produced no artifact.
    """
    if failure_policy == "best_effort":
        if not required_missing and not optional_missing:
            return "succeeded"
        return "partially_succeeded" if any_artifact_produced else "failed"
    # fail_fast and required_only: success demands every required output.
    if required_missing:
        return "failed"
    return "partially_succeeded" if optional_missing else "succeeded"
