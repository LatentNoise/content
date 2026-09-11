"""One-time move of an ownerless job tree under its owner (ADR 0030).

Before ownership, jobs lived at ``<data>/jobs/<job_id>/``. They now live at
``<data>/jobs/<owner_id>/<job_id>/``. A database that existed before the change
holds exactly one user's work — the self-hosted single user — which is why the
schema migration could backfill ``owner_id`` to ``local`` without looking at a
single row, and why this pass can move every existing directory under
``jobs/local/`` without deciding anything.

Three properties it has to have, because it runs once against real data:

**Idempotent.** It recognizes an already-migrated tree and does nothing. Safe to
run on every start, which is where it is called from.

**Interruptible.** Directories move one at a time. A crash halfway leaves some
jobs moved and some not, and the next run finishes the job. There is no state
to repair because a moved directory is indistinguishable from one that was
always there.

**Conservative about what it touches.** Only entries whose name looks like a job
id (``job_…``) are moved. A directory that is already an owner id is left where
it is, which is what makes a second run a no-op rather than a disaster —
``jobs/local/`` must never end up at ``jobs/local/local/``.
"""

import logging
import shutil
from pathlib import Path

from content.identity import LOCAL_OWNER

logger = logging.getLogger("content.storage.migrate")

JOB_ID_PREFIX = "job_"


def _pending(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.iterdir()
        if entry.is_dir() and entry.name.startswith(JOB_ID_PREFIX)
    )


def migrate_jobs_to_owner(
    data_dir: Path, owner_id: str = LOCAL_OWNER, tmp_root: Path | None = None
) -> dict:
    """Move ownerless job directories under *owner_id*. Returns what happened.

    Never raises on a single failure: one unmovable directory must not stop the
    engine from starting, nor prevent the other directories from being filed.
    What could not be moved is counted and logged, and the next run retries it.
    """
    data_dir = Path(data_dir)
    moved, failed = 0, 0

    for root, label in (
        (data_dir / "jobs", "jobs"),
        (Path(tmp_root) if tmp_root else data_dir / "tmp", "tmp"),
    ):
        pending = _pending(root)
        if not pending:
            continue
        destination_root = root / owner_id
        destination_root.mkdir(parents=True, exist_ok=True)
        for source in pending:
            destination = destination_root / source.name
            if destination.exists():
                # Both shapes present for the same id: an earlier run was
                # interrupted between the move and the source's removal. The
                # destination is the migrated truth; the leftover goes.
                shutil.rmtree(source, ignore_errors=True)
                continue
            try:
                source.rename(destination)
                moved += 1
            except OSError as exc:
                failed += 1
                logger.warning(
                    "could not file %s/%s under %s: %s",
                    label,
                    source.name,
                    owner_id,
                    exc,
                )

    if moved or failed:
        logger.info(
            "ownership migration: %d directory(ies) filed under %r, %d failed",
            moved,
            owner_id,
            failed,
        )
    return {"moved": moved, "failed": failed, "owner_id": owner_id}
