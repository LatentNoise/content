"""Making and checking the secrets that prove who someone is.

One rule governs this module, and it is the reason it exists apart from the
routes that use it:

    **the database stores a fingerprint, never a secret.**

A magic-link token and an API key are both bearer secrets: whoever holds one
is the user. If the database held them in the clear, a leaked backup would be
a working set of keys rather than a list of useless hashes.

SHA-256 is the right tool here and bcrypt/argon2 are not, which is worth
saying because the opposite is true for passwords. These secrets are 32 bytes
of `secrets.token_urlsafe` — there is no dictionary to attack and no human
memory to compensate for, so a slow hash would only tax the server on every
request while adding nothing against an attacker who cannot guess 256 bits.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Long enough that guessing is hopeless, short enough to sit in a URL.
TOKEN_BYTES = 32

# A readable prefix on an API key, so it is recognisable in a log and
# detectable by the secret scanners that watch public repositories.
API_KEY_PREFIX = "ck_live_"


def new_token() -> str:
    """A single-use secret for a magic link."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_session_secret() -> str:
    """The value that goes in the session cookie."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_api_key() -> str:
    """A long-lived secret for a program, carrying its readable prefix."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


def fingerprint(secret: str) -> str:
    """What the database stores. Never reversible to the secret."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def matches(secret: str, stored_fingerprint: str) -> bool:
    """Compare in constant time.

    Both values are hex digests of fixed length, so the comparison leaks
    nothing about the secret through timing.
    """
    return hmac.compare_digest(fingerprint(secret), stored_fingerprint)


def new_owner_id() -> str:
    """A real account's owner id.

    The `usr_` prefix keeps it from ever colliding with ``local``, the single
    implicit user of a self-hosted instance, and makes an id recognisable on
    sight in a path or a log line.
    """
    return f"usr_{secrets.token_hex(12)}"


def new_api_key_id() -> str:
    """The public handle of a key: what a list shows and what a revoke names.

    Separate from the key itself on purpose — the secret is shown once and
    then unknowable, so something else has to be addressable afterwards.
    """
    return f"key_{secrets.token_hex(8)}"


def normalize_email(raw: str) -> str:
    """The form an address is stored and compared in.

    Case-folded and trimmed, because a person who signs up as `Yann@…` and
    signs in as `yann@…` is the same person — and because the Polar payment
    email has to match the account email exactly (ADR 0030, decision 5).
    Nothing more clever than that: stripping dots or `+tags` would merge
    addresses their owner considers distinct.
    """
    return raw.strip().lower()


# Letters and digits a person reads aloud without hesitating: no 0/O, no 1/I/L.
# The mail service uses the same alphabet for the codes it makes itself.
_REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_link_reference() -> str:
    """A short code that tells one sign-in email from the one before it.

    Not a secret and not a credential: it is printed in the subject and on the
    confirmation page, and grants nothing. It exists because identical subjects
    made mail clients fold every new link into the first one's conversation.
    """
    return "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(4))


def is_link_reference(value: str) -> bool:
    """Is this shaped like a code `new_link_reference` makes — and nothing else?"""
    return len(value) == 4 and all(c in _REFERENCE_ALPHABET for c in value)
