"""Per-job filesystem layout. Builds paths under the data directory alongside
``paths.py`` (the roots authority) — the future S3/object-storage boundary. See
docs/storage.md and ADR 0009 for the tmp/work/artifacts/cache separation.

    <data_dir>/jobs/<job_id>/
    ├── sources/    # materialized input materials
    ├── work/       # VALID intermediate files, purged when the job ends
    ├── artifacts/  # final results, backend-generated names
    ├── logs/       # per-step stdout/stderr
    └── snapshots/  # request/analysis/plan/result JSON snapshots
    <tmp_root>/<job_id>/  # disposable scratch, its own root and cleanup

The database stays the source of truth; files are addressed relative to the
job directory so the tree can be relocated.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

# Canonical home is content.naming.sanitize (ADR 0017); sanitize_filename is
# re-exported here for the storage-side callers that predate the naming module.
from content.naming.sanitize import display_name, sanitize_filename
from content.storage.paths import publish_file, safe_segment


def checksum_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class JobStorage:
    """Per-job filesystem paths. ``work`` holds valid intermediates for this
    job; ``artifacts`` holds persistent results; ``tmp`` holds disposable
    scratch (its own root, cleaned independently — INV-STORAGE-001/004)."""

    def __init__(self, data_dir: Path, job_id: str, tmp_root: Path | None = None):
        safe_segment(job_id, "job_id")
        self.job_id = job_id
        self.root = Path(data_dir) / "jobs" / job_id
        self.sources = self.root / "sources"
        self.work = self.root / "work"
        self.artifacts = self.root / "artifacts"
        self.logs = self.root / "logs"
        self.snapshots = self.root / "snapshots"
        self.tmp = Path(tmp_root or Path(data_dir) / "tmp") / job_id

    @classmethod
    def from_settings(cls, settings, job_id: str) -> "JobStorage":
        return cls(settings.data_dir, job_id, tmp_root=settings.tmp_dir)

    def ensure(self) -> "JobStorage":
        for directory in (
            self.sources,
            self.work,
            self.artifacts,
            self.logs,
            self.snapshots,
            self.tmp,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def step_tmp(self, step_id: str) -> Path:
        """A disposable scratch dir for one step, under the job's tmp root."""
        path = self.tmp / safe_segment(step_id, "step_id")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_snapshot(self, name: str, payload: dict) -> Path:
        path = self.snapshots / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return path

    def step_log_path(self, step_id: str, stream: str) -> Path:
        return self.logs / f"{sanitize_filename(step_id)}.{stream}.log"

    def _free_target(self, filename: str) -> Path:
        target = self.artifacts / sanitize_filename(filename)
        counter = 1
        while target.exists():
            target = self.artifacts / sanitize_filename(
                f"{target.stem}-{counter}{target.suffix}"
            )
            counter += 1
        return target

    def promote_artifact(self, produced: Path, filename: str) -> Path:
        """Publish a produced file into artifacts/ (write-then-register: the
        caller registers the DB row only after this succeeded). Atomic so a
        partial file is never visible as an artifact (INV-STORAGE-007/008)."""
        target = self._free_target(filename)
        publish_file(Path(produced), target)
        return target

    def promote_artifact_copy(self, existing: Path, filename: str) -> Path:
        """Copy an already-promoted artifact under another name (a mutualized
        step bound to several outputs promotes once, then copies)."""
        target = self._free_target(filename)
        shutil.copy2(str(existing), str(target))
        return target

    def purge_work(self) -> None:
        """Idempotent working-files cleanup (V1 retention behavior). Scoped to
        this job's work/ only — never touches artifacts (INV-STORAGE-004)."""
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True, exist_ok=True)

    def purge_tmp(self) -> None:
        """Remove this job's disposable scratch. Independent of work/ and
        artifacts/ (INV-STORAGE-003)."""
        shutil.rmtree(self.tmp, ignore_errors=True)


def safe_relative_folder(folder: str) -> Path:
    """Turn user ``folder`` intent into a safe *relative* path: each segment
    goes through the display profile (the library is user-facing — "talks
    2026" keeps its space) and ``.``/``..``/empty segments are dropped, so the
    result can never escape the delivery root."""
    parts = [
        cleaned
        for segment in folder.replace("\\", "/").split("/")
        if segment not in ("", ".", "..")
        for cleaned in (display_name(segment),)
        if cleaned
    ]
    return Path(*parts) if parts else Path()


class UploadStore:
    """The fifth storage root: bytes a client supplied (ADR 0020).

    Not tmp (that is per-job scratch), not work (per-job), not artifacts
    (produced results), not cache. An upload exists *before* any job, may feed
    several, and must survive a job's `purge_work`. Each lives alone under its
    own id so the stored name can never collide with another's:

        <uploads_root>/<upload_id>/<sanitized filename>

    The id is the address; the path is never shown to a client, which keeps the
    layout free to change and stops callers constructing their own.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def directory_for(self, upload_id: str) -> Path:
        return self.root / safe_segment(upload_id, "upload_id")

    def path_for(self, upload_id: str, filename: str) -> Path:
        return self.directory_for(upload_id) / sanitize_filename(filename)

    def write_stream(
        self, upload_id: str, filename: str, chunks, *, limit: int
    ) -> dict:
        """Stream *chunks* to disk under *upload_id*, enforcing *limit* as we go.

        The size is checked against what has actually been written, never
        against a declared Content-Length — a header is a claim, and this is
        the one endpoint where a stranger chooses the number. The file is
        written to `.part` and renamed only once the stream completed within
        the limit, so a partial or oversized upload is never addressable.
        """
        directory = self.directory_for(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(upload_id, filename)
        partial = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        written = 0
        try:
            with partial.open("wb") as handle:
                for chunk in chunks:
                    written += len(chunk)
                    if limit and written > limit:
                        raise UploadTooLarge(limit)
                    digest.update(chunk)
                    handle.write(chunk)
        except BaseException:
            partial.unlink(missing_ok=True)
            shutil.rmtree(directory, ignore_errors=True)
            raise
        os.replace(partial, target)
        return {
            "path": target,
            "size_bytes": written,
            "sha256": f"sha256:{digest.hexdigest()}",
        }

    def total_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def remove(self, upload_id: str) -> None:
        shutil.rmtree(self.directory_for(upload_id), ignore_errors=True)


class UploadTooLarge(Exception):
    """Raised mid-stream when an upload exceeds the configured limit."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"upload exceeds the {limit}-byte limit")


def _same_content(left: Path, right: Path) -> bool:
    """Are these two files byte-identical? Size first — it settles almost every
    comparison without reading a gigabyte off the disk — then sha256. An
    unreadable file answers "not identical", so delivery falls back to the
    counter rather than raising."""
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return checksum_sha256(left) == checksum_sha256(right)
    except OSError:
        return False


class DeliveryStore:
    """Delivers a copy of finished artifacts into a server-side library tree
    (``<root>/<folder>/<filename>``). The job artifact store stays the source
    of truth; this is an additional, browsable destination. Files land under
    the artifact's user-facing name (ADR 0017/0018); the store never invents
    one."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def deliver(self, source: Path, folder: str, filename: str) -> Path:
        """Copy ``source`` under ``<root>/<folder>/<filename>``. Returns the
        actual target.

        A name that is already taken is resolved in one of two ways, and the
        difference matters to anyone who re-runs a playlist:

        * **the file there is already this exact content** — same size, same
          sha256 — so nothing is copied and the existing path is returned. Re-
          submitting a download the library already holds must not litter it
          with ``…-1``, ``…-2`` clones of the same bytes.
        * **the name collides but the content differs** — two different videos
          that share a title — so the deterministic ``-1``, ``-2``… counter
          keeps both, which is the reason the counter exists.
        """
        target_dir = (self.root / safe_relative_folder(folder)).resolve()
        if self.root != target_dir and self.root not in target_dir.parents:
            raise ValueError("delivery target escapes the delivery root")
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix
        stem = display_name(Path(filename).stem) or "artifact"
        target = target_dir / f"{stem}{suffix}"
        counter = 1
        while target.exists():
            if _same_content(source, target):
                return target
            target = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        shutil.copy2(str(source), str(target))
        return target

    def list_folders(self) -> list[str]:
        """Existing sub-folders under the root, as sorted relative posix paths."""
        if not self.root.exists():
            return []
        folders = [
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_dir()
        ]
        return sorted(folders)
