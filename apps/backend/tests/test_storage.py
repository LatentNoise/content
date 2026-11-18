"""Storage boundaries: tmp != work != artifact != cache (ADR 0009).

Covers path safety, the tmp/work/artifact lifecycles, atomic publication and
the cache-disabled default (no inter-job reuse in V1).
"""

import errno
from dataclasses import replace

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.execution.executor import JobExecutor
from content.identity import LOCAL_OWNER
from content.storage.layout import JobStorage
from content.storage.paths import (
    StoragePaths,
    publish_file,
    safe_segment,
    stage_beside,
    storage_report,
)
from content.storage.roots import owner_roots, shared_roots
from tests.conftest import make_request, minimal_payload

# --- path safety (INV-STORAGE-006) ---------------------------------------------


@pytest.mark.parametrize("bad", ["..", ".", "", "a/b", "a\\b", "../etc", "x/../y"])
def test_safe_segment_rejects_unsafe_ids(bad):
    with pytest.raises(ValueError):
        safe_segment(bad)


@pytest.mark.parametrize("ok", ["job_123", "step-a.1", "art_456", "acquire_main"])
def test_safe_segment_accepts_controlled_ids(ok):
    assert safe_segment(ok) == ok


def test_temporary_operation_dir_stays_under_tmp_root(settings):
    paths = StoragePaths.from_settings(settings)
    op = paths.temporary_operation_dir("job_1", "step_1", "op_1")
    assert paths.tmp_root.resolve() in op.resolve().parents


def test_temporary_operation_dir_rejects_traversal(settings):
    paths = StoragePaths.from_settings(settings)
    with pytest.raises(ValueError):
        paths.temporary_operation_dir("job_1", "..", "op_1")


def test_job_storage_rejects_unsafe_job_id(settings):
    with pytest.raises(ValueError):
        JobStorage.from_settings(settings, LOCAL_OWNER, "../escape")


# --- roots + cache disabled (INV-STORAGE-009/010) ------------------------------


def test_roots_are_derived_from_data_dir(settings):
    paths = StoragePaths.from_settings(settings)
    assert paths.tmp_root == settings.data_dir / "tmp"
    assert paths.cache_root == settings.data_dir / "cache"
    assert paths.cache_enabled is False


def test_ensure_cache_refused_and_absent_when_disabled(settings):
    paths = StoragePaths.from_settings(settings)
    with pytest.raises(RuntimeError):
        paths.ensure_cache()
    assert not paths.cache_root.exists()  # no empty cache/ left behind


# --- atomic publication (INV-STORAGE-007/008) ----------------------------------


def test_publish_file_moves_and_removes_source(tmp_path):
    src = tmp_path / "work" / "out.bin"
    src.parent.mkdir()
    src.write_bytes(b"payload")
    dest = tmp_path / "artifacts" / "final.bin"

    published = publish_file(src, dest)
    assert published == dest
    assert dest.read_bytes() == b"payload"
    assert not src.exists()  # published by move, not copy


def test_publish_file_refuses_silent_overwrite(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"new")
    dest = tmp_path / "b.bin"
    dest.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        publish_file(src, dest)
    assert dest.read_bytes() == b"old"
    assert src.exists()  # nothing published, source untouched


def test_publish_file_cross_filesystem_fallback(tmp_path, monkeypatch):
    """When os.replace fails (cross-FS), a temp-then-rename keeps atomicity and
    never leaves a partial file at the destination."""
    import content.storage.paths as paths_mod

    src = tmp_path / "src.bin"
    src.write_bytes(b"cross-fs")
    dest = tmp_path / "sub" / "dest.bin"

    real_replace = paths_mod.os.replace
    calls = {"n": 0}

    def flaky_replace(a, b):
        calls["n"] += 1
        if calls["n"] == 1:  # first call = the direct move
            raise OSError("EXDEV")
        return real_replace(a, b)  # the staged rename succeeds

    monkeypatch.setattr(paths_mod.os, "replace", flaky_replace)
    publish_file(src, dest)
    assert dest.read_bytes() == b"cross-fs"
    assert not src.exists()
    assert not (dest.parent / f".{dest.name}.partial").exists()


def _refusing_copystat():
    """What a CIFS/SMB mount does to ``os.utime``."""

    def refuse(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    return refuse


def _flaky_replace(paths_mod):
    """os.replace that fails the direct move once, then behaves."""
    real_replace = paths_mod.os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] == 1:  # the direct move, as across a mount point
            raise OSError(errno.EXDEV, "Cross-device link")
        return real_replace(a, b)  # the staged rename succeeds

    return flaky


def test_publish_file_survives_a_destination_that_refuses_timestamps(
    tmp_path, monkeypatch
):
    """A CIFS/SMB share refuses ``utime`` outright, and the copy staged beside
    the destination lives *on that share*. ``copy2`` is ``copyfile`` plus
    ``copystat``, so the bytes landed and the call raised anyway — a timestamp
    failing a finished copy, and no artifact publishable to such a mount.

    Preserved metadata is worth having and is not worth a failed delivery.
    """
    import content.storage.paths as paths_mod

    src = tmp_path / "src.bin"
    src.write_bytes(b"finished media")
    dest = tmp_path / "share" / "dest.bin"

    monkeypatch.setattr(paths_mod.os, "replace", _flaky_replace(paths_mod))
    monkeypatch.setattr(
        paths_mod.shutil,
        "copystat",
        _refusing_copystat(),
    )

    publish_file(src, dest)

    assert dest.read_bytes() == b"finished media"
    assert not src.exists()  # consumed, as on any successful publish
    assert not (dest.parent / f".{dest.name}.partial").exists()


def test_stage_beside_survives_a_destination_that_refuses_timestamps(
    tmp_path, monkeypatch
):
    """The same defect on the copy branch of ``stage_beside``: it stages into
    the caller's directory, which is the share."""
    import content.storage.paths as paths_mod

    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    monkeypatch.setattr(paths_mod.shutil, "copystat", _refusing_copystat())

    staged = stage_beside(src, tmp_path / "share", move=False)

    assert staged.read_bytes() == b"payload"
    assert src.exists()  # move=False leaves the source alone
    assert staged.parent == tmp_path / "share"


def test_the_cross_filesystem_publish_still_preserves_timestamps(tmp_path, monkeypatch):
    """The fix tolerates a refusal; it must not stop *asking*. Dropping
    ``copystat`` everywhere would quietly lose mtimes on every filesystem that
    was happy to keep them."""
    import content.storage.paths as paths_mod

    src = tmp_path / "src.bin"
    src.write_bytes(b"cross-fs")
    dest = tmp_path / "sub" / "dest.bin"

    seen: list[str] = []
    real_copystat = paths_mod.shutil.copystat

    def recording_copystat(a, b, **kwargs):
        seen.append(str(b))
        return real_copystat(a, b, **kwargs)

    monkeypatch.setattr(paths_mod.os, "replace", _flaky_replace(paths_mod))
    monkeypatch.setattr(paths_mod.shutil, "copystat", recording_copystat)

    publish_file(src, dest)

    assert seen, "copystat was never attempted; timestamps are no longer preserved"
    assert dest.read_bytes() == b"cross-fs"


# --- job lifecycles (INV-STORAGE-003/004/005) ----------------------------------


def test_job_storage_creates_tmp_and_isolates_jobs(settings):
    a = JobStorage.from_settings(settings, LOCAL_OWNER, "job_a").ensure()
    b = JobStorage.from_settings(settings, LOCAL_OWNER, "job_b").ensure()
    assert a.tmp.is_dir() and a.tmp != b.tmp
    assert a.work != b.work
    assert a.step_tmp("s1").parent == a.tmp


def test_purge_tmp_and_work_keep_artifacts(settings):
    storage = JobStorage.from_settings(settings, LOCAL_OWNER, "job_keep").ensure()
    (storage.work / "inter.bin").write_bytes(b"i")
    (storage.tmp / "scratch.part").write_bytes(b"p")
    art = storage.promote_artifact(_produced(storage.work, b"final"), "final.bin")

    storage.purge_tmp()
    storage.purge_work()

    assert not storage.tmp.exists()
    assert not any(storage.work.iterdir())
    assert art.is_file() and art.read_bytes() == b"final"  # artifact survives


def _produced(workdir, data: bytes):
    path = workdir / "produced.tmp"
    path.write_bytes(data)
    return path


# --- cache disabled end-to-end (ADR 0009) --------------------------------------


@pytest.fixture
def pipeline(store, providers, settings):
    service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def run(payload: dict):
        request = make_request(payload)
        result = submit_generation(
            LOCAL_OWNER,
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=service,
        )
        executor.execute(store.claim_next_queued())
        return result

    return run


def test_identical_jobs_both_run_when_cache_disabled(pipeline, providers):
    pipeline(minimal_payload())
    pipeline(minimal_payload())
    # default settings → cache disabled → the operation ran for both jobs
    fake = providers.for_source(make_request(minimal_payload()).sources[0])
    assert fake.executed_operations == ["media.acquire_audio"] * 2


def test_reuse_existing_true_warns_when_cache_disabled(pipeline):
    result = pipeline(minimal_payload())  # reuse_existing defaults to true
    codes = {w.code for w in result.warnings}
    assert "reuse_unavailable" in codes


def test_no_reuse_warning_when_reuse_disabled(pipeline):
    result = pipeline(minimal_payload(execution={"reuse_existing": False}))
    codes = {w.code for w in result.warnings}
    assert "reuse_unavailable" not in codes


# --- the installation-wide report measures where the layout puts things --------
#
# `storage_report` answers "why is my disk full" for the operator console. It
# used to read the flat tree's locations whatever the layout, so on a per-user
# installation — the only kind that has several owners, and the one the hosted
# engine runs — uploads, tmp and a per-owner library all reported **0 bytes**
# while their bytes sat under `users/<owner>/`, counted inside `jobs` because
# that family measured the whole `users/` tree.


def _occupy(settings, owner: str) -> None:
    """One owner's five families, each with a distinguishable size."""
    roots = owner_roots(settings, owner)
    _write(roots.jobs / "job_1" / "artifacts" / "out.mp4", 2000)
    _write(roots.tmp / "job_1" / "download.part", 500)
    _write(roots.uploads / "upl_1" / "film.mp4", 1000)
    if roots.output is not None:
        _write(roots.output / "Film.mp4", 300)


def _write(path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


@pytest.fixture
def hosted(settings):
    """What `CONTENT_AUTH_MODE=token` forces: several owners, one tree each."""
    return replace(settings, storage_layout="per_user", delivery_scope="per_owner")


def test_report_finds_uploads_in_the_per_user_layout(hosted):
    _occupy(hosted, "usr_alice")
    _occupy(hosted, "usr_bob")
    uploads = storage_report(hosted)["uploads"]
    assert uploads["bytes"] == 2000  # 1000 each, not 0
    assert uploads["count"] == 2


def test_report_finds_tmp_and_the_library_in_the_per_user_layout(hosted):
    _occupy(hosted, "usr_alice")
    _occupy(hosted, "usr_bob")
    report = storage_report(hosted)
    assert report["tmp"]["bytes"] == 1000
    assert report["delivery"]["bytes"] == 600


def test_jobs_no_longer_swallows_the_other_families(hosted):
    """The symptom that made the three zeros above hard to notice: `jobs`
    measured `users/`, so the missing bytes were silently inside it."""
    _occupy(hosted, "usr_alice")
    _occupy(hosted, "usr_bob")
    jobs = storage_report(hosted)["jobs"]
    assert jobs["bytes"] == 4000  # the artifacts alone, not 7600
    assert jobs["files"] == 2


def test_a_tmp_directory_named_after_its_job_is_not_counted_as_a_job(hosted):
    """`tmp/<job_id>/` shares the job's name, so a report that walked `users/`
    for `job_*` counted every job twice."""
    _occupy(hosted, "usr_alice")
    assert storage_report(hosted)["jobs"]["count"] == 1


def test_report_names_the_owner_wildcard_for_a_spread_family(hosted):
    _occupy(hosted, "usr_alice")
    _occupy(hosted, "usr_bob")
    report = storage_report(hosted)
    assert report["uploads"]["path"] == str(hosted.data_dir / "users" / "*" / "uploads")
    assert report["jobs"]["path"] == str(hosted.data_dir / "users" / "*" / "jobs")
    # The cache belongs to nobody in either layout: one real path, no wildcard.
    assert report["cache"]["path"] == str(hosted.data_dir / "cache")


def test_a_shared_library_is_measured_once_not_once_per_owner(settings):
    """`delivery_scope: shared` gives every owner the *same* directory, so
    summing per owner would multiply the library by the number of accounts."""
    shared = replace(settings, storage_layout="per_user", delivery_scope="shared")
    _occupy(shared, "usr_alice")
    _occupy(shared, "usr_bob")
    assert storage_report(shared)["delivery"]["bytes"] == 300


def test_the_shared_analysis_scratch_is_counted_once_under_flat_tmp(settings):
    """Flat puts the shared probe scratch *inside* the one tmp root; counting
    the two directories separately would double it."""
    _write(shared_roots(settings).tmp_analysis / "probe.json", 70)
    assert storage_report(settings)["tmp"]["bytes"] == 70


def test_a_directory_no_owner_id_could_have_produced_is_skipped(hosted):
    """Measuring is never worth failing for (the `_dir_stats` rule)."""
    _occupy(hosted, "usr_alice")
    (hosted.data_dir / "users" / "..stray").mkdir(parents=True)
    assert storage_report(hosted)["uploads"]["bytes"] == 1000


def test_the_flat_report_is_unchanged_by_all_of_this(settings):
    """The self-hosted majority: one owner, the historical locations."""
    _occupy(settings, LOCAL_OWNER)
    report = storage_report(settings)
    assert report["jobs"]["path"] == str(settings.data_dir / "jobs")
    assert report["uploads"]["path"] == str(settings.data_dir / "uploads")
    assert report["tmp"]["path"] == str(settings.data_dir / "tmp")
    assert report["jobs"]["bytes"] == 2000
    assert report["tmp"]["bytes"] == 500
    assert report["uploads"]["bytes"] == 1000


def test_a_switched_off_library_still_reports_what_was_delivered_before(settings):
    """`delivery_scope: off` gives no owner a library, but bytes an earlier
    configuration delivered are still on the disk, and "why is my disk full"
    has to account for them."""
    off = replace(settings, storage_layout="per_user", delivery_scope="off")
    _occupy(off, "usr_alice")
    _write(off.data_dir / "delivery" / "Film.mp4", 300)
    report = storage_report(off)
    assert report["delivery"]["bytes"] == 300
    assert report["delivery"]["path"] == str(off.data_dir / "delivery")
