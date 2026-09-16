"""Message templates, kept in code on purpose.

They live here rather than in loose .txt/.html files because a template is a
published behaviour: it belongs in review and in the diff, not in a folder a
deployment can quietly replace. A caller may still post a fully written body
and skip templates entirely.
"""

from __future__ import annotations

import html
import secrets
from collections.abc import Callable
from dataclasses import dataclass

# Letters and digits a person reads aloud without hesitating: no 0/O, no 1/I/L.
_REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_reference() -> str:
    """A short code that makes one message unlike the last one sent."""
    return "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(4))


class TemplateError(ValueError):
    """The template does not exist, or a required variable is missing."""


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str | None


def _need(variables: dict[str, str], *names: str) -> list[str]:
    missing = [name for name in names if not str(variables.get(name) or "").strip()]
    if missing:
        raise TemplateError(f"missing variables: {', '.join(missing)}")
    return [str(variables[name]) for name in names]


def _magic_link(variables: dict[str, str]) -> Rendered:
    """A sign-in link, and a subject no earlier link shares.

    Every link used to go out as "Your Content sign-in link". Gmail groups mail
    by sender and subject, so the second link asked for landed *inside the
    conversation of the first* — under a message already read, often already
    archived — and a person asking again, from another browser or after losing
    a session, reasonably concluded that nothing had been sent. The messages
    were delivered every time; they were just impossible to find.

    So the subject carries a short reference, different each time. It is the
    caller's when given — the engine shows the same code on its "check your
    mail" page, so someone with several links can tell which is which — and a
    fresh one otherwise.
    """
    link, product, minutes = _need(variables, "link", "product", "minutes")
    reference = str(variables.get("reference") or "").strip() or new_reference()
    subject = f"Your {product} sign-in link · {reference}"
    text = (
        f"Here is your sign-in link for {product}.\n\n"
        f"{link}\n\n"
        f"It works once and expires in {minutes} minutes.\n"
        "If you did not ask to sign in, ignore this message: nothing happens until "
        "the link is opened.\n\n"
        f"Reference {reference}\n"
    )
    safe_link = html.escape(link, quote=True)
    body = (
        '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5">'
        f"<p>Here is your sign-in link for {html.escape(product)}.</p>"
        f'<p><a href="{safe_link}" '
        'style="display:inline-block;padding:10px 18px;background:#111;color:#fff;'
        'border-radius:6px;text-decoration:none">Sign in</a></p>'
        f'<p style="color:#666">It works once and expires in {html.escape(minutes)} minutes. '
        "If you did not ask to sign in, ignore this message.</p>"
        f'<p style="color:#999;font-size:12px;word-break:break-all">{safe_link}</p>'
        f'<p style="color:#999;font-size:12px">Reference {html.escape(reference)}</p>'
        "</div>"
    )
    return Rendered(subject=subject, text=text, html=body)


TEMPLATES: dict[str, Callable[[dict[str, str]], Rendered]] = {
    "magic-link": _magic_link,
}


def render(name: str, variables: dict[str, str]) -> Rendered:
    template = TEMPLATES.get(name)
    if template is None:
        raise TemplateError(f"unknown template {name!r}; known: {', '.join(sorted(TEMPLATES))}")
    return template(variables)
