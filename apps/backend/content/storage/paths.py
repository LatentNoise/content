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
            shutil.copy2(source, staged)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        return destination


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

    Reports the four lifecycles (docs/storage.md) plus a couple of useful
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
    return {
        "jobs": {**jobs, "count": job_count, "path": str(paths.jobs_root)},
        "delivery": {
            **_dir_stats(delivery_root),
            "folders": delivery_folders,
            "path": str(delivery_root),
        },
        "tmp": {**_dir_stats(paths.tmp_root), "path": str(paths.tmp_root)},
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
