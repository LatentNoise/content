"""Two writers, one filename: the claim cannot be lost.

``JobStorage.promote_artifact`` and ``DeliveryStore.deliver`` both pick a free
name and then write to it. Between the look and the write another writer can
take the same name, and the loser's file is overwritten — one artifact, two
rows in the database, one of them pointing at somebody else's bytes.

The window is real with the shipped defaults: ``CONTENT_MAX_CONCURRENT_JOBS``
is 2, members inside a job run concurrently, and the delivery library is the
shared destination of all of them. These tests hold the invariant that closes
it — a name is taken by *creating* it, so every writer ends up with a distinct
file holding exactly its own bytes.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from content.storage.layout import DeliveryStore, JobStorage
from content.storage.paths import claim_path, claim_with, stage_beside

WRITERS = 8


def _payloads(tmp_path: Path, prefix: str) -> list[Path]:
    """One source file per writer, each with distinct, distinctly sized bytes
    so a truncated or overwritten result cannot pass as a correct one."""
    sources = []
    for index in range(WRITERS):
        path = tmp_path / f"{prefix}-{index}.src"
        path.write_bytes(f"writer-{index}".encode() + b"." * index)
        sources.append(path)
    return sources


def _race(work, count: int = WRITERS) -> list:
    """Run *work(index)* on *count* threads released at the same instant."""
    barrier = threading.Barrier(count)

    def run(index):
        barrier.wait()
        return work(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


# --- the primitive -------------------------------------------------------------


def test_a_claimed_path_cannot_be_claimed_twice(tmp_path):
    target = tmp_path / "one.mkv"
    assert claim_path(target) is True
    assert claim_path(target) is False  # deterministic: no timing involved
    assert target.exists()


def test_claim_creates_missing_parents(tmp_path):
    target = tmp_path / "deep" / "deeper" / "one.mkv"
    assert claim_path(target) is True
    assert target.exists()


def test_staging_then_claiming_leaves_only_the_final_file(tmp_path):
    source = tmp_path / "src.bin"
    source.write_bytes(b"payload")
    directory = tmp_path / "out"

    staged = stage_beside(source, directory)
    assert source.exists()  # a copy, not a move
    assert claim_with(staged, directory / "dst.bin") is True

    assert (directory / "dst.bin").read_bytes() == b"payload"
    assert [p.name for p in directory.iterdir()] == ["dst.bin"]


def test_claiming_a_taken_name_refuses_and_keeps_the_staged_file(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    (directory / "taken.bin").write_bytes(b"someone else")
    source = tmp_path / "src.bin"
    source.write_bytes(b"mine")

    staged = stage_beside(source, directory)
    assert claim_with(staged, directory / "taken.bin") is False

    assert (directory / "taken.bin").read_bytes() == b"someone else"
    assert staged.exists()  # still ours to place under the next candidate
    assert claim_with(staged, directory / "taken-1.bin") is True


def test_claim_falls_back_when_the_filesystem_has_no_hard_links(tmp_path, monkeypatch):
    """A NAS export or a FAT-family mount may refuse os.link. The name must
    still be claimed exclusively — with a rename-sized visible window instead
    of none at all."""
    directory = tmp_path / "out"
    source = tmp_path / "src.bin"
    source.write_bytes(b"payload")

    def no_links(*args, **kwargs):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr("content.storage.paths.os.link", no_links)
    staged = stage_beside(source, directory)
    assert claim_with(staged, directory / "dst.bin") is True
    assert (directory / "dst.bin").read_bytes() == b"payload"

    other = stage_beside(source, directory)
    assert claim_with(other, directory / "dst.bin") is False
    other.unlink()


# --- promotion into the job's artifact store -----------------------------------


def test_concurrent_promotion_gives_every_writer_its_own_file(tmp_path):
    storage = JobStorage(tmp_path / "data", "job_race").ensure()
    sources = _payloads(tmp_path, "promote")

    targets = _race(lambda i: storage.promote_artifact(sources[i], "Same Title.mkv"))

    assert len({t.resolve() for t in targets}) == WRITERS, "two writers shared a name"
    for index, target in enumerate(targets):
        expected = f"writer-{index}".encode() + b"." * index
        assert target.read_bytes() == expected, f"{target.name} was overwritten"


def test_concurrent_copies_of_one_artifact_do_not_collide(tmp_path):
    """The mutualized-step path: one produced file promoted under N names."""
    storage = JobStorage(tmp_path / "data", "job_copies").ensure()
    origin = tmp_path / "origin.mkv"
    origin.write_bytes(b"one source of truth")

    targets = _race(lambda i: storage.promote_artifact_copy(origin, "Shared.mkv"))

    assert len({t.resolve() for t in targets}) == WRITERS
    assert all(t.read_bytes() == b"one source of truth" for t in targets)


def test_promotion_failure_does_not_reserve_the_name(tmp_path):
    """A claim is an empty file. If the write that was going to fill it fails,
    the name must be free again — otherwise a transient error permanently
    renames every later artifact to ``…-1``."""
    storage = JobStorage(tmp_path / "data", "job_fail").ensure()
    missing = tmp_path / "not-there.mkv"

    try:
        storage.promote_artifact(missing, "Wanted.mkv")
    except OSError:
        pass
    else:  # pragma: no cover - the source really is absent
        raise AssertionError("promoting a missing file should raise")

    assert not (storage.artifacts / "Wanted.mkv").exists()
    produced = tmp_path / "real.mkv"
    produced.write_bytes(b"real")
    assert storage.promote_artifact(produced, "Wanted.mkv").name == "Wanted.mkv"


# --- delivery into the shared library -------------------------------------------


def test_concurrent_delivery_of_different_content_keeps_every_file(tmp_path):
    store = DeliveryStore(tmp_path / "library")
    sources = _payloads(tmp_path, "deliver")

    targets = _race(lambda i: store.deliver(sources[i], "talks", "Same Title.mkv"))

    assert len({t.resolve() for t in targets}) == WRITERS, "a delivery was lost"
    for index, target in enumerate(targets):
        expected = f"writer-{index}".encode() + b"." * index
        assert target.read_bytes() == expected, f"{target.name} was overwritten"
    # Nothing was left half-written, and no staging file survived.
    names = sorted(p.name for p in (store.root / "talks").iterdir())
    assert names == sorted(t.name for t in targets)


def test_identical_bytes_still_deliver_once(tmp_path):
    """The dedup rule survives the claim: re-delivering the same bytes under a
    taken name returns the existing path instead of making a ``-1`` clone."""
    store = DeliveryStore(tmp_path / "library")
    source = tmp_path / "same.mkv"
    source.write_bytes(b"identical bytes")

    first = store.deliver(source, "talks", "Talk.mkv")
    second = store.deliver(source, "talks", "Talk.mkv")

    assert first == second
    assert [p.name for p in (store.root / "talks").iterdir()] == ["Talk.mkv"]


def test_delivery_never_publishes_a_partial_file(tmp_path):
    """A reader watching the folder sees whole files only: the copy is staged
    beside the target and renamed onto it."""
    store = DeliveryStore(tmp_path / "library")
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (2 * 1024 * 1024))
    seen: list[int] = []
    stop = threading.Event()

    def watch():
        folder = store.root / "talks"
        while not stop.is_set():
            for path in folder.glob("*.bin") if folder.exists() else ():
                try:
                    seen.append(path.stat().st_size)
                except OSError:
                    pass

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    delivered = store.deliver(source, "talks", "Big.bin")
    stop.set()
    watcher.join(timeout=5)

    assert delivered.stat().st_size == 2 * 1024 * 1024
    assert all(size == 2 * 1024 * 1024 for size in seen), "a partial file was visible"
