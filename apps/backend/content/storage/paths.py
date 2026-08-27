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
    """Total bytes + file count under a directory (0/0 if absent)."""
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


def storage_report(settings) -> dict:
    """Disk usage per storage family for observability (admin console).

    Reports the five lifecycles (docs/storage.md) plus a couple of useful
    sub-counts (job count, cached analyses, delivery folders)."""
    paths = StoragePaths.from_settings(settings)
    delivery_root = Path(settings.delivery_dir or settings.data_dir / "delivery")
    jobs = _dir_stats(paths.jobs_root)
    job_count = (
        sum(1 for p in paths.jobs_root.iterdir() if p.is_dir())
        if paths.jobs_root.exists()
        else 0
    )
    analysis_cache = paths.cache_root / "analysis"
    cached_analyses = (
        sum(1 for p in analysis_cache.glob("*.json")) if analysis_cache.exists() else 0
    )
    delivery_folders = (
        sum(1 for p in delivery_root.rglob("*") if p.is_dir())
        if delivery_root.exists()
        else 0
    )
    uploads_root = Path(
        getattr(settings, "uploads_dir", None) or settings.data_dir / "uploads"
    )
    upload_count = (
        sum(1 for p in uploads_root.iterdir() if p.is_dir())
        if uploads_root.exists()
        else 0
    )
    return {
        "jobs": {**jobs, "count": job_count, "path": str(paths.jobs_root)},
        "delivery": {
            **_dir_stats(delivery_root),
            "folders": delivery_folders,
            "path": str(delivery_root),
        },
        "tmp": {**_dir_stats(paths.tmp_root), "path": str(paths.tmp_root)},
        # The fifth family (ADR 0020). Reported so "why is my disk full" has an
        # answer without a shell, and so an operator can see the sweep working.
        "uploads": {
            **_dir_stats(uploads_root),
            "count": upload_count,
            "ttl_hours": getattr(settings, "upload_ttl_hours", 0),
            "quota_bytes": getattr(settings, "uploads_total_bytes", 0),
            "path": str(uploads_root),
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
