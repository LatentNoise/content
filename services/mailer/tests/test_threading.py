"""Every sign-in link is its own conversation.

The bug this pins: a person asked for a second link — another browser, a lost
session — and "received nothing". Every message had been delivered. Gmail had
grouped them by sender and subject, so each new link landed inside the
conversation of the first one, under a message already read or archived, where
nobody looks. Identical subjects made the mail impossible to find.

Two defences, because they cover different clients: a subject that differs
from the last one, which every client respects, and an `X-Entity-Ref-ID`
header, which is what Gmail reads to keep transactional mail apart.
"""

from mailer.sender import build_email
from mailer.store import Message
from mailer.templates import new_reference, render

VARIABLES = {"link": "https://x.test/t", "product": "HomeTube", "minutes": "15"}


def test_two_links_for_the_same_person_never_share_a_subject():
    first = render("magic-link", VARIABLES).subject
    second = render("magic-link", VARIABLES).subject
    assert first != second
    assert first.startswith("Your HomeTube sign-in link · ")


def test_the_callers_reference_is_the_one_shown():
    """The engine prints the same code on its "check your mail" page, so
    someone holding several links can tell which one they just asked for."""
    rendered = render("magic-link", {**VARIABLES, "reference": "7KQ3"})
    assert rendered.subject == "Your HomeTube sign-in link · 7KQ3"
    assert "Reference 7KQ3" in rendered.text
    assert rendered.html and "Reference 7KQ3" in rendered.html


def test_a_reference_reads_aloud_without_ambiguity():
    for _ in range(200):
        code = new_reference()
        assert len(code) == 4
        assert not set(code) & set("01OIL")


def _message(message_id: str) -> Message:
    return Message(
        id=message_id,
        client="content",
        to_addrs=["someone@example.com"],
        from_addr="Content <content@example.com>",
        reply_to=None,
        subject="Your HomeTube sign-in link · 7KQ3",
        text_body="x",
        html_body=None,
        status="queued",
        attempts=0,
        last_error=None,
        created_at=0.0,
        updated_at=0.0,
        next_attempt_at=0.0,
        sent_at=None,
    )


def test_each_message_carries_its_own_entity_reference():
    first = build_email(_message("msg_a"), message_id_domain="example.com")
    second = build_email(_message("msg_b"), message_id_domain="example.com")
    assert first["X-Entity-Ref-ID"] == "msg_a"
    assert second["X-Entity-Ref-ID"] == "msg_b"
    assert first["Message-ID"] != second["Message-ID"]
