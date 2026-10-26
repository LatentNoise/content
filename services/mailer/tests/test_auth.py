"""Who is calling — and what happens when the credential is not plain ASCII.

`hmac.compare_digest` refuses two `str` arguments when either carries a
non-ASCII character. Both sides of this comparison can: the presented key
comes from an `Authorization` header, which Starlette decodes as latin-1, and
the configured ones come from an operator. Before the encode in
`client_from_key`, either raised `TypeError` straight out of the dependency —
a stranger's stray byte became a 500 where a 401 was owed, and one accented
key in the configuration made the service answer nobody at all.
"""

import pytest
from fastapi.testclient import TestClient

from mailer.app import create_app
from mailer.auth import client_from_key

KEYS = {"test-secret-key": "content", "other-key": "studio"}

# An Authorization header a stranger can send and an ASCII-only comparison
# cannot survive: a raw latin-1 byte, and the two bytes of a UTF-8 "é".
NON_ASCII_HEADERS = [
    pytest.param("Bearer cl\xe9".encode("latin-1"), id="latin-1-byte"),
    pytest.param("Bearer clé".encode(), id="utf-8-bytes"),
]

AUTHENTICATED_ROUTES = [("GET", "/v1/config"), ("GET", "/v1/messages/anything")]


@pytest.mark.parametrize("raw", NON_ASCII_HEADERS)
@pytest.mark.parametrize(("method", "path"), AUTHENTICATED_ROUTES)
def test_a_non_ascii_credential_is_refused_not_crashed(settings, store, sender, method, path, raw):
    """401, not 500: a bad credential is a bad credential whatever its bytes."""
    app = create_app(settings, store=store, sender=sender, start_worker=False)
    # raise_server_exceptions=False so an unhandled error shows as the 500 a
    # real deployment would return, instead of surfacing as the test's own.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, path, headers={b"Authorization": raw})
    assert response.status_code == 401


def test_posting_a_message_with_a_non_ascii_credential_is_refused(settings, store, sender):
    app = create_app(settings, store=store, sender=sender, start_worker=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/messages",
            json={"to": ["a@b.com"], "subject": "s", "text": "t"},
            headers={b"Authorization": "Bearer clé".encode()},
        )
    assert response.status_code == 401
    assert store.counts().get("queued", 0) == 0


def test_a_non_ascii_presented_key_matches_nothing():
    assert client_from_key(KEYS, "clé-non-ascii") is None


def test_a_non_ascii_configured_key_still_lets_the_others_match():
    """One accented key in the configuration used to poison every lookup.

    The loop deliberately visits every key, so the key that raises need not be
    the key presented: before the fix, `plain-key` could not match while
    `clé-secrète` sat anywhere in the mapping.
    """
    keys = {"clé-secrète": "accented", "plain-key": "studio"}
    assert client_from_key(keys, "plain-key") == "studio"
    assert client_from_key(keys, "clé-secrète") == "accented"
    assert client_from_key(keys, "wrong") is None
