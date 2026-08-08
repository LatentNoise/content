"""Storage boundaries: tmp != work != artifact != cache (ADR 0009).

Covers path safety, the tmp/work/artifact lifecycles, atomic publication and
the cache-disabled default (no inter-job reuse in V1).
"""

import pytest

from content.analysis.service import AnalysisService
from content.application.submit import submit_generation
from content.execution.executor import JobExecutor
from content.storage.layout import JobStorage
from content.storage.paths import StoragePaths, publish_file, safe_segment
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
        JobStorage(settings.data_dir, "../escape")


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


# --- job lifecycles (INV-STORAGE-003/004/005) ----------------------------------


def test_job_storage_creates_tmp_and_isolates_jobs(settings):
    a = JobStorage.from_settings(settings, "job_a").ensure()
    b = JobStorage.from_settings(settings, "job_b").ensure()
    assert a.tmp.is_dir() and a.tmp != b.tmp
    assert a.work != b.work
    assert a.step_tmp("s1").parent == a.tmp


def test_purge_tmp_and_work_keep_artifacts(settings):
    storage = JobStorage.from_settings(settings, "job_keep").ensure()
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
