"""The HTTP surface: accept a message, report on one, say whether we are alive."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, model_validator

from mailer import __version__
from mailer.auth import require_client
from mailer.config import Settings, load_settings
from mailer.sender import DryRunSender, SmtpSender
from mailer.store import Store
from mailer.templates import TemplateError, render
from mailer.worker import run_worker

log = logging.getLogger("mailer")


class SendRequest(BaseModel):
    """A product describes the message it wants delivered.

    It may supply the body itself, or name a template and its variables. It
    may not do both: two sources of truth for one body is a bug waiting for a
    busy day.
    """

    to: list[EmailStr] = Field(min_length=1, max_length=20)
    subject: str | None = Field(default=None, max_length=300)
    text: str | None = None
    html: str | None = None
    template: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    from_: str | None = Field(default=None, alias="from")
    reply_to: EmailStr | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _one_body_source(self) -> "SendRequest":
        has_body = bool(self.text or self.html)
        if self.template and has_body:
            raise ValueError("give either 'template' or a body, not both")
        if not self.template:
            if not has_body:
                raise ValueError("give 'template', or 'text'/'html'")
            if not self.subject:
                raise ValueError("'subject' is required when no template is used")
        return self


def create_app(
    settings: Settings | None = None,
    *,
    store: Store | None = None,
    sender=None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(settings.db_path)
    sender = sender or (DryRunSender() if settings.dry_run else SmtpSender(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task: asyncio.Task | None = None
        if start_worker:
            task = asyncio.create_task(run_worker(store, sender, settings))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="LatentNoise Mailer",
        version=__version__,
        summary="One door for outbound email across LatentNoise products.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.sender = sender

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "version": __version__, "queue": store.counts()}

    @app.get("/v1/config", tags=["system"])
    async def read_config(client: str = Depends(require_client)) -> dict[str, object]:
        return settings.describe()

    @app.post("/v1/messages", status_code=status.HTTP_202_ACCEPTED, tags=["messages"])
    async def send_message(
        request: SendRequest, client: str = Depends(require_client)
    ) -> dict[str, object]:
        subject = request.subject or ""
        text = request.text or ""
        html_body = request.html
        if request.template:
            try:
                rendered = render(request.template, request.variables)
            except TemplateError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
                ) from exc
            subject = request.subject or rendered.subject
            text = rendered.text
            html_body = rendered.html
        message = store.enqueue(
            client=client,
            to_addrs=[str(address) for address in request.to],
            from_addr=request.from_ or settings.default_from,
            subject=subject,
            text_body=text,
            html_body=html_body,
            reply_to=str(request.reply_to) if request.reply_to else None,
        )
        log.info("accepted message %s from %s for %s", message.id, client, message.to_addrs)
        return message.public()

    @app.get("/v1/messages/{message_id}", tags=["messages"])
    async def read_message(
        message_id: str, client: str = Depends(require_client)
    ) -> dict[str, object]:
        message = store.get(message_id)
        # A client may only read its own messages, and a message belonging to
        # another client must look absent rather than forbidden: "403" would
        # confirm the id exists.
        if message is None or message.client != client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown message.")
        return message.public()

    return app
