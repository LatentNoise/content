"""Who the caller is, and who owns a row.

Two words for two questions, and keeping them apart is what makes the rest of
the code readable:

``user_id``
    Who is making this request. Established once, at the edge, by
    :func:`content.api.auth.current_user`. Never declared by the client — the
    client sends a secret, the server derives the identity (ADR 0030).

``owner_id``
    Who a stored row or a stored file belongs to. In an ordinary request they
    are the same value; they are not the same *concept*, and an operator route
    that lists someone else's jobs is exactly where the difference shows.

``LOCAL_OWNER`` is the single implicit user of a self-hosted instance. It is
not a fallback, an anonymous marker, or a guest: it is a real owner id that
simply never had to sign in. Everything else in the codebase can then assume
an owner exists, always — there is no "no user" branch anywhere.

Chosen over ``default`` (which reads as "a default value" and could collide
with a real account named default) and over ``self_hosted`` (which names a
deployment mode, not a person). Real accounts get ``usr_<random>`` ids, so
``local`` can never collide with one.
"""

from enum import Enum

LOCAL_OWNER = "local"

# The window the magic-link rate limit counts over. It lives here because two
# queries in two different modules must agree on it: the limit counts the
# unopened links created inside it (`Store.count_recent_auth_tokens`, read by
# the sign-in route), and housekeeping must *not* delete the rows it counts
# (`Store.delete_expired_auth_tokens`).
#
# The two were written a release apart and the overlap is easy to miss: a link
# expires in 15 minutes by default and is counted for an hour, so for 45 of
# every 60 minutes the evidence of a flood is expired — and therefore
# deletable — while it is still the only thing standing between a stranger's
# inbox and the next request. Named once, so a sweep cannot quietly reset the
# limit it is supposed to leave alone.
MAGIC_LINK_RATE_WINDOW_HOURS = 1.0


class AuthMode(str, Enum):
    """How ``current_user`` establishes identity.

    ``none`` is the default and keeps the historical behaviour exactly: no
    credential is asked for, every request belongs to :data:`LOCAL_OWNER`.
    This is the self-hosted contract of ADR 0024, unchanged.

    ``token`` is the hosted contract: a request without a valid credential is
    rejected, and the owner comes from the credential.
    """

    NONE = "none"
    TOKEN = "token"


# Where an owner id is checked, and it is not here: an owner id becomes a path
# segment, so `content.storage.paths.safe_segment` validates it — once, in the
# module that builds the paths (`storage/roots.py`, INV-STORAGE-006). This file
# carried a second `is_valid_owner_id` from ADR 0030 that nothing ever called,
# and it was the looser of the two in both directions, so believing its
# docstring would have weakened the guarantee rather than doubled it.
