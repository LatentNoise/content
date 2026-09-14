"""`APIError.message`: the sentence written for the person, never a dict dump.

Studio used to show `Request refused: {'detail': {'errors': [...]}}` — the
contract's messages are written to be read, and printing the body threw that
away. One extractor on the error itself, so no surface keeps its own (D-21).
"""

from content_sdk.errors import APIError

QUOTA = {
    "detail": {
        "ok": False,
        "phase": "feasibility",
        "errors": [
            {
                "code": "quota_exceeded",
                "path": "execution",
                "message": "You have used 60 of 60 media minutes this month.",
                "details": {"limit": "media_minutes", "used": 60, "allowed": 60},
            }
        ],
        "warnings": [],
    }
}


def test_the_contracts_message_is_shown_as_written():
    assert APIError(422, QUOTA).message == (
        "You have used 60 of 60 media minutes this month."
    )


def test_several_issues_are_joined():
    body = {"detail": {"errors": [{"message": "one"}, {"message": "two"}]}}
    assert APIError(422, body).message == "one · two"


def test_a_single_code_and_message_body():
    body = {"detail": {"code": "artifact_content_expired", "message": "Expired on…"}}
    assert APIError(410, body).message == "Expired on…"


def test_a_plain_string_detail_is_the_message():
    assert APIError(404, {"detail": "artifact not found"}).message == (
        "artifact not found"
    )


def test_nothing_readable_falls_back_to_the_status():
    assert APIError(500, {"weird": 1}).message == "HTTP 500"
    assert APIError(502, "gateway text").message == "HTTP 502"
