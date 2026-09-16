"""Handing a message to the provider.

The only place in the service that speaks SMTP. It classifies failures into
two kinds, because they deserve opposite treatments: a permanent refusal is
final and retrying it only annoys the provider, while a temporary one is
exactly what retries exist for.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from mailer.config import Settings
from mailer.store import Message


class PermanentSendError(RuntimeError):
    """The provider refused in a way that a retry cannot fix."""


class TemporarySendError(RuntimeError):
    """The attempt failed for a reason that may not hold next time."""


def build_email(message: Message, *, message_id_domain: str) -> EmailMessage:
    mail = EmailMessage()
    mail["From"] = message.from_addr
    mail["To"] = ", ".join(message.to_addrs)
    mail["Subject"] = message.subject
    mail["Date"] = formatdate(localtime=True)
    mail["Message-ID"] = make_msgid(domain=message_id_domain)
    # Gmail threads messages by sender and subject, and a transactional mail
    # that joins an old conversation is a mail nobody sees. A unique entity
    # reference is the header Gmail reads to keep each one on its own — the
    # subject reference in the templates does the same for every other client.
    mail["X-Entity-Ref-ID"] = message.id
    if message.reply_to:
        mail["Reply-To"] = message.reply_to
    mail.set_content(message.text_body)
    if message.html_body:
        mail.add_alternative(message.html_body, subtype="html")
    return mail


def _domain_of(address: str) -> str:
    local_part, _, domain = address.rpartition("@")
    domain = domain.strip(" >")
    return domain or "localhost"


class SmtpSender:
    """Sends through the configured provider, one connection per message.

    One connection per message is deliberate at this volume: a pooled
    connection that has silently died is a class of bug that costs more than
    the handshake it saves.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, message: Message) -> None:
        mail = build_email(message, message_id_domain=_domain_of(self.settings.default_from))
        context = ssl.create_default_context()
        try:
            if self.settings.smtp_security == "ssl":
                with smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    context=context,
                    timeout=30,
                ) as server:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                    server.send_message(mail)
            else:
                with smtplib.SMTP(
                    self.settings.smtp_host, self.settings.smtp_port, timeout=30
                ) as server:
                    server.starttls(context=context)
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                    server.send_message(mail)
        except smtplib.SMTPAuthenticationError as exc:
            # A 5xx, but the operator can fix it by correcting the credentials —
            # so the message waits rather than being thrown away.
            raise TemporarySendError(f"authentication refused: {exc}") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentSendError(f"recipients refused: {exc.recipients}") from exc
        except smtplib.SMTPResponseException as exc:
            if 500 <= exc.smtp_code < 600:
                raise PermanentSendError(f"{exc.smtp_code} {exc.smtp_error!r}") from exc
            raise TemporarySendError(f"{exc.smtp_code} {exc.smtp_error!r}") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise TemporarySendError(f"{type(exc).__name__}: {exc}") from exc


class DryRunSender:
    """Records instead of sending. What the test suite uses, and what a
    staging instance runs so it can never mail a real person by accident."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)
