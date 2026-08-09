"""Delivery: destination folder + filename intent, honored end-to-end.

Artifacts always live in the job store; when an output carries a `delivery`
block, the engine *additionally* drops a copy under the server delivery root.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from content.analysis.service import AnalysisService
from content.domain.request import Delivery
from content.execution.executor import JobExecutor
from content.storage.layout import DeliveryStore, safe_relative_folder
from tests.conftest import make_request, minimal_payload

# --- contract validation -------------------------------------------------------


def test_delivery_accepts_relative_folder_and_plain_filename():
    delivery = Delivery(folder="talks/2026", filename="my clip")
    assert delivery.folder == "talks/2026"
    assert delivery.filename == "my clip"


def test_delivery_defaults_are_empty():
    delivery = Delivery()
    assert delivery.folder == "" and delivery.filename == ""


@pytest.mark.parametrize("folder", ["../etc", "a/../../b", "a\\b", "."])
def test_delivery_rejects_unsafe_folder(folder):
    with pytest.raises(ValidationError):
        Delivery(folder=folder)


def test_delivery_normalizes_absolute_folder_to_relative():
    assert Delivery(folder="/abs/path").folder == "abs/path"


# D-51: an ordinary title contains separators ("Artist - Song / Official
# Video"); the server sanitizes name *intent* instead of rejecting it — the
# promise both docstrings always made.
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a/b", "a - b"),
        ("a\\b", "a - b"),
        ("Artist - Song / Official Video", "Artist - Song - Official Video"),
        ("..", ""),  # nothing survives: absent intent, not an error
        ("///", ""),
    ],
)
def test_delivery_sanitizes_filename_instead_of_rejecting(filename, expected):
    assert Delivery(filename=filename).filename == expected


def test_leading_and_trailing_slashes_are_trimmed():
    assert Delivery(folder="/talks/2026/").folder == "talks/2026"


# --- storage safety ------------------------------------------------------------


def test_safe_relative_folder_drops_traversal_segments():
    assert safe_relative_folder("a/../b").as_posix() == "a/b"
    assert safe_relative_folder("").as_posix() == "."


def test_delivery_store_copies_and_lists(tmp_path):
    root = tmp_path / "delivery"
    src = tmp_path / "source.mp4"
    src.write_bytes(b"payload")
    store = DeliveryStore(root)

    # The store receives the artifact's full display filename (ADR 0018) and
    # never invents a name; resolve() because DeliveryStore resolves its root.
    delivered = store.deliver(src, "talks/2026", "My Keynote.mp4")
    assert delivered == root.resolve() / "talks" / "2026" / "My Keynote.mp4"
    assert delivered.read_bytes() == b"payload"

    # Re-delivering the very same bytes resolves to the file already there:
    # a library must not accumulate clones when a download is re-run.
    again = store.deliver(src, "talks/2026", "My Keynote.mp4")
    assert again == delivered

    # A *different* file under a name already taken still gets a sibling.
    other = tmp_path / "other.mp4"
    other.write_bytes(b"a different payload")
    sibling = store.deliver(other, "talks/2026", "My Keynote.mp4")
    assert sibling.name == "My Keynote-1.mp4"

    assert store.list_folders() == ["talks", "talks/2026"]


# --- end-to-end delivery through the executor ----------------------------------


@pytest.fixture
def pipeline(store, providers, settings):
    from content.application.submit import submit_generation

    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def submit_and_run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=analysis_service,
        )
        claimed = store.claim_next_queued()
        executor.execute(claimed)
        return result.job_id

    return submit_and_run


def _delivery_root(settings):
    return settings.delivery_dir or (settings.data_dir / "delivery")


def test_output_with_delivery_is_copied_into_the_library(pipeline, store, settings):
    payload = minimal_payload(
        outputs=[
            {
                "id": "audio_main",
                "type": "audio",
                "delivery": {"folder": "podcasts", "filename": "episode-1"},
            }
        ]
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "succeeded"

    delivered = _delivery_root(settings) / "podcasts" / "episode-1.m4a"
    assert delivered.is_file() and delivered.read_bytes() == b"fake-audio-bytes"

    # the job artifact store remains the source of truth
    artifacts = store.list_artifacts(job_id)
    assert len(artifacts) == 1


def test_no_delivery_block_leaves_library_untouched(pipeline, store, settings):
    job_id = pipeline(minimal_payload())
    assert store.get_job(job_id)["status"] == "succeeded"
    assert not _delivery_root(settings).exists()


def test_subtitle_delivery_names_include_language(pipeline, store, settings):
    payload = minimal_payload(
        outputs=[
            {
                "id": "subs",
                "type": "subtitles",
                "options": {"languages": ["en", "fr"]},
                "delivery": {"folder": "subs", "filename": "talk"},
            }
        ]
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "succeeded"

    root = _delivery_root(settings) / "subs"
    names = sorted(p.name for p in root.iterdir())
    # Display-style names (ADR 0017): the client base keeps its qualifier-free
    # form because subtitles are this request's only output (primary).
    assert names == ["talk - en.srt", "talk - fr.srt"]


def test_delivery_without_filename_uses_the_display_name(pipeline, store, settings):
    # No client filename: the naming engine names the file after the analyzed
    # resource (ADR 0017) — never after the output id anymore.
    payload = minimal_payload(
        outputs=[{"id": "audio_main", "type": "audio", "delivery": {"folder": "loose"}}]
    )
    job_id = pipeline(payload)
    assert store.get_job(job_id)["status"] == "succeeded"
    assert (_delivery_root(settings) / "loose" / "Fake conference.m4a").is_file()
    artifact = store.list_artifacts(job_id)[0]
    assert artifact["delivered_path"] == "loose/Fake conference.m4a"


# --- the delivery policy (ADR 0018) --------------------------------------------


def _pipeline_with(store, providers, settings):
    from content.application.submit import submit_generation

    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def submit_and_run(payload: dict) -> str:
        request = make_request(payload)
        result = submit_generation(
            payload,
            request,
            store=store,
            settings=settings,
            providers=providers,
            analysis_service=analysis_service,
        )
        claimed = store.claim_next_queued()
        executor.execute(claimed)
        return result.job_id

    return submit_and_run


@pytest.fixture
def policy_on(store, providers, settings):
    settings = replace(settings, delivery_default=True)
    return _pipeline_with(store, providers, settings), settings


def test_policy_on_delivers_bare_requests_into_the_library_root(policy_on, store):
    pipeline, settings = policy_on
    job_id = pipeline(minimal_payload())  # no delivery block at all
    assert store.get_job(job_id)["status"] == "succeeded"
    delivered = _delivery_root(settings) / "Fake conference.m4a"
    assert delivered.is_file() and delivered.read_bytes() == b"fake-audio-bytes"
    artifact = store.list_artifacts(job_id)[0]
    assert artifact["delivered_path"] == "Fake conference.m4a"


def test_policy_on_mode_none_keeps_the_library_untouched(policy_on, store):
    pipeline, settings = policy_on
    job_id = pipeline(
        minimal_payload(
            outputs=[
                {"id": "audio_main", "type": "audio", "delivery": {"mode": "none"}}
            ]
        )
    )
    assert store.get_job(job_id)["status"] == "succeeded"
    assert not _delivery_root(settings).exists()
    assert store.list_artifacts(job_id)[0]["delivered_path"] == ""


def test_policy_off_mode_deliver_forces_the_copy(pipeline, store, settings):
    job_id = pipeline(
        minimal_payload(
            outputs=[
                {"id": "audio_main", "type": "audio", "delivery": {"mode": "deliver"}}
            ]
        )
    )
    assert store.get_job(job_id)["status"] == "succeeded"
    assert (_delivery_root(settings) / "Fake conference.m4a").is_file()


def test_mode_none_with_destination_is_contradictory_intent():
    with pytest.raises(ValidationError):
        Delivery(mode="none", filename="something")
    with pytest.raises(ValidationError):
        Delivery(mode="none", folder="somewhere")


def test_running_the_same_job_twice_delivers_one_file(policy_on, store):
    """The same request run twice points at the same library file.

    It used to record `Fake conference-1.m4a` for the second run — the counter
    fired on the name without ever asking whether the bytes differed, so a
    re-submitted playlist cloned the library. Identical content now resolves to
    the file already delivered, and both jobs report that same path.
    """
    pipeline, settings = policy_on
    first = pipeline(minimal_payload())
    second = pipeline(minimal_payload())
    assert store.list_artifacts(first)[0]["delivered_path"] == "Fake conference.m4a"
    assert store.list_artifacts(second)[0]["delivered_path"] == "Fake conference.m4a"
    root = _delivery_root(settings)
    assert (root / "Fake conference.m4a").is_file()
    assert not (root / "Fake conference-1.m4a").exists()


def test_resolved_delivery_is_visible_in_the_plan_snapshot(policy_on, store):
    import json as _json
    from pathlib import Path as _Path

    pipeline, settings = policy_on
    job_id = pipeline(minimal_payload())
    snapshot = _json.loads(
        (
            _Path(settings.data_dir) / "jobs" / job_id / "snapshots" / "plan.json"
        ).read_text()
    )
    assert snapshot["delivery"] == [
        {"output_id": "audio_main", "deliver": True, "folder": ""}
    ]


def test_the_three_paths_are_distinct_concepts(policy_on, store, settings):
    """One artifact, three names, three roles — none may collapse into another:

    - ``filename``: the technical name inside the job store (implementation
      detail, id-based, stable for addressing);
    - ``display_filename``: the user-facing name (ADR 0017), served as the
      download name whether or not any delivery happened;
    - ``delivered_path``: where the delivered *copy* landed, relative to the
      delivery root (ADR 0018) — ``""`` when there is no copy.
    """
    pipeline, run_settings = policy_on
    job_id = pipeline(
        minimal_payload(
            outputs=[
                {"id": "audio_main", "type": "audio", "delivery": {"folder": "loose"}}
            ]
        )
    )
    artifact = store.list_artifacts(job_id)[0]

    # Internal storage path: technical, id-based, physically present.
    assert artifact["filename"].startswith("audio_main")
    internal = (
        run_settings.data_dir / "jobs" / job_id / "artifacts" / artifact["filename"]
    )
    assert internal.is_file()

    # Display name: semantic, different from the technical name, and not a path.
    assert artifact["display_filename"] == "Fake conference.m4a"
    assert artifact["display_filename"] != artifact["filename"]
    assert "/" not in artifact["display_filename"]

    # Delivered path: relative to the library root, physically a *copy* there,
    # and distinct from both names (it carries the folder).
    assert artifact["delivered_path"] == "loose/Fake conference.m4a"
    assert not artifact["delivered_path"].startswith("/")
    assert str(run_settings.data_dir) not in artifact["delivered_path"]
    delivered = _delivery_root(run_settings) / artifact["delivered_path"]
    assert delivered.is_file()
    assert delivered.read_bytes() == internal.read_bytes()
    assert delivered != internal


# --- re-delivering the same content ---------------------------------------------


def test_delivering_identical_content_twice_keeps_one_file(tmp_path):
    """The library must not fill up with clones of the same bytes.

    Re-running a playlist re-delivers artifacts whose names already exist. The
    counter used to fire blindly, so a second run produced `Video-1.mkv`, a
    third `Video-2.mkv` — same video every time. Identical content now resolves
    to the file already there.
    """
    from content.storage.layout import DeliveryStore

    root = tmp_path / "library"
    store = DeliveryStore(root)
    source = tmp_path / "produced.mkv"
    source.write_bytes(b"the same video bytes")

    first = store.deliver(source, "", "My Video.mkv")
    second = store.deliver(source, "", "My Video.mkv")

    assert first == second
    assert first.name == "My Video.mkv"  # no "-1"
    assert [p.name for p in root.iterdir()] == ["My Video.mkv"]


def test_a_different_video_with_the_same_name_still_gets_a_counter(tmp_path):
    """The counter exists for a real case — two different videos sharing a
    title — and that case must keep working."""
    from content.storage.layout import DeliveryStore

    root = tmp_path / "library"
    store = DeliveryStore(root)
    first_source = tmp_path / "a.mkv"
    first_source.write_bytes(b"one video")
    other_source = tmp_path / "b.mkv"
    other_source.write_bytes(b"a different video entirely")

    first = store.deliver(first_source, "", "Same Title.mkv")
    second = store.deliver(other_source, "", "Same Title.mkv")

    assert first.name == "Same Title.mkv"
    assert second.name == "Same Title-1.mkv"
    assert sorted(p.name for p in root.iterdir()) == [
        "Same Title-1.mkv",
        "Same Title.mkv",
    ]


def test_same_size_different_bytes_is_not_treated_as_identical(tmp_path):
    """Size is only the cheap pre-filter; the checksum is what decides."""
    from content.storage.layout import DeliveryStore

    root = tmp_path / "library"
    store = DeliveryStore(root)
    first_source = tmp_path / "a.mkv"
    first_source.write_bytes(b"AAAAAAAAAA")
    other_source = tmp_path / "b.mkv"
    other_source.write_bytes(b"BBBBBBBBBB")  # same length, different content

    store.deliver(first_source, "", "Clash.mkv")
    second = store.deliver(other_source, "", "Clash.mkv")
    assert second.name == "Clash-1.mkv"
