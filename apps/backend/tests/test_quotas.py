"""What one owner may use of this installation.

Three limits, all off by default — a self-hosted instance is not a customer of
itself. The design rests on one choice worth guarding: media is counted in
**seconds of source**, known at analysis, so a refusal lands before the expense
rather than after it. Processing time would only be knowable afterwards, and
would charge twenty times more for a 4K video than for an audio clip at
identical service rendered.
"""

from dataclasses import replace

import pytest

from content.application import quotas
from content.domain.analysis import (
    CollectionEntry,
    NormalizedResource,
    ResourceAnalysis,
    SourceAnalysis,
)
from content.domain.errors import RequestRejected
from content.identity import LOCAL_OWNER

OWNER = "usr_someone"


def _analysis(*durations, collection=False):
    if collection:
        return ResourceAnalysis(
            analysis_id="a",
            created_at="x",
            sources=[
                SourceAnalysis(
                    source_id="s",
                    resource=NormalizedResource(resource_type="collection"),
                    entries=[CollectionEntry(duration_seconds=d) for d in durations],
                )
            ],
        )
    return ResourceAnalysis(
        analysis_id="a",
        created_at="x",
        sources=[
            SourceAnalysis(
                source_id=f"s{i}", resource=NormalizedResource(duration_seconds=d)
            )
            for i, d in enumerate(durations)
        ],
    )


# --- counting what is asked for -------------------------------------------------


def test_a_single_source_counts_its_duration():
    assert quotas.media_seconds_of(_analysis(630)) == 630


def test_several_sources_add_up():
    assert quotas.media_seconds_of(_analysis(60, 120)) == 180


def test_a_playlist_counts_every_member():
    """Asking for a playlist asks for every video in it."""
    assert quotas.media_seconds_of(_analysis(60, 120, 30, collection=True)) == 210


def test_an_unknown_duration_counts_as_nothing():
    """Refusing what cannot be measured would turn every unusual source into a
    support ticket."""
    assert quotas.media_seconds_of(_analysis(None)) == 0


# --- off by default -------------------------------------------------------------


def test_no_limit_is_set_by_default(settings, store):
    assert not quotas.Limits.from_settings(settings).any_set
    quotas.check(OWNER, store, settings, media_seconds=10**9)


def test_an_operator_is_never_counted(settings, store):
    """The quotas protect the installation from its users; the operator is the
    installation."""
    tight = replace(settings, quota_media_minutes_per_month=1)
    quotas.check(OWNER, store, tight, media_seconds=10**6, is_operator=True)


def test_self_hosted_local_is_exempt_through_being_the_operator(settings, store):
    tight = replace(settings, quota_media_minutes_per_month=1)
    quotas.check(
        LOCAL_OWNER,
        store,
        tight,
        media_seconds=10**6,
        is_operator=store.is_operator(LOCAL_OWNER),
    )


# --- each limit -----------------------------------------------------------------


def test_media_minutes_refuses_before_any_work(settings, store):
    tight = replace(settings, quota_media_minutes_per_month=30)
    # 40 minutes asked for in one go, with nothing used yet.
    with pytest.raises(RequestRejected) as refusal:
        quotas.check(OWNER, store, tight, media_seconds=40 * 60)
    issue = refusal.value.result.errors[0]
    assert issue.code == "quota_exceeded"
    assert issue.details["limit"] == "media_minutes"
    assert issue.details["allowed"] == 30


def test_media_minutes_accumulate_over_the_window(settings, store):
    tight = replace(settings, quota_media_minutes_per_month=30)
    job = store.create_job(OWNER, {"sources": []}, "fail_fast", None)
    store.record_media_seconds(OWNER, job, 25 * 60)

    # 4 more is fine, 10 more is not.
    quotas.check(OWNER, store, tight, media_seconds=4 * 60)
    with pytest.raises(RequestRejected):
        quotas.check(OWNER, store, tight, media_seconds=10 * 60)


def test_another_owners_minutes_are_not_yours(settings, store):
    tight = replace(settings, quota_media_minutes_per_month=30)
    job = store.create_job("usr_other", {"sources": []}, "fail_fast", None)
    store.record_media_seconds("usr_other", job, 29 * 60)
    quotas.check(OWNER, store, tight, media_seconds=29 * 60)


def test_a_failed_job_still_counts(settings, store):
    """It consumed the analysis and usually the download. Not counting it would
    make failure a way to get free capacity."""
    tight = replace(settings, quota_media_minutes_per_month=30)
    job = store.create_job(OWNER, {"sources": []}, "fail_fast", None)
    store.record_media_seconds(OWNER, job, 29 * 60)
    store.mark_job_failed(OWNER, job, "boom") if hasattr(
        store, "mark_job_failed"
    ) else None
    with pytest.raises(RequestRejected):
        quotas.check(OWNER, store, tight, media_seconds=5 * 60)


def test_concurrent_jobs_are_capped(settings, store):
    tight = replace(settings, quota_concurrent_jobs=1)
    store.create_job(OWNER, {"sources": []}, "fail_fast", None)
    with pytest.raises(RequestRejected) as refusal:
        quotas.check(OWNER, store, tight)
    assert refusal.value.result.errors[0].details["limit"] == "active_jobs"


def test_storage_is_a_ceiling_on_what_is_held(settings, store):
    tight = replace(settings, quota_storage_bytes=1000)
    held = settings.data_dir / "jobs" / OWNER / "job_1"
    held.mkdir(parents=True)
    (held / "a.bin").write_bytes(b"x" * 1500)
    with pytest.raises(RequestRejected) as refusal:
        quotas.check(OWNER, store, tight)
    assert refusal.value.result.errors[0].details["limit"] == "storage_bytes"


# --- seeing where you stand -----------------------------------------------------


def test_usage_is_readable_before_being_refused(settings, store):
    """A limit a person cannot watch themselves approach is a trap."""
    tight = replace(settings, quota_media_minutes_per_month=30, quota_concurrent_jobs=2)
    job = store.create_job(OWNER, {"sources": []}, "fail_fast", None)
    store.record_media_seconds(OWNER, job, 10 * 60)

    described = quotas.describe(OWNER, store, tight)
    assert described["media_minutes"] == {"used": 10.0, "allowed": 30}
    assert described["active_jobs"] == {"used": 1, "allowed": 2}
    # An unset limit says so rather than showing a misleading zero.
    assert described["storage_bytes"]["allowed"] is None


def test_an_exempt_owner_says_so(settings, store):
    described = quotas.describe(OWNER, store, settings, is_operator=True)
    assert described["exempt"] is True


# --- through the real submission path -------------------------------------------


def test_a_submission_over_the_limit_is_refused_and_creates_no_job(
    settings, store, providers
):
    """The property that matters: refused *before* the job exists, so nothing
    was spent and nothing has to be cleaned up."""
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from tests.conftest import make_request, minimal_payload

    tight = replace(settings, quota_concurrent_jobs=1)
    service = AnalysisService(store, providers, tight)
    payload = minimal_payload()

    first = submit_generation(
        LOCAL_OWNER,
        payload,
        make_request(payload),
        store=store,
        settings=tight,
        providers=providers,
        analysis_service=service,
    )
    assert first.job_id

    # `local` is the operator, so it is exempt — use a plain owner to be refused.
    before = len(store.list_jobs("usr_plain"))
    store.create_job("usr_plain", {"sources": []}, "fail_fast", None)
    with pytest.raises(RequestRejected) as refusal:
        submit_generation(
            "usr_plain",
            payload,
            make_request(payload),
            store=store,
            settings=tight,
            providers=providers,
            analysis_service=service,
        )
    assert refusal.value.result.errors[0].code == "quota_exceeded"
    # One job existed before the refusal, and still one after it.
    assert len(store.list_jobs("usr_plain")) == before + 1


def test_a_submission_records_the_media_it_asked_for(settings, store, providers):
    from content.analysis.service import AnalysisService
    from content.application.submit import submit_generation
    from tests.conftest import make_request, minimal_payload

    service = AnalysisService(store, providers, settings)
    payload = minimal_payload()
    result = submit_generation(
        "usr_counted",
        payload,
        make_request(payload),
        store=store,
        settings=settings,
        providers=providers,
        analysis_service=service,
    )
    # The fixture source has a duration, so the job carries it.
    assert store.media_seconds_since("usr_counted", "2000-01-01") > 0
    assert result.job_id
