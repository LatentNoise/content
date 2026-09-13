"""Filing existing directories into the configured layout (ADR 0037).

Three shapes can exist on a disk that has run earlier versions:

    before 0.8.0     <data>/jobs/<job>                       ownerless
    0.8.0 – 0.8.1    <data>/jobs/<owner>/<job>               owner level
    from 0.8.2       flat:     <data>/jobs/<job>
                     per_user: <data>/users/<owner>/jobs/<job>

This pass moves whatever it finds into the shape the configuration asks for.
The properties it has to have are the ones the ownership migration had: it is
**idempotent** (a migrated tree is recognised and left alone), **interruptible**
(directories move one at a time and the next start finishes), and
**conservative** (only names that look like what they claim to be are moved).

It moves directories and never rewrites a database row: the one recorded path,
an upload's, is derived from the layout at read time and honoured from the row
only while nothing exists at the derived location.

Leftovers are tolerated. An orphan directory is a wasted megabyte and a log
line; a lost one is a support ticket. Where the two collide — a destination
already holding the id — the destination is the migrated truth and the
leftover goes.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from content.identity import LOCAL_OWNER
from content.storage.roots import PER_USER, layout_of, owner_roots, users_root

logger = logging.getLogger("content.storage.migrate")

JOB_PREFIX = "job_"
UPLOAD_PREFIX = "upl_"


def _move_children(
    source: Path, destination: Path, prefix: str, counters: dict
) -> None:
    """Move every child of *source* whose name starts with *prefix* into
    *destination*, one at a time. A child already present at the destination
    means an earlier run was interrupted after the move; the leftover goes."""
    if not source.is_dir():
        return
    for child in sorted(source.iterdir()):
        if not child.is_dir() or not child.name.startswith(prefix):
            continue
        target = destination / child.name
        if target.exists():
            shutil.rmtree(child, ignore_errors=True)
            continue
        destination.mkdir(parents=True, exist_ok=True)
        try:
            child.rename(target)
            counters["moved"] += 1
        except OSError as exc:
            counters["failed"] += 1
            logger.warning("could not file %s into %s: %s", child, destination, exc)


def _move_one(child: Path, destination_root: Path, counters: dict) -> None:
    """Move a single directory under *destination_root*, same rules as above."""
    target = destination_root / child.name
    if target.exists():
        shutil.rmtree(child, ignore_errors=True)
        return
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        child.rename(target)
        counters["moved"] += 1
    except OSError as exc:
        counters["failed"] += 1
        logger.warning("could not file %s into %s: %s", child, destination_root, exc)


def _roots_or_none(settings, owner_name: str):
    """A directory that is not a valid owner id is somebody's stray folder,
    not ours to move: it is left where it is and mentioned once."""
    try:
        return owner_roots(settings, owner_name)
    except ValueError:
        logger.warning("leaving unrecognised directory %r where it is", owner_name)
        return None


def _remove_if_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _owner_dirs(root: Path) -> list[Path]:
    """Children of *root* that are owner directories: anything that is not a
    job, an upload or the analysis scratch — `local`, `usr_…`."""
    if not root.is_dir():
        return []
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir()
        and not child.name.startswith((JOB_PREFIX, UPLOAD_PREFIX))
        and child.name != "analysis"
    )


def migrate_layout(settings, store) -> dict:
    """Bring the disk to the configured layout. Returns what happened."""
    counters = {"moved": 0, "failed": 0, "layout": layout_of(settings)}
    data = Path(settings.data_dir)
    legacy_jobs = data / "jobs"
    legacy_tmp = Path(settings.tmp_dir or data / "tmp")
    legacy_uploads = Path(settings.uploads_dir or data / "uploads")

    if layout_of(settings) == PER_USER:
        _to_per_user(settings, store, legacy_jobs, legacy_tmp, legacy_uploads, counters)
    else:
        _to_flat(settings, legacy_jobs, legacy_tmp, counters)

    if counters["moved"] or counters["failed"]:
        logger.info(
            "layout migration to %s: %d directory(ies) moved, %d failed",
            counters["layout"],
            counters["moved"],
            counters["failed"],
        )
    return counters


def _to_flat(settings, legacy_jobs: Path, legacy_tmp: Path, counters: dict) -> None:
    """Flat holds one owner. Anything filed under an owner level comes back up.

    The 0.8.0 shape `jobs/<owner>/<job>` is the one to undo, and the per-user
    shape `users/<owner>/…` too, for an instance that switched back. Both
    collapse onto `local`: a flat tree cannot tell owners apart, and a flat
    tree with several owners was refused at startup before this ran.
    """
    roots = owner_roots(settings, LOCAL_OWNER)
    for owner_dir in _owner_dirs(legacy_jobs):
        _move_children(owner_dir, roots.jobs, JOB_PREFIX, counters)
        _remove_if_empty(owner_dir)
    for owner_dir in _owner_dirs(legacy_tmp):
        _move_children(owner_dir, roots.tmp, JOB_PREFIX, counters)
        _remove_if_empty(owner_dir)
    users = users_root(settings)
    for owner_dir in _owner_dirs(users):
        _move_children(owner_dir / "jobs", roots.jobs, JOB_PREFIX, counters)
        _move_children(owner_dir / "tmp", roots.tmp, JOB_PREFIX, counters)
        _move_children(owner_dir / "uploads", roots.uploads, UPLOAD_PREFIX, counters)
        for sub in ("jobs", "tmp", "uploads"):
            _remove_if_empty(owner_dir / sub)
        _remove_if_empty(owner_dir)
    _remove_if_empty(users)


def _to_per_user(
    settings,
    store,
    legacy_jobs: Path,
    legacy_tmp: Path,
    legacy_uploads: Path,
    counters: dict,
) -> None:
    """Per-user files everything under `users/<owner>/`.

    Three sources, in order of age: ownerless jobs (they belong to `local`,
    the only owner a pre-ownership database can hold), jobs already filed
    under an owner level, and uploads — whose owner only the database knows,
    which is why this pass takes the store.
    """
    # 1. Ownerless jobs and tmp: pre-0.8.0. Exactly one owner is possible.
    local = owner_roots(settings, LOCAL_OWNER)
    _move_children(legacy_jobs, local.jobs, JOB_PREFIX, counters)
    _move_children(legacy_tmp, local.tmp, JOB_PREFIX, counters)

    # 2. The 0.8.0 owner level: jobs/<owner>/<job> → users/<owner>/jobs/<job>.
    for owner_dir in _owner_dirs(legacy_jobs):
        roots = _roots_or_none(settings, owner_dir.name)
        if roots is None:
            continue
        _move_children(owner_dir, roots.jobs, JOB_PREFIX, counters)
        _remove_if_empty(owner_dir)
    for owner_dir in _owner_dirs(legacy_tmp):
        roots = _roots_or_none(settings, owner_dir.name)
        if roots is None:
            continue
        _move_children(owner_dir, roots.tmp, JOB_PREFIX, counters)
        _remove_if_empty(owner_dir)
    _remove_if_empty(legacy_jobs)

    # 3. Uploads never had an owner level on disk. The row says whose it is.
    if legacy_uploads.is_dir() and legacy_uploads != local.uploads:
        for child in sorted(legacy_uploads.iterdir()):
            if not child.is_dir() or not child.name.startswith(UPLOAD_PREFIX):
                continue
            owner_id = store.upload_owner_any(child.name) or LOCAL_OWNER
            _move_one(child, owner_roots(settings, owner_id).uploads, counters)
        _remove_if_empty(legacy_uploads)
