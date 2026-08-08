"""The gate's own guard-rail: prove the hermetic suite is actually hermetic.

`make validate` claims "no network". Until CI existed, that claim was only ever
checked on a machine that *had* network — so a test that quietly resolved a
hostname passed, and would have kept passing right up to the first run on a
locked-down runner. The autouse guard in `conftest.py` turns the claim into an
enforced invariant; these tests prove the guard itself is live.

Without them the guard could be disabled by a stray fixture reorder and nothing
would notice: every test would still be green, and "hermetic" would quietly go
back to meaning "asserted in a docstring".
"""

import socket

import pytest


def test_resolving_a_hostname_is_blocked():
    with pytest.raises(BaseException, match="example.com"):
        socket.getaddrinfo("example.com", 80)


def test_connecting_to_a_public_address_is_blocked():
    with pytest.raises(BaseException, match="93.184.216.34"):
        socket.socket().connect(("93.184.216.34", 80))


def test_a_literal_address_still_resolves():
    """No name server is involved, so this must not be mistaken for egress."""
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_loopback_stays_open():
    """A local HTTP server is not the Internet — several tests rely on one."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())


def test_the_guard_is_installed():
    assert getattr(socket.getaddrinfo, "hermetic_guard", False)


@pytest.mark.external
def test_external_tests_are_exempt():
    """The `external` suite exists to exercise the real tools; guarding it would
    make `make validate-all` test nothing."""
    assert not getattr(socket.getaddrinfo, "hermetic_guard", False)
