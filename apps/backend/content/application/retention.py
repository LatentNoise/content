"""Reclaiming disk: deleting a job on purpose, and sweeping old ones.

Two families accumulate and nothing removed them: `artifacts`, which are the
results themselves, and the delivery library. `tmp` and `work` were already
purged at the end of every job.

This matters more since quotas exist. A ceiling with no way to free space is a
dead end: someone who reaches it can never do anything again, and their only
recourse is to write to the operator.

Two rules shape the whole module.

**Deleting is explicit and scoped to an owner.** A job id from someone else's
list is not found, never forbidden — 404 would otherwise confirm it exists.

**The delivered copy is left alone by default.** `/output` is a library a human
organises and a media server reads; files there have been renamed, moved into
folders, added to playlists. Sweeping a job must not reach into it and take a
film out of someone's collection. Deleting deliberately may, if asked.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from content.storage.layout import JobStorage, delivery_root_for

log = logging.getLogger("content.retention")


@dataclass(frozen=True)
class Deletion:
    job_id: str
    freed_bytes: int
    delivered_removed: int


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def delete_job(
    owner_id: str,
    job_id: str,
    *,
    store,
    settings,
    include_delivered: bool = False,
) -> Deletion | None:
    """Remove one job: its files, then its rows. `None` when it is not theirs.

    Files first and rows last, deliberately. A crash between the two leaves an
    orphan directory, which housekeeping can find and a human can understand;
    the reverse leaves a row pointing at bytes that no longer exist, which the
    API would serve as a 500 forever.
    """
    job = store.get_job(owner_id, job_id)
    if job is None:
        return None

    delivered_paths: list[Path] = []
    if include_delivered:
        root = delivery_root_for(settings, owner_id)
        if root is not None:
            for artifact in store.list_artifacts(owner_id, job_id):
                relative = artifact.get("delivered_path") or ""
                if relative:
                    delivered_paths.append(root / relative)

    storage = JobStorage.from_settings(settings, owner_id, job_id)
    freed = _tree_bytes(storage.root)
    shutil.rmtree(storage.root, ignore_errors=True)
    shutil.rmtree(storage.tmp, ignore_errors=True)

    removed = 0
    for path in delivered_paths:
        try:
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
                removed += 1
        except OSError:
            # A delivered file may have been moved or renamed by its owner —
            # that is what a library is for. Losing track of it is not an error.
            log.info("delivered file already gone: %s", path.name)

    store.delete_job(owner_id, job_id)
    return Deletion(job_id=job_id, freed_bytes=freed, delivered_removed=removed)


def sweep_owner(
    owner_id: str, *, store, settings, now: datetime | None = None
) -> list[Deletion]:
    """Delete this owner's terminal jobs older than the retention window.

    Never touches the delivery library: a sweep runs unattended, and unattended
    deletion of someone's film collection is not a feature. The artifacts go;
    the copy the person organised stays.
    """
    days = float(getattr(settings, "retention_days", 0) or 0)
    if days <= 0:
        return []
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    deletions: list[Deletion] = []
    for job in store.jobs_finished_before(owner_id, cutoff):
        done = delete_job(
            owner_id, job["id"], store=store, settings=settings, include_delivered=False
        )
        if done is not None:
            deletions.append(done)
    return deletions


def sweep_all(*, store, settings, now: datetime | None = None) -> dict:
    """Sweep every owner. Returns what it freed, for a log line worth reading."""
    days = float(getattr(settings, "retention_days", 0) or 0)
    if days <= 0:
        return {"enabled": False, "jobs": 0, "freed_bytes": 0}
    jobs = 0
    freed = 0
    for owner_id in store.owners_with_jobs():
        for deletion in sweep_owner(owner_id, store=store, settings=settings, now=now):
            jobs += 1
            freed += deletion.freed_bytes
    if jobs:
        log.info("retention swept %s job(s), freeing %s bytes", jobs, freed)
    return {"enabled": True, "jobs": jobs, "freed_bytes": freed}
