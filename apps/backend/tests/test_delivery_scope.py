"""How the delivery library is shared: shared, per owner, or not at all.

ADR 0018 gave the engine one server-side library for everyone. With more than
one user that is a bug. The fix is a *policy*, never a question about the
deployment mode — nothing here asks "am I hosted", it asks what the delivery
policy is, which is what keeps ADR 0030's rule intact.

Note the asymmetry with `jobs/` and `tmp/`, which is deliberate: those are
internal folders nobody looks at, so an owner level costs nothing there.
`/output` is a library a human organises and a media server reads, so
`shared` stays the default and an existing self-hosted library never moves.
"""

from dataclasses import replace

import pytest

from content.analysis.service import AnalysisService
from content.domain import errors as codes
from content.domain.errors import RequestRejected
from content.execution.executor import JobExecutor
from content.identity import LOCAL_OWNER
from content.storage.layout import delivery_root_for
from tests.conftest import make_request, minimal_payload


def _a_signed_in_instance(monkeypatch) -> None:
    """The least a `token` instance needs to start: a public address and a
    cookie domain it sits under. Signing in refuses to start without them."""
    monkeypatch.setenv("CONTENT_AUTH_MODE", "token")
    monkeypatch.setenv("CONTENT_PUBLIC_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("CONTENT_SESSION_COOKIE_DOMAIN", ".example.test")


# --- resolving the root --------------------------------------------------------


def test_shared_is_the_default_and_is_the_plain_root(settings):
    assert settings.delivery_scope == "shared"
    assert delivery_root_for(settings, LOCAL_OWNER) == settings.data_dir / "delivery"


def test_the_default_scope_follows_the_mode(monkeypatch):
    """One library for everybody is right for one person and wrong the moment
    strangers sign in."""
    from content.config import settings_from_env

    monkeypatch.delenv("CONTENT_DELIVERY_SCOPE", raising=False)
    monkeypatch.setenv("CONTENT_AUTH_MODE", "none")
    assert settings_from_env().delivery_scope == "shared"
    _a_signed_in_instance(monkeypatch)
    assert settings_from_env().delivery_scope == "per_owner"


def test_a_household_can_still_ask_for_one_library(monkeypatch):
    """Several accounts, one library everyone reads: an explicit value wins."""
    from content.config import settings_from_env

    _a_signed_in_instance(monkeypatch)
    monkeypatch.setenv("CONTENT_DELIVERY_SCOPE", "shared")
    assert settings_from_env().delivery_scope == "shared"


def test_per_owner_puts_each_owner_in_their_own_subtree(settings):
    """A private library lives with the rest of the owner's files, under the
    per-user layout: one directory holds everything of one person (ADR 0037)."""
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    users = settings.data_dir / "users"
    assert delivery_root_for(scoped, LOCAL_OWNER) == users / "local" / "output"
    assert delivery_root_for(scoped, "usr_abc") == users / "usr_abc" / "output"


def test_per_owner_under_a_flat_tree_is_the_plain_library(settings):
    """A flat tree holds one owner, so `per_owner` has nobody to separate.
    It collapses to the library rather than inventing a `<root>/local/` level
    that would break every path a media server already reads."""
    scoped = replace(settings, delivery_scope="per_owner")
    assert delivery_root_for(scoped, LOCAL_OWNER) == settings.data_dir / "delivery"


def test_off_has_no_root_at_all(settings):
    assert (
        delivery_root_for(replace(settings, delivery_scope="off"), LOCAL_OWNER) is None
    )


@pytest.mark.parametrize("owner", ["..", "a/b", "", "x" * 200])
def test_an_owner_id_can_never_escape_its_prefix(settings, owner):
    scoped = replace(settings, delivery_scope="per_owner")
    with pytest.raises(ValueError):
        delivery_root_for(scoped, owner)


def test_an_unknown_policy_is_refused_at_startup(monkeypatch):
    from content.config import settings_from_env

    monkeypatch.setenv("CONTENT_DELIVERY_SCOPE", "somewhere")
    with pytest.raises(ValueError, match="CONTENT_DELIVERY_SCOPE"):
        settings_from_env()


# --- end to end ----------------------------------------------------------------


def _pipeline(settings, store, providers):
    from content.application.submit import submit_generation

    analysis_service = AnalysisService(store, providers, settings)
    executor = JobExecutor(store, settings, providers)

    def submit_and_run(payload: dict, owner: str = LOCAL_OWNER) -> str:
        request = make_request(payload)
        result = submit_generation(
            owner,
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


def _asking_for_delivery():
    return minimal_payload(
        outputs=[
            {
                "id": "audio_main",
                "type": "audio",
                "delivery": {"folder": "podcasts", "filename": "episode-1"},
            }
        ]
    )


def test_per_owner_delivers_under_the_owner(settings, store, providers):
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    run = _pipeline(scoped, store, providers)
    job_id = run(_asking_for_delivery())
    assert store.get_job(LOCAL_OWNER, job_id)["status"] == "succeeded"

    mine = scoped.data_dir / "users" / "local" / "output"
    assert (mine / "podcasts" / "episode-1.m4a").is_file()
    # and nothing landed in the shared position
    assert not (scoped.data_dir / "delivery").exists()


def test_the_recorded_path_stays_relative_to_the_owners_root(
    settings, store, providers
):
    """The owner prefix is where the file lives, not part of its address: a
    client asked for `podcasts/` and must read back `podcasts/`."""
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    run = _pipeline(scoped, store, providers)
    job_id = run(_asking_for_delivery())
    artifact = store.list_artifacts(LOCAL_OWNER, job_id)[0]
    assert artifact["delivered_path"] == "podcasts/episode-1.m4a"


def test_off_refuses_an_asked_for_delivery_rather_than_dropping_it(
    settings, store, providers
):
    run = _pipeline(replace(settings, delivery_scope="off"), store, providers)
    with pytest.raises(RequestRejected) as rejection:
        run(_asking_for_delivery())
    issue = rejection.value.result.errors[0]
    assert issue.code == codes.DELIVERY_NOT_SUPPORTED
    assert rejection.value.result.phase == "feasibility"


def test_off_runs_a_job_that_asked_for_nothing(settings, store, providers):
    off = replace(settings, delivery_scope="off")
    run = _pipeline(off, store, providers)
    job_id = run(minimal_payload())
    assert store.get_job(LOCAL_OWNER, job_id)["status"] == "succeeded"
    assert not (off.data_dir / "delivery").exists()


def test_off_ignores_the_server_side_default(settings, store, providers):
    """`delivery_default` is a policy, not a caller's intent — so it is simply
    not applied, rather than refused."""
    off = replace(settings, delivery_scope="off", delivery_default=True)
    run = _pipeline(off, store, providers)
    job_id = run(minimal_payload())
    assert store.get_job(LOCAL_OWNER, job_id)["status"] == "succeeded"
    assert not (off.data_dir / "delivery").exists()


def test_shared_is_unchanged(settings, store, providers):
    run = _pipeline(settings, store, providers)
    job_id = run(_asking_for_delivery())
    assert store.get_job(LOCAL_OWNER, job_id)["status"] == "succeeded"
    assert (settings.data_dir / "delivery" / "podcasts" / "episode-1.m4a").is_file()


# --- what a client is offered as destinations ----------------------------------


def _folders(settings, store, providers) -> list[str]:
    from fastapi.testclient import TestClient

    from content.api.app import create_app

    app = create_app(settings, store=store, providers=providers, start_worker=False)
    with TestClient(app) as client:
        return client.get("/api/v1/folders").json()["folders"]


def test_per_owner_offers_only_the_callers_own_folders(settings, store, providers):
    scoped = replace(settings, delivery_scope="per_owner", storage_layout="per_user")
    users = scoped.data_dir / "users"
    (users / "local" / "output" / "mine").mkdir(parents=True)
    (users / "usr_someone_else" / "output" / "theirs").mkdir(parents=True)

    offered = _folders(scoped, store, providers)
    assert offered == ["", "mine"]
    assert not any("theirs" in folder for folder in offered)


def test_off_offers_nothing(settings, store, providers):
    off = replace(settings, delivery_scope="off")
    assert _folders(off, store, providers) == []


def test_shared_offers_the_common_library(settings, store, providers):
    (settings.data_dir / "delivery" / "Tech").mkdir(parents=True)
    assert _folders(settings, store, providers) == ["", "Tech"]
