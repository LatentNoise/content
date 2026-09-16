"""The refusal read structurally, and the sentence written for the limit hit.

Two things this guards, and they are the reasons the module exists rather than
each surface doing it inline:

- a refusal is recognised by its **stable code and its `details`**, never by
  matching prose — the message is written for a human and may be reworded;
- the three limits mean different things. Minutes age out on their own, storage
  needs a deletion, concurrency needs patience. One sentence for all three
  would be useless for two of them.
"""

from content_sdk.errors import APIError, error_for
from content_sdk.quota import refusal_of, sentence_for


def _refusal(limit, used, allowed):
    return {
        "detail": {
            "valid": False,
            "phase": "feasibility",
            "errors": [
                {
                    "code": "quota_exceeded",
                    "path": "execution",
                    "message": "this installation allows something",
                    "details": {"limit": limit, "used": used, "allowed": allowed},
                }
            ],
            "warnings": [],
        }
    }


# --- recognising it -------------------------------------------------------------


def test_a_quota_refusal_yields_its_numbers():
    exc = error_for(422, _refusal("media_minutes", 62.5, 60))
    assert refusal_of(exc) == {"limit": "media_minutes", "used": 62.5, "allowed": 60}


def test_another_refusal_is_not_a_quota_wall():
    body = {
        "detail": {
            "valid": False,
            "errors": [{"code": "url_not_supported", "message": "no"}],
        }
    }
    assert refusal_of(error_for(422, body)) is None


def test_a_transport_shaped_error_is_not_one_either():
    assert refusal_of(APIError(500, "gateway is on fire")) is None


def test_the_code_without_the_details_is_still_a_quota_refusal():
    """An older engine, or a shape we do not know: the wall still belongs,
    it just cannot be itemised."""
    body = {"detail": {"errors": [{"code": "quota_exceeded", "message": "no"}]}}
    assert refusal_of(error_for(422, body)) == {}


# --- saying the right thing about it --------------------------------------------


def test_minutes_say_they_free_themselves_up():
    said = sentence_for({"limit": "media_minutes", "used": 62, "allowed": 60}, "x")
    assert "60 minutes" in said
    assert "age out" in said


def test_storage_asks_for_a_deletion():
    said = sentence_for(
        {"limit": "storage_bytes", "used": 314_000_000, "allowed": 300_000_000}, "x"
    )
    assert "314 MB" in said
    assert "300 MB" in said
    assert "Delete" in said


def test_one_job_at_a_time_reassures_that_nothing_is_lost():
    said = sentence_for({"limit": "active_jobs", "used": 1, "allowed": 1}, "x")
    assert said.startswith("One job at a time")
    assert "nothing is lost" in said


def test_several_jobs_are_not_called_one():
    assert sentence_for(
        {"limit": "active_jobs", "used": 3, "allowed": 3}, "x"
    ).startswith("3 jobs at a time")


def test_an_unknown_limit_falls_back_to_the_engines_own_words():
    engine = "this installation allows something"
    assert sentence_for({"limit": "something_new", "allowed": 1}, engine) == engine
    assert sentence_for({}, engine) == engine
