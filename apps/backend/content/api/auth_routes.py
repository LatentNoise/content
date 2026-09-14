"""The sign-in door: ask for a link, follow it, get a session.

**Why this lives in the engine and not in a surface.** ADR 0030 makes
`content/api/auth.py` the only place that turns a request into an identity;
issuing the credentials somewhere else would create a second such place. A
Streamlit surface also cannot set an `HttpOnly` cookie on the parent domain,
and making one surface own sign-in would make the other three depend on it.

The two HTML pages here are protocol pages, not product pages. The engine
still has no product UI.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator

from content.config import surface_at
from content.identity import credentials
from content.persistence.store import utcnow

log = logging.getLogger("content.auth")


class NewKeyRequest(BaseModel):
    """A key is named by the person creating it, so that "which key is this?"
    has an answer a year later — and so that one can be revoked without
    touching the others."""

    name: str

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a key needs a name")
        return value.strip()[:80]


class LinkRequest(BaseModel):
    """What a sign-in request carries.

    The address is checked for shape only — one `@`, something on each side,
    no spaces — rather than against the RFC. A full validator would cost a
    runtime dependency (and a DNS library) to reject addresses that the mail
    service will reject anyway, while risking a *valid* exotic address being
    turned away at the door. An address that does not exist simply never
    receives its link, which is the same outcome and costs nothing.
    """

    email: str
    next: str = ""

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        candidate = value.strip()
        local, sep, domain = candidate.partition("@")
        if (
            not sep
            or not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or any(c.isspace() for c in candidate)
            or len(candidate) > 254
        ):
            raise ValueError("not an email address")
        return candidate


def _iso_in(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _iso_ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def redirect_is_allowed(target: str, allowed_origins: tuple[str, ...]) -> bool:
    """Is this a place a sign-in may send the browser?

    An open redirect on a sign-in endpoint is how a phishing page borrows your
    domain: the link is genuinely yours, the mail is genuinely yours, and the
    browser still lands on someone else's form. So the rule is an allowlist of
    whole origins — scheme, host and port together, since `https://evil.com`
    and `http://evil.com` are different places and so are two ports.

    An empty target means "nowhere in particular", which is allowed: the
    caller then picks its own default.
    """
    if not target:
        return True
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.netloc:
        # A relative target ("/jobs") stays on this origin, which is safe by
        # construction — there is nowhere else for it to go.
        return not target.startswith("//")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in allowed_origins


def _page(title: str, body: str) -> HTMLResponse:
    """One small stylesheet, no assets, no framework. These pages load on a
    phone on a train, and they are the last thing standing between a user and
    their account."""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0; display: grid;
         min-height: 100vh; place-items: center; padding: 24px; }}
  main {{ width: 100%; max-width: 26rem; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 .5rem; }}
  p {{ margin: 0 0 1rem; opacity: .8; }}
  input, button {{ font: inherit; width: 100%; box-sizing: border-box;
                  padding: .7rem .8rem; border-radius: .5rem; }}
  input {{ border: 1px solid rgba(128,128,128,.5); background: transparent;
          color: inherit; margin-bottom: .75rem; }}
  button {{ border: 0; background: #2563eb; color: #fff; cursor: pointer; }}
  .note {{ font-size: .85rem; opacity: .6; margin-top: 1.5rem; }}
</style></head>
<body><main>{body}</main></body></html>""",
    )


def build_auth_router(settings, store, mailer) -> APIRouter:
    router = APIRouter()
    cookie_name = settings.session_cookie_name
    product = settings.product_name

    def _product_for(next_target: str) -> str:
        """What the page and the email call the place someone is signing in
        to. "Sign in to Content Studio" when the return address is a surface
        we know; the product's own name otherwise — never the bare target."""
        surface = surface_at(settings, next_target)
        return surface["title"] if surface else product

    def _set_session_cookie(response: Response, secret: str) -> None:
        response.set_cookie(
            cookie_name,
            secret,
            max_age=int(settings.session_ttl_hours * 3600),
            httponly=True,  # JavaScript must never read it (XSS steals sessions)
            secure=settings.session_cookie_secure,
            samesite="lax",  # the sign-in redirect is a top-level GET, so lax works
            domain=settings.session_cookie_domain or None,
            path="/",
        )

    def _issue_link(email: str, next_target: str) -> None:
        """Create a token, hand the mail over. Never tells the caller anything."""
        token = credentials.new_token()
        store.create_auth_token(
            credentials.fingerprint(token),
            email,
            _iso_in(minutes=settings.magic_link_ttl_minutes),
        )
        base = settings.public_base_url or ""
        query = {"token": token}
        if next_target:
            query["next"] = next_target
        link = f"{base}/api/v1/auth/callback?{urlencode(query)}"
        mailer.send_template(
            email,
            "magic-link",
            {
                "link": link,
                "product": _product_for(next_target),
                "minutes": f"{settings.magic_link_ttl_minutes:g}",
            },
        )

    @router.post("/api/v1/auth/link", status_code=status.HTTP_202_ACCEPTED)
    async def request_link(payload: LinkRequest) -> dict:
        """Ask for a sign-in link.

        **The answer never depends on whether the address is known.** A
        different response for a known address turns this endpoint into an
        account-enumeration oracle: anyone could ask it, one address at a
        time, who has an account here. So the reply is identical in every
        case, including when the rate limit fired and when the mail service
        refused the message.
        """
        if not redirect_is_allowed(payload.next, settings.allowed_redirect_origins):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="That redirect target is not allowed.",
            )
        email = credentials.normalize_email(payload.email)
        recent = store.count_recent_auth_tokens(email, _iso_ago(hours=1))
        if recent >= settings.magic_link_max_per_hour:
            log.warning("magic-link rate limit reached for one address")
        else:
            try:
                await asyncio.to_thread(_issue_link, email, payload.next)
            except Exception:  # noqa: BLE001 — the caller learns nothing either way
                log.exception("could not issue a sign-in link")
        return {"status": "sent"}

    @router.get("/api/v1/auth/callback")
    async def follow_link(token: str = "", next: str = "") -> Response:
        """Burn the token, open the session, send the browser onward."""
        if not redirect_is_allowed(next, settings.allowed_redirect_origins):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That redirect target is not allowed.",
            )
        claimed = (
            store.burn_auth_token(credentials.fingerprint(token), utcnow())
            if token
            else None
        )
        if claimed is None:
            # Expired, already used, or never existed — one answer for all
            # three, because telling them apart helps nobody but an attacker.
            return _page(
                "Link expired",
                f"<h1>This link no longer works</h1><p>A sign-in link works once "
                f"and expires after {settings.magic_link_ttl_minutes:g} minutes."
                "</p><p>Ask for a new one.</p>"
                '<p><a href="/auth/sign-in">Sign in</a></p>',
            )
        account = store.account_for_email(claimed["email"]) or store.create_account(
            credentials.new_owner_id(), claimed["email"]
        )
        # Granting the privilege at sign-in is how the FIRST operator exists:
        # there is nobody to promote them. Listing an address grants it;
        # unlisting does not revoke it, because withdrawing a privilege is a
        # deliberate act on the account and not a side effect of editing a file.
        if claimed["email"] in settings.operator_emails and not account.get(
            "is_operator"
        ):
            store.set_operator(account["owner_id"], True)
            log.info("granted the operator privilege to %s", account["owner_id"])
        secret = credentials.new_session_secret()
        store.create_session(
            credentials.fingerprint(secret),
            account["owner_id"],
            _iso_in(hours=settings.session_ttl_hours),
        )
        store.touch_account(account["owner_id"])
        # Where to land. A link that named a destination wins; otherwise the
        # operator's configured landing page; otherwise the engine itself,
        # which serves API documentation and welcomes nobody.
        #
        # The configured default is checked against the same allowlist as any
        # other destination: a misconfiguration must fail visibly here rather
        # than quietly become the one redirect nobody validates.
        fallback = settings.sign_in_default_target
        if fallback and not redirect_is_allowed(
            fallback, settings.allowed_redirect_origins
        ):
            log.warning(
                "CONTENT_SIGN_IN_DEFAULT_TARGET is not an allowed origin; ignoring it"
            )
            fallback = ""
        target = next or fallback or settings.public_base_url or "/"
        response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
        _set_session_cookie(response, secret)
        return response

    @router.get("/auth/sign-in", include_in_schema=False)
    async def sign_in_page(next: str = "") -> HTMLResponse:
        safe_next = (
            next if redirect_is_allowed(next, settings.allowed_redirect_origins) else ""
        )
        going_to = _product_for(safe_next)
        return _page(
            f"Sign in to {going_to}",
            f"<h1>Sign in to {html.escape(going_to)}</h1>"
            "<p>Enter your email and we will send you a link. No password.</p>"
            '<form method="post" action="/auth/sign-in">'
            f'<input type="hidden" name="next" value="{html.escape(safe_next, True)}">'
            '<input type="email" name="email" required autofocus '
            'autocomplete="email" placeholder="you@example.com">'
            '<button type="submit">Send me a link</button></form>'
            '<p class="note">The link works once and expires in '
            f"{settings.magic_link_ttl_minutes:g} minutes.</p>",
        )

    @router.post("/auth/sign-in", include_in_schema=False)
    async def sign_in_submit(
        email: str = Form(...), next: str = Form(default="")
    ) -> Response:
        target = (
            next if redirect_is_allowed(next, settings.allowed_redirect_origins) else ""
        )
        await request_link(LinkRequest(email=email, next=target))
        return RedirectResponse(
            f"/auth/check-your-mail?email={quote(email)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.get("/auth/check-your-mail", include_in_schema=False)
    async def check_your_mail(email: str = "") -> HTMLResponse:
        where = f" for <strong>{html.escape(email)}</strong>" if email else ""
        return _page(
            "Check your mail",
            f"<h1>Check your mail</h1><p>If an account exists{where}, a sign-in "
            "link is on its way.</p>"
            '<p class="note">Nothing yet? Look in spam, then '
            '<a href="/auth/sign-in">ask again</a>.</p>',
        )

    return router


def build_session_router(settings, store, owner_dependency) -> APIRouter:
    """The two routes that need an established identity.

    Separate from the router above on purpose: everything there runs *before*
    anyone is known, and these two run after. Keeping them apart makes the
    ownership guard-rail readable rather than a list of exceptions.
    """
    router = APIRouter()

    @router.get("/api/v1/auth/me")
    async def whoami(owner_id: str = owner_dependency) -> dict:
        account = store.account_for_owner(owner_id)
        return {
            "owner_id": owner_id,
            "email": account["email"] if account else "",
            # Surfaced so a UI can show or hide operator views without probing
            # a route to see whether it gets a 403.
            "is_operator": store.is_operator(owner_id),
            # A self-hosted instance has one implicit user and no account row.
            # Saying so plainly beats a client guessing from an empty email.
            "account": account is not None,
        }

    @router.post("/api/v1/auth/keys", status_code=status.HTTP_201_CREATED)
    async def create_key(
        payload: NewKeyRequest, owner_id: str = owner_dependency
    ) -> dict:
        """Mint a key for a program.

        **The secret is in this response and nowhere else, ever.** Only its
        fingerprint is stored, so a database dump is a list of useless hashes
        rather than a keyring — and nobody, including the operator, can read
        the key back afterwards.
        """
        secret = credentials.new_api_key()
        key = store.create_api_key(
            credentials.new_api_key_id(),
            credentials.fingerprint(secret),
            owner_id,
            payload.name.strip(),
        )
        log.info("api key %s created for %s", key["id"], owner_id)
        return {**key, "key": secret}

    @router.get("/api/v1/auth/keys")
    async def list_keys(owner_id: str = owner_dependency) -> list[dict]:
        """Name, age and last use — never the key. There is nothing to show."""
        return store.list_api_keys(owner_id)

    @router.delete("/api/v1/auth/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_key(key_id: str, owner_id: str = owner_dependency) -> Response:
        """Revoke one key, which is the entire reason they are named.

        Scoped to its owner, so an id copied from someone else's list is not
        found rather than forbidden — 404 would otherwise confirm it exists.
        """
        if not store.revoke_api_key(owner_id, key_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown key."
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/api/v1/auth/logout")
    async def logout(request: Request, owner_id: str = owner_dependency) -> Response:
        presented = request.cookies.get(settings.session_cookie_name, "")
        if presented:
            store.revoke_session(credentials.fingerprint(presented))
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            settings.session_cookie_name,
            domain=settings.session_cookie_domain or None,
            path="/",
        )
        return response

    return router
