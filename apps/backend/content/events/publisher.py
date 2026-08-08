"""JobEvent publication: append-only, sequenced, persisted.

Raw process output never goes through here (it lands in the job's logs/); the
event stream is the structured, replayable progress protocol. SSE will consume
the same store later.
"""

from content.persistence.store import Store


class EventPublisher:
    def __init__(self, store: Store):
        self._store = store

    def publish(self, job_id: str, event_type: str, data: dict | None = None) -> int:
        return self._store.append_event(job_id, event_type, data or {})

    def step_progress(
        self,
        job_id: str,
        step_id: str,
        current: float,
        total: float,
        unit: str,
        message: str = "",
    ) -> int:
        return self.publish(
            job_id,
            "step.progress",
            {
                "step_id": step_id,
                "progress": {"current": current, "total": total, "unit": unit},
                "message": message,
            },
        )
