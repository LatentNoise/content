"""Talking to the outbound-email service (ADR 0031)."""

from content.mail.client import Mailer, MailerError, build_mailer

__all__ = ["Mailer", "MailerError", "build_mailer"]
