"""Storage roots and their lifecycles — the single authority on *where* files
live (docs/storage.md). Path *building* stays here so no provider or route
invents its own layout.

Four roots, four lifecycles (ADR 0009):

    <data_root>/
    ├── jobs/<job_id>/{work,artifacts,logs,snapshots,sources}
    ├── tmp/            # incomplete, technical, disposable — never an artifact
    └── cache/          # validated, cross-job reusable — DISABLED in V1

``tmp != work != artifact != cache`` — each has its own cleanup policy. The
database stays the source of truth; artifacts are addressed by ``(job_id,
filename)`` relative to ``data_root``, so the tree can be relocated.
"""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Identifiers that name a directory segment (job_id, step_id, operation_id).
# The backend controls these; this rejects traversal and anything unsafe.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_segment(value: str, kind: str = "identifier") -> str:
    """Validate a single path segment coming from a backend-controlled id.

    Rejects ``..``, separators and anything outside the allowlist — a
    controlled id must never be able to escape its root (INV-STORAGE-006)."""
    if value in ("", ".", "..") or not _ID_PATTERN.match(value):
        raise ValueError(f"unsafe {kind}: {value!r}")
    return value


def _copy_preserving_what_it_can(source: Path, target: Path) -> None:
    """Copy the bytes, then carry the metadata across if the filesystem lets us.

    ``shutil.copy2`` is ``copyfile`` followed by ``copystat``, and ``copystat``
    calls ``os.utime`` — which a CIFS/SMB mount refuses outright with EPERM.
    Both callers below stage *beside the destination*, which on such a
    deployment is the share itself: the bytes landed, the timestamp call
    raised, and the publish failed with the copy already complete. A finished
    artifact was lost to a modification time.

    So the two halves are separated. The copy must succeed; the metadata is an
    improvement on top of it. Still attempted rather than dropped, because
    timestamps are worth preserving wherever they are allowed — and not
    logged, because nothing in this module logs and a share refusing ``utime``
    is expected rather than exceptional.
    """
    shutil.copyfile(source, target)
    try:
        shutil.copystat(source, target)
    except OSError:
        pass


def publish_file(source: Path, destination: Path, *, overwrite: bool = False) -> Path:
    """Publish a completed file to its final path (INV-STORAGE-007/008).

    Atomic (``os.replace``) when source and destination share a filesystem;
    otherwise copy to a sibling temp file then atomically rename, so a partial
    file is never visible at ``destination``. The source is removed on success.
    """
    source = Path(source)
    destination = Path(destination)
    if not overwrite and destination.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)  # atomic on the same filesystem
        return destination
    except OSError:
        # Cross-filesystem: stage next to the destination, then rename in place.
        staged = destination.parent / f".{destination.name}.partial"
        try:
            _copy_preserving_what_it_can(source, staged)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        return destination


def claim_path(destination: Path) -> bool:
    """Reserve *destination* by creating it, exclusively. ``True`` when this
    caller created it, ``False`` when someone else already holds the name.

    This is the answer to check-then-act: ``if not path.exists(): write(path)``
    leaves a window between the look and the write in which another writer can
    take the same name, and the loser is silently overwritten. ``O_CREAT |
    O_EXCL`` is one atomic syscall — the name is claimed by the act of creating
    it, so two racers cannot both win.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    os.close(handle)
    return True


def stage_beside(source: Path, directory: Path, *, move: bool = False) -> Path:
    """Put *source*'s bytes in *directory* under a private, hidden name.

    Staging first is what lets a name be claimed with its content already in
    place: the slow part (the copy) happens somewhere nobody is looking, and
    taking the final name is then a single atomic step. The staging name is
    unique per caller, so concurrent writers never stage over each other.
    ``move=True`` consumes the source (a produced file being promoted);
    otherwise it is left alone (an artifact copied under a second name).
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        dir=directory, prefix=".staging-", suffix=".partial"
    )
    os.close(handle)
    staged = Path(name)
    try:
        if move:
            publish_file(Path(source), staged, overwrite=True)
        else:
            _copy_preserving_what_it_can(Path(source), staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def claim_with(staged: Path, destination: Path) -> bool:
    """Give the staged file the name *destination*, but only if it is free.

    ``True`` when the name is now ours and holds the staged content, ``False``
    when another writer holds it — the caller moves to the next candidate.

    A hard link publishes name and content in the same atomic step, so nothing
    incomplete is ever visible at *destination*: it does not exist, then it is
    the finished file. That matters because the delivery library is watched by
    Plex, Jellyfin or Emby, and a zero-byte file appearing there is a broken
    library entry. Where hard links are unavailable — some network and
    FAT-family mounts a NAS user may well have — the name is claimed empty and
    filled by a rename, which reduces the visible window to a rename rather
    than the length of a copy.
    """
    staged, destination = Path(staged), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(staged, destination)
    except FileExistsError:
        return False
    except OSError:
        if not claim_path(destination):
            return False
        os.replace(staged, destination)
        return True
    staged.unlink(missing_ok=True)
    return True


def _dir_stats(path: Path) -> dict:
    """Total bytes + file count under a directory (0/0 if absent).

    A file that disappears between ``is_file()`` and ``stat()`` is skipped
    rather than fatal: housekeeping deletes uploads and whole job trees from
    its own loop, so any walk of a live storage root races it. See
    :func:`tree_bytes` for why this guard is the shared one.
    """
    total, count = 0, 0
    if path.exists():
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
                count += 1
    return {"bytes": total, "files": count}


def tree_bytes(path: Path) -> int:
    """Total bytes of the files under *path* (0 if absent).

    The one place that answers "how much is under this tree", because the
    answer has a race in it and each copy of the walk had to remember the same
    guard. ``rglob`` survives a directory vanishing mid-iteration; ``stat`` on
    an entry it already yielded does not, and raises ``FileNotFoundError``.

    That window is not theoretical. Housekeeping removes expired uploads and
    deleted job trees from the worker's own loop, and the upload quota is
    checked by walking *every* owner's uploads — so a sweep landing between
    "it is a file" and "how big is it" turned a valid upload into a 500. The
    sweep itself always guarded its own walk (and says so); two callers
    counting the same bytes did not.

    Measuring is never worth failing for: what vanished mid-walk is, by
    definition, no longer occupying the disk this number describes.
    """
    return _dir_stats(path)["bytes"]


def owner_storage_report(settings, owner_id: str) -> dict:
    """What one owner occupies, and what of it counts against their quota.

    The families that belong to the installation are absent: the shared fact
    cache is not theirs, and the server's own paths are not their business.

    **`total_bytes` counts what the person asked for, not what the engine keeps
    to be fast.** Artifacts are the results they wanted, the library is where
    they wanted them, uploads are what they put there themselves. `resources`
    is the raw material the engine holds so the *next* job need not fetch it
    again, and `tmp` is scratch — charging for either would bill someone for an
    optimisation they did not request, and would punish the very reuse that
    makes the engine cheaper to run.

    Paths are deliberately absent. An owner needs to know how much they hold,
    never where the machine keeps it.
    """
    from content.storage.roots import owner_roots

    roots = owner_roots(settings, owner_id)
    # Artifacts only, not the whole job tree: logs, snapshots and the request
    # record are the history, a few kilobytes that nobody should pay for.
    artifacts = {"bytes": 0, "files": 0}
    history = {"bytes": 0, "files": 0}
    job_count = 0
    if roots.jobs.exists():
        for job_dir in roots.jobs.iterdir():
            if not job_dir.is_dir():
                continue
            job_count += 1
            produced = _dir_stats(job_dir / "artifacts")
            whole = _dir_stats(job_dir)
            artifacts = {
                "bytes": artifacts["bytes"] + produced["bytes"],
                "files": artifacts["files"] + produced["files"],
            }
            history = {
                "bytes": history["bytes"] + whole["bytes"] - produced["bytes"],
                "files": history["files"] + whole["files"] - produced["files"],
            }

    # Under a shared library none of it is this owner's to report — counting it
    # would bill them for other people's files.
    delivery = (
        _dir_stats(roots.output)
        if roots.output is not None and roots.output_is_private
        else {"bytes": 0, "files": 0}
    )
    uploads = _dir_stats(roots.uploads)
    resources = _dir_stats(roots.resources)
    tmp = _dir_stats(roots.tmp)
    return {
        "owner_id": owner_id,
        "jobs": {"count": job_count},
        "artifacts": artifacts,
        "delivery": delivery,
        "uploads": uploads,
        # Reported so a person can see it, excluded from the total on purpose.
        "resources": resources,
        "history": history,
        "tmp": tmp,
        "total_bytes": artifacts["bytes"] + delivery["bytes"] + uploads["bytes"],
    }


def _owners_on_disk(settings) -> list[str]:
    """Every owner this installation keeps files for, read from the tree.

    The flat layout holds exactly one owner by construction (ADR 0037), so it
    answers ``local`` without looking; the per-user layout answers with the
    directories under ``users/``. A directory no owner id could have produced
    is skipped rather than fatal: measuring is never worth failing for.
    """
    from content.identity import LOCAL_OWNER
    from content.storage.roots import PER_USER, layout_of, users_root

    if layout_of(settings) != PER_USER:
        return [LOCAL_OWNER]
    root = users_root(settings)
    if not root.is_dir():
        return []
    owners = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            owners.append(safe_segment(child.name, "owner_id"))
        except ValueError:
            continue
    return owners


def _outermost(paths: set[Path]) -> list[Path]:
    """The given directories with any nested one dropped.

    One family can resolve to several directories (one per owner) and, in the
    flat layout, to one directory that *contains* another — ``tmp/`` holds the
    shared analysis scratch. Measuring both would count those bytes twice.
    """
    return sorted(p for p in paths if not any(q in p.parents for q in paths))


def _combined_stats(paths: set[Path]) -> dict:
    """Bytes + file count over the directories a family resolves to."""
    total, files = 0, 0
    for path in _outermost(paths):
        stats = _dir_stats(path)
        total += stats["bytes"]
        files += stats["files"]
    return {"bytes": total, "files": files}


def _family_path(entries: list[tuple[str, Path]], fallback: Path) -> str:
    """One display string for where a family lives.

    The directory itself when the layout puts it in one place; the same path
    with the owner segment replaced by ``*`` when it is spread per owner —
    which is what an operator would type to go and look. The owner level is
    always one segment, so this stays true whatever ``roots.py`` decides.
    """
    unique = {path for _, path in entries}
    if len(unique) == 1:
        return str(unique.pop())
    if not unique:
        return str(fallback)
    owner, sample = min(entries, key=lambda entry: str(entry[1]))
    parts = list(sample.parts)
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == owner:
            parts[index] = "*"
            break
    return str(Path(*parts))


def storage_report(settings) -> dict:
    """Disk usage per storage family for observability (admin console).

    Reports the five lifecycles (docs/storage.md) plus a couple of useful
    sub-counts (job count, cached analyses, delivery folders).

    **Every family is measured where the layout actually puts it**, by asking
    ``roots.py`` for each owner's roots — the same way ``owner_storage_report``
    does. The flat shortcut this used to take (``<data>/uploads``,
    ``<data>/tmp``, one jobs root) is only true of the single-owner tree: on a
    per-user installation uploads, tmp and a per-owner library all live under
    ``users/<owner>/``, so the report answered **0 bytes** for three families
    whose bytes were on the disk, and counted them inside ``jobs`` instead
    because it measured the whole ``users/`` tree. The one family that was
    right is the cache, which belongs to nobody in either layout.
    """
    from content.storage.roots import owner_roots, shared_roots

    # Kept for the two installation-wide values: the cache is outside every
    # owner in both layouts, so it is the one family StoragePaths can answer.
    paths = StoragePaths.from_settings(settings)
    shared = shared_roots(settings)
    data_dir = Path(settings.data_dir)
    delivery_dir = Path(settings.delivery_dir or data_dir / "delivery")

    owners = _owners_on_disk(settings)
    roots = [(owner, owner_roots(settings, owner)) for owner in owners]

    jobs_dirs = [(owner, root.jobs) for owner, root in roots]
    # The shared analysis scratch is nobody's, and in the flat layout it sits
    # *inside* the one tmp root — `_outermost` is what keeps it counted once.
    tmp_dirs = [(owner, root.tmp) for owner, root in roots]
    uploads_dirs = [(owner, root.uploads) for owner, root in roots]
    delivery_dirs = [
        (owner, root.output) for owner, root in roots if root.output is not None
    ]
    if not any(root.output_is_private for _, root in roots):
        # One library for the whole installation (scope `shared`, or `flat`) —
        # and the configured location is measured even when the scope is `off`,
        # because bytes an earlier configuration delivered there are still on
        # the disk and "why is my disk full" has to account for them.
        delivery_dirs = [("", delivery_dir)]

    job_count = sum(
        sum(1 for entry in root.rglob("job_*") if entry.is_dir())
        for root in _outermost({path for _, path in jobs_dirs})
        if root.exists()
    )
    delivery_folders = sum(
        sum(1 for entry in root.rglob("*") if entry.is_dir())
        for root in _outermost({path for _, path in delivery_dirs})
        if root.exists()
    )
    upload_count = sum(
        sum(1 for entry in root.iterdir() if entry.is_dir())
        for root in _outermost({path for _, path in uploads_dirs})
        if root.exists()
    )
    analysis_cache = shared.cache_analysis
    cached_analyses = (
        sum(1 for p in analysis_cache.glob("*.json")) if analysis_cache.exists() else 0
    )
    return {
        "jobs": {
            **_combined_stats({path for _, path in jobs_dirs}),
            "count": job_count,
            "path": _family_path(jobs_dirs, data_dir / "jobs"),
        },
        "delivery": {
            **_combined_stats({path for _, path in delivery_dirs}),
            "folders": delivery_folders,
            "path": _family_path(delivery_dirs, delivery_dir),
        },
        "tmp": {
            **_combined_stats({path for _, path in tmp_dirs} | {shared.tmp_analysis}),
            "path": _family_path(tmp_dirs, shared.tmp_analysis.parent),
        },
        # The fifth family (ADR 0020). Reported so "why is my disk full" has an
        # answer without a shell, and so an operator can see the sweep working.
        "uploads": {
            **_combined_stats({path for _, path in uploads_dirs}),
            "count": upload_count,
            "ttl_hours": getattr(settings, "upload_ttl_hours", 0),
            "quota_bytes": getattr(settings, "uploads_total_bytes", 0),
            "path": _family_path(uploads_dirs, data_dir / "uploads"),
        },
        "cache": {
            **_dir_stats(paths.cache_root),
            "enabled": paths.cache_enabled,
            "cached_analyses": cached_analyses,
            "path": str(paths.cache_root),
        },
    }


@dataclass(frozen=True)
class StoragePaths:
    """Resolved storage roots. Build one from settings; ask it for paths."""

    data_root: Path
    tmp_root: Path
    cache_root: Path
    cache_enabled: bool = False

    @classmethod
    def from_settings(cls, settings) -> "StoragePaths":
        data_root = Path(settings.data_dir)
        return cls(
            data_root=data_root,
            tmp_root=Path(settings.tmp_dir or data_root / "tmp"),
            cache_root=Path(settings.cache_dir or data_root / "cache"),
            cache_enabled=bool(settings.cache_enabled),
        )

    # --- roots -----------------------------------------------------------------

    @property
    def jobs_root(self) -> Path:
        return self.data_root / "jobs"

    def ensure_tmp(self) -> Path:
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        return self.tmp_root

    def ensure_cache(self) -> Path:
        """Only create ``cache/`` when the cache is enabled — a disabled cache
        leaves no empty directory behind (INV-STORAGE-010)."""
        if not self.cache_enabled:
            raise RuntimeError("cache is disabled; refusing to create cache root")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        return self.cache_root

    # --- per-job paths ---------------------------------------------------------

    def job_root(self, job_id: str) -> Path:
        return self.jobs_root / safe_segment(job_id, "job_id")

    def job_tmp(self, job_id: str) -> Path:
        return self.tmp_root / safe_segment(job_id, "job_id")

    def temporary_operation_dir(
        self, job_id: str, step_id: str, operation_id: str
    ) -> Path:
        """A disposable scratch dir for one step operation, under ``tmp/``.

        ``tmp/<job_id>/<step_id>/<operation_id>/`` — every segment validated,
        so the result is always contained under ``tmp_root``."""
        return (
            self.tmp_root
            / safe_segment(job_id, "job_id")
            / safe_segment(step_id, "step_id")
            / safe_segment(operation_id, "operation_id")
        )

    # --- cleanup ---------------------------------------------------------------

    def purge_job_tmp(self, job_id: str) -> None:
        """Remove one job's tmp subtree (INV-STORAGE-004: scoped to the job)."""
        shutil.rmtree(self.job_tmp(job_id), ignore_errors=True)

    def cleanup_tmp(
        self, max_age_seconds: float, active_job_ids: set[str] | None = None
    ) -> list[str]:
        """Remove per-job tmp subtrees older than ``max_age_seconds`` whose job
        is not currently active. Never touches artifacts, work, logs or an
        active operation (INV-STORAGE-003). Returns the removed job ids."""
        import time

        active = active_job_ids or set()
        removed: list[str] = []
        if not self.tmp_root.exists():
            return removed
        cutoff = time.time() - max_age_seconds
        for child in self.tmp_root.iterdir():
            if not child.is_dir() or child.name in active:
                continue
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        return removed
