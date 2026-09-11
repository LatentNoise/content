"""The engine's side of the mail service.

Content never speaks SMTP. It posts a message to the mailer and is done: the
credentials, the retries and the delivery log belong there, not here
(ADR 0031).

Two implementations, chosen by configuration rather than by a branch in the
caller. A self-hosted instance and the test suite have no mailer, and the
right behaviour there is to *log* the link rather than to fail — a single-user
instance never sends a sign-in mail to anybody in the first place.

`urllib.request` rather than httpx: one JSON POST does not justify a runtime
dependency for the whole engine. It is blocking, so the async caller runs it
in a thread.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("content.mail")


class MailerError(RuntimeError):
    """The mail could not be handed over."""


class Mailer:
    """Posts a templated message to the mail service."""

    def __init__(self, url: str, api_key: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def send_template(self, to: str, template: str, variables: dict[str, str]) -> str:
        payload = json.dumps(
            {"to": [to], "template": template, "variables": variables}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/v1/messages",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # The secret travels in the Authorization header, never in the
                # body (it would end up in logs) and never in the URL (history,
                # Referer). Same rule as ADR 0030.
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MailerError(f"mailer refused the message: {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise MailerError(f"mailer unreachable: {exc}") from exc
        return str(body.get("id", ""))


class LoggingMailer:
    """No mail service configured: write the link to the log instead.

    This is what a self-hosted instance runs, and it is not a degraded mode —
    such an instance has one implicit user who never signs in. It is also how
    an operator recovers when the mail service is down: the link is in the
    log, and the log is theirs.
    """

    def send_template(self, to: str, template: str, variables: dict[str, str]) -> str:
        log.warning(
            "no mailer configured; %s message for %s not sent. variables=%s",
            template,
            to,
            variables,
        )
        return ""


def build_mailer(settings) -> Mailer | LoggingMailer:
    if not settings.mailer_url:
        return LoggingMailer()
    return Mailer(
        settings.mailer_url,
        settings.mailer_api_key,
        timeout=settings.mailer_timeout_seconds,
    )
