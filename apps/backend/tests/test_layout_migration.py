"""Filing an existing disk into the configured layout (ADR 0037).

Three shapes can be found on a disk that ran earlier versions, and the pass has
to bring each of them to either target without losing a byte. The properties
guarded here are the ones a migration that runs unattended on real data must
have: idempotent, interruptible, conservative about what it touches.
"""

from dataclasses import replace

from content.identity import LOCAL_OWNER
from content.persistence.store import Store
from content.storage.migrate_layout import migrate_layout
from content.storage.roots import owner_roots


def _store(tmp_path) -> Store:
    return Store(tmp_path / "db.sqlite")


def _touch(path, content=b"bytes that must survive"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- to flat, the self-hosted majority --------------------------------------------


def test_the_owner_level_of_0_8_0_comes_back_up_in_a_flat_tree(settings, tmp_path):
    """The shape every 0.8.0/0.8.1 install has today: jobs/local/<job>."""
    data = settings.data_dir
    _touch(data / "jobs" / LOCAL_OWNER / "job_a" / "artifacts" / "a.mp4")
    (data / "tmp" / LOCAL_OWNER / "job_a").mkdir(parents=True)

    result = migrate_layout(settings, _store(tmp_path))

    assert result["layout"] == "flat" and result["moved"] == 2
    assert (data / "jobs" / "job_a" / "artifacts" / "a.mp4").read_bytes() == (
        b"bytes that must survive"
    )
    assert (data / "tmp" / "job_a").is_dir()
    # The now-empty owner directories are gone; a flat tree has no owner level.
    assert not (data / "jobs" / LOCAL_OWNER).exists()


def test_a_pre_ownership_tree_is_already_flat(settings, tmp_path):
    data = settings.data_dir
    _touch(data / "jobs" / "job_old" / "artifacts" / "a.mp4")
    result = migrate_layout(settings, _store(tmp_path))
    assert result["moved"] == 0
    assert (data / "jobs" / "job_old" / "artifacts" / "a.mp4").exists()


def test_a_per_user_tree_collapses_onto_local_when_switched_back(settings, tmp_path):
    """An instance that tried per_user and returned to flat. Only one owner can
    be there — flat with sign-in is refused before this runs."""
    data = settings.data_dir
    _touch(data / "users" / LOCAL_OWNER / "jobs" / "job_x" / "artifacts" / "x.bin")
    _touch(data / "users" / LOCAL_OWNER / "uploads" / "upl_1" / "f.txt")

    migrate_layout(settings, _store(tmp_path))

    assert (data / "jobs" / "job_x" / "artifacts" / "x.bin").exists()
    assert (data / "uploads" / "upl_1" / "f.txt").exists()
    assert not (data / "users").exists()


# --- to per_user, the hosted instance --------------------------------------------


def test_ownerless_jobs_are_filed_under_local(settings, tmp_path):
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    _touch(data / "jobs" / "job_old" / "artifacts" / "a.mp4")

    result = migrate_layout(per_user, _store(tmp_path))

    assert result["moved"] == 1
    expected = owner_roots(per_user, LOCAL_OWNER).job("job_old") / "artifacts" / "a.mp4"
    assert expected.read_bytes() == b"bytes that must survive"
    assert not (data / "jobs").exists()


def test_the_0_8_0_owner_level_moves_under_users(settings, tmp_path):
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    _touch(data / "jobs" / LOCAL_OWNER / "job_a" / "artifacts" / "a.mp4")
    _touch(data / "jobs" / "usr_bob" / "job_b" / "artifacts" / "b.mp4")
    (data / "tmp" / "usr_bob" / "job_b").mkdir(parents=True)

    result = migrate_layout(per_user, _store(tmp_path))

    assert result["moved"] == 3
    assert (
        data / "users" / LOCAL_OWNER / "jobs" / "job_a" / "artifacts" / "a.mp4"
    ).exists()
    assert (
        data / "users" / "usr_bob" / "jobs" / "job_b" / "artifacts" / "b.mp4"
    ).exists()
    assert (data / "users" / "usr_bob" / "tmp" / "job_b").is_dir()


def test_uploads_are_filed_under_the_owner_the_row_names(settings, tmp_path):
    """Uploads never had an owner level on disk; only the database knows."""
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    store = _store(tmp_path)
    bob_file = _touch(data / "uploads" / "upl_bob" / "notes.md", b"bob's")
    _touch(data / "uploads" / "upl_orphan" / "x.bin", b"nobody's")
    store.register_upload(
        "usr_bob",
        {
            "id": "upl_bob",
            "filename": "notes.md",
            "path": str(bob_file),
            "size_bytes": 5,
        },
    )

    migrate_layout(per_user, store)

    assert (
        data / "users" / "usr_bob" / "uploads" / "upl_bob" / "notes.md"
    ).read_bytes() == (b"bob's")
    # An upload no row claims belongs to the only owner a row-less disk can have.
    assert (data / "users" / LOCAL_OWNER / "uploads" / "upl_orphan" / "x.bin").exists()
    assert not (data / "uploads").exists()


def test_an_uploads_recorded_path_is_derived_after_the_move(settings, tmp_path):
    """The row still says the old absolute path; resolution must not trust it."""
    from content.application.uploads import upload_path

    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    store = _store(tmp_path)
    old = _touch(data / "uploads" / "upl_1" / "f.txt", b"payload")
    store.register_upload(
        LOCAL_OWNER,
        {"id": "upl_1", "filename": "f.txt", "path": str(old), "size_bytes": 7},
    )
    migrate_layout(per_user, store)

    row = store.get_upload(LOCAL_OWNER, "upl_1")
    assert row["path"] == str(old)  # untouched, and now stale
    assert upload_path(LOCAL_OWNER, row, per_user).read_bytes() == b"payload"


# --- the properties every unattended migration needs ------------------------------


def test_running_twice_moves_nothing_the_second_time(settings, tmp_path):
    per_user = replace(settings, storage_layout="per_user")
    _touch(per_user.data_dir / "jobs" / LOCAL_OWNER / "job_a" / "artifacts" / "a")
    store = _store(tmp_path)
    assert migrate_layout(per_user, store)["moved"] == 1
    assert migrate_layout(per_user, store)["moved"] == 0
    # Never filed under itself.
    assert not (per_user.data_dir / "users" / LOCAL_OWNER / "users").exists()


def test_an_interrupted_run_finishes_on_the_next_one(settings, tmp_path):
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    _touch(data / "users" / LOCAL_OWNER / "jobs" / "job_done" / "artifacts" / "a")
    _touch(data / "jobs" / LOCAL_OWNER / "job_pending" / "artifacts" / "b")

    assert migrate_layout(per_user, _store(tmp_path))["moved"] == 1
    assert (data / "users" / LOCAL_OWNER / "jobs" / "job_done").is_dir()
    assert (data / "users" / LOCAL_OWNER / "jobs" / "job_pending").is_dir()


def test_a_leftover_source_is_dropped_when_the_destination_holds_it(settings, tmp_path):
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    kept = _touch(
        data / "users" / LOCAL_OWNER / "jobs" / "job_dup" / "artifacts" / "kept"
    )
    (data / "jobs" / LOCAL_OWNER / "job_dup").mkdir(parents=True)

    migrate_layout(per_user, _store(tmp_path))

    assert kept.exists()
    assert not (data / "jobs" / LOCAL_OWNER / "job_dup").exists()


def test_a_stray_directory_is_left_where_it_is(settings, tmp_path):
    """Something that is not an owner id is somebody's folder, not ours."""
    per_user = replace(settings, storage_layout="per_user")
    data = per_user.data_dir
    stray = _touch(data / "jobs" / "my notes" / "todo.txt", b"keep")
    migrate_layout(per_user, _store(tmp_path))
    assert stray.exists()


def test_nothing_to_migrate_on_a_fresh_install(settings, tmp_path):
    result = migrate_layout(settings, _store(tmp_path))
    assert result["moved"] == 0 and result["failed"] == 0
