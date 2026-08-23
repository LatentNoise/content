"""FastAPI application: the /api/v1 surface of the public contract.

Dependencies are injectable (store, providers, worker) so tests run hermetic
apps with fake providers — the pattern proven by HomeTube's create_app().
Run with::

    uvicorn content.api.app:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from pydantic import BaseModel, ConfigDict, Field

from content import __version__
from content.analysis.cache import AnalysisJsonCache
from content.analysis.service import (
    AnalysisExpired,
    AnalysisNotFound,
    AnalysisService,
)
from content.application.collections import attach_collection_runner
from content.application.submit import submit_generation
from content.application.uploads import sweep_expired_uploads
from content.capabilities.facts import facts_from_analysis
from content.capabilities.inventory import describe_architecture
from content.capabilities.policy import (
    RequestConstraints,
    effective_policy,
    instance_policy_from_settings,
)
from content.capabilities.resolver import CapabilityResolver
from content.config import (
    ContentSettings,
    describe_environment,
    settings_from_env,
)
from content.domain import errors as codes
from content.domain.capability import ResolvedCapability
from content.domain.errors import RequestRejected, ValidationIssue
from content.domain.job import JOB_TERMINAL
from content.domain.request import GenerationRequest, SourceDescriptor
from content.execution.executor import JobExecutor
from content.execution.worker import JobQueue
from content.naming.engine import suggest_base_name
from content.naming.sanitize import sanitize_filename
from content.notifications import build_notifications
from content.persistence.store import Store, new_id
from content.planning.transformations import build_registry
from content.processors.chapters import ChaptersProcessor
from content.processors.pdf import ReportLabPdfProcessor, TypstPdfProcessor
from content.processors.transcript import TranscriptProcessor
from content.providers.base import ProviderRegistry
from content.providers.cloud_llm import CloudSummarizer
from content.providers.documents import DocumentProvider
from content.providers.ffmpeg import FfmpegProvider
from content.providers.ollama import OllamaProvider
from content.providers.webpage import WebPageProvider
from content.providers.whisper import WhisperProcessor
from content.providers.ytdlp import YtDlpProvider
from content.storage.layout import (
    DeliveryStore,
    JobStorage,
    UploadStore,
    UploadTooLarge,
)
from content.storage.paths import storage_report


class AnalysisRequest(BaseModel):
    sources: list[SourceDescriptor] = Field(min_length=1)


class CapabilityConstraints(BaseModel):
    """Optional per-request overlay — may only restrict instance policies (R4)."""

    allow_cloud_providers: bool | None = None


class CapabilitiesRequest(BaseModel):
    """Inputs given **either** inline as ``sources`` **or** by reference to an
    addressable analysis via ``analysis_id`` — exactly one (ADR 0014)."""

    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [{"required": ["sources"]}, {"required": ["analysis_id"]}]
        }
    )

    analysis_id: str | None = Field(default=None, min_length=1)
    sources: list[SourceDescriptor] | None = Field(default=None, min_length=1)
    constraints: CapabilityConstraints | None = None


class SourceCapabilities(BaseModel):
    """The resolved public capabilities for one analyzed source (ADR 0013)."""

    source_id: str
    resource_type: str
    title: str
    # The base name the naming engine would give this source's artifacts
    # (ADR 0017) — a UI prefills its filename field with this editable
    # proposal instead of re-implementing the display profile client-side.
    suggested_filename: str = ""
    capabilities: list[ResolvedCapability]


class CapabilitiesResponse(BaseModel):
    analysis_id: str
    sources: list[SourceCapabilities]


class JobSubmitted(BaseModel):
    job_id: str
    status: str
    warnings: list[ValidationIssue] = Field(default_factory=list)


def _http_status_for(result_phase: str, error_codes: set[str]) -> int:
    if codes.IDEMPOTENCY_CONFLICT in error_codes:
        return 409
    return 422


def _public_path(loc: tuple, body: object) -> str:
    """Pydantic's `loc` as a path that actually addresses the submitted body.

    Two things have to be removed. `"body"` is a FastAPI artefact. Worse, a
    tagged union inserts the *discriminator value* as a segment, so a bad
    container on a video output came back as
    `outputs[0].video.options.container` — a path with no counterpart in the
    JSON the client sent, which is the opposite of useful in an error message.

    Rather than special-casing the known unions, each segment is walked against
    the real body and dropped when it does not exist there. That stays correct
    for whatever unions the contract grows later.
    """
    parts: list[str] = []
    cursor = body
    for segment in loc:
        if segment == "body":
            continue
        if isinstance(cursor, dict) and isinstance(segment, str):
            if segment not in cursor:
                continue  # a discriminator tag, not a key the client sent
            cursor = cursor[segment]
        elif isinstance(cursor, list) and isinstance(segment, int):
            cursor = cursor[segment] if segment < len(cursor) else None
        else:
            cursor = None  # cannot verify further; keep reporting what is left
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}" if parts else str(segment))
    return "".join(parts)


def _reject_duplicate_source_ids(sources) -> None:
    seen: set[str] = set()
    for index, source in enumerate(sources):
        if source.id in seen:
            raise HTTPException(
                status_code=422,
                detail={
                    "valid": False,
                    "errors": [
                        {
                            "code": codes.DUPLICATE_ID,
                            "path": f"sources[{index}].id",
                            "message": f"Duplicate id '{source.id}' in sources.",
                        }
                    ],
                },
            )
        seen.add(source.id)


def _xor_source_input_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "valid": False,
            "errors": [{"code": code, "path": "sources", "message": message}],
        },
    )


def _resolve_source_input(
    sources: list[SourceDescriptor] | None,
    analysis_id: str | None,
    analysis_service: "AnalysisService",
) -> list[SourceDescriptor]:
    """Enforce the `sources` XOR `analysis_id` rule (ADR 0014) with stable codes,
    and resolve an `analysis_id` to its stored sources. Shared by /capabilities
    and /jobs so both accept the two input shapes identically."""
    if sources is not None and analysis_id is not None:
        raise _xor_source_input_error(
            codes.SOURCES_AND_ANALYSIS_ID_CONFLICT,
            "Provide exactly one of 'sources' or 'analysis_id', not both.",
        )
    if sources is None and analysis_id is None:
        raise _xor_source_input_error(
            codes.SOURCES_OR_ANALYSIS_ID_REQUIRED,
            "Provide exactly one of 'sources' or 'analysis_id'.",
        )
    if analysis_id is not None:
        try:
            return analysis_service.sources_for_analysis(analysis_id)
        except AnalysisNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": codes.ANALYSIS_NOT_FOUND, "message": str(exc)},
            ) from exc
        except AnalysisExpired as exc:
            raise HTTPException(
                status_code=410,
                detail={"code": codes.ANALYSIS_EXPIRED, "message": str(exc)},
            ) from exc
    return list(sources)


def _drain_upload(uploads, upload_id: str, filename: str, source, settings) -> dict:
    """Copy an uploaded file to the upload store, enforcing the size limit.

    ``source`` is Starlette's `UploadFile.file` — a spooled temporary file, so
    the read side is ordinary blocking I/O and this runs in a worker thread to
    keep the event loop free while a large upload lands.

    Honest about what the limit does: Starlette has already buffered the body
    (to memory, then to a temp file past its threshold) before this runs, so
    the check bounds what the engine *stores*, not what it receives. Refusing
    earlier means a middleware reading Content-Length and the raw stream; worth
    doing before this is exposed to anything untrusted, and out of scope here.
    """
    return uploads.write_stream(
        upload_id,
        filename,
        iter(lambda: source.read(1024 * 256), b""),
        limit=settings.max_upload_bytes,
    )


def _upload_view(row: dict) -> dict:
    """The public shape of an upload. Never the stored path: the id is the
    address, and a filesystem path would leak the layout and invite clients to
    build their own."""
    return {
        "upload_id": row["id"],
        "filename": row["filename"],
        "media_type": row["media_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
    }


def create_app(
    settings: ContentSettings | None = None,
    *,
    store: Store | None = None,
    providers: ProviderRegistry | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or settings_from_env()
    store = store or Store(settings.db_path)
    if providers is None:
        summarizers: list = [
            OllamaProvider(
                settings.ollama_url,
                settings.ollama_model,
                settings.ollama_max_context,
            )
        ]
        if settings.anthropic_api_key:
            summarizers.append(
                CloudSummarizer(
                    "anthropic", settings.anthropic_api_key, settings.anthropic_model
                )
            )
        if settings.openai_api_key:
            summarizers.append(
                CloudSummarizer(
                    "openai", settings.openai_api_key, settings.openai_model
                )
            )
        providers = ProviderRegistry(
            # Order here is irrelevant: precedence comes from each provider's
            # analysis_priority, so yt-dlp always gets a URL first.
            [
                YtDlpProvider(),
                FfmpegProvider(),
                DocumentProvider(),
                WebPageProvider(),
            ],
            processors=[
                TranscriptProcessor(),
                ChaptersProcessor(),
                WhisperProcessor(settings.whisper_model),
                # Both implementations of document.render_pdf are registered;
                # the planner picks one per job and records it in the plan.
                TypstPdfProcessor(
                    settings.typst_binary, settings.pdf_font, settings.pdf_template
                ),
                ReportLabPdfProcessor(settings.pdf_font),
                *summarizers,
            ],
        )
    analysis_service = AnalysisService(store, providers, settings)
    # A collection orchestrates the canonical pipeline for its members
    # (ADR 0019), so its runner needs the analysis service and the very
    # registry it joins — hence attached here rather than constructed above.
    attach_collection_runner(providers, analysis_service, settings)
    capability_resolver = CapabilityResolver(build_registry(providers), providers)
    executor = JobExecutor(store, settings, providers)
    # Housekeeping runs beside the queue: uploads nobody referenced within
    # their TTL are collected, which ADR 0020 promised and 0.5.0 did not do.
    queue = JobQueue(
        store,
        executor.execute,
        settings.max_concurrent_jobs,
        sweeper=lambda: sweep_expired_uploads(store, settings),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            await queue.start()
        try:
            yield
        finally:
            if start_worker:
                await queue.stop()

    app = FastAPI(title="Content API", version=__version__, lifespan=lifespan)
    # The API is open by design (no auth in V1): every client — curl, the SDK,
    # scripts — can always reach the backend directly. CORS is the one browser-
    # specific gate; it stays OFF unless the operator opts in with explicit
    # origins (CONTENT_CORS_ORIGINS), which also keeps drive-by browser
    # requests from arbitrary websites blocked by default.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.store = store
    app.state.settings = settings
    app.state.executor = executor

    @app.exception_handler(RequestValidationError)
    def _one_validation_shape(_request: Request, exc: RequestValidationError):
        """One 422 body, not two (D-09).

        Pydantic rejects a malformed body with its own `[{type, loc, msg, …}]`
        list, while everything the engine rejects uses
        `{valid, errors: [{code, path, message}]}`. A client had to parse two
        formats to handle one status code — and could only tell them apart by
        looking. Schema failures are translated into the contract's shape, with
        Pydantic's own text preserved as the human message.
        """
        errors = []
        for error in exc.errors():
            errors.append(
                {
                    "code": codes.SCHEMA_VIOLATION,
                    "path": _public_path(error.get("loc", ()), exc.body),
                    "message": error.get("msg", "Invalid value."),
                    "details": {"type": error.get("type", "")},
                }
            )
        return JSONResponse(
            status_code=422,
            content={"detail": {"valid": False, "phase": "schema", "errors": errors}},
        )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        # The backend has no UI of its own — the API is the product. Land on the
        # interactive contract docs (Swagger). Operations UI = apps/web-admin.
        return RedirectResponse(url="/docs")

    @app.get("/api/v1/health", tags=["system"])
    def health(response: Response) -> dict:
        """Liveness, plus the two failures that make the engine unusable.

        This used to be a static 200, which turned the container healthcheck
        into a formality: an unmounted volume or an unreadable database still
        reported "ok", and an orchestrator kept routing work to an engine that
        could not accept a single job. A healthcheck that cannot fail is worse
        than none, because it is believed.

        Deliberately NOT checked: ffmpeg, yt-dlp, Typst, an LLM daemon. Those
        decide which *capabilities* resolve, and `/api/v1/capabilities` already
        reports that honestly per source. An engine that can still read a page
        and render a PDF is not unhealthy because nobody installed Whisper —
        "not installed" and "broken" are different answers.

        Stays cheap: it runs every 30 seconds, so it is one indexless read and
        one permission check, never a write or an integrity scan.
        """
        checks: dict[str, str] = {}
        try:
            store.ping()
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - any failure here is unhealthy
            checks["database"] = f"unreachable: {exc}"

        data_dir = settings.data_dir
        if data_dir.is_dir() and os.access(data_dir, os.W_OK):
            checks["data_dir"] = "ok"
        else:
            checks["data_dir"] = f"not writable: {data_dir}"

        healthy = all(value == "ok" for value in checks.values())
        if not healthy:
            response.status_code = 503
        return {
            "status": "ok" if healthy else "degraded",
            "version": __version__,
            "checks": checks,
        }

    @app.get("/api/v1/system", tags=["system"])
    def system() -> dict:
        """Instance observability for the admin console: version, effective
        settings (no secrets), and the installed provider/processor inventory."""
        return {
            "version": __version__,
            # AGPL §13 offer of Corresponding Source for this deployment.
            "license": "AGPL-3.0-or-later",
            "source_url": settings.source_url,
            "cache_enabled": settings.cache_enabled,
            "analysis_ttl_hours": settings.analysis_ttl_hours,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "credentials": sorted(settings.credentials),
            "credentials_info": _credentials_info(settings),
            "language": {
                "primary": settings.language_primary,
                "secondaries": list(settings.languages_secondaries),
                "vo_first": settings.vo_first,
                "primary_include_subtitles": settings.primary_include_subtitles,
            },
            "runners": providers.describe(),
            "environment": describe_environment(settings),
            "paths": {
                "data_dir": str(settings.data_dir),
                "delivery_dir": str(
                    settings.delivery_dir or settings.data_dir / "delivery"
                ),
                "tmp_dir": str(settings.tmp_dir or settings.data_dir / "tmp"),
                "cache_dir": str(settings.cache_dir or settings.data_dir / "cache"),
                "allowed_input_roots": [str(p) for p in settings.allowed_input_roots],
            },
        }

    @app.get("/api/v1/notifications", tags=["system"])
    def notifications() -> dict:
        """What the instance wants to tell its operator: a newer release, a
        stale yt-dlp (D-20). Data, never markup — the UIs render it and add
        their own dismissal. Failure-silent: an unreachable release endpoint
        yields an empty list, never an error."""
        return {"notifications": build_notifications(settings, providers)}

    @app.get("/api/v1/catalog", tags=["system"])
    def catalog() -> dict:
        """The engine's architecture (ADR 0013) for the console: the public
        capability catalog, the internal operations, and which implementations
        are installed/available. Read-only observability."""
        return describe_architecture(providers)

    @app.get("/api/v1/storage", tags=["system"])
    def storage() -> dict:
        """Disk usage per storage family (jobs / delivery / tmp / cache) for the
        admin console — bytes, file counts, and a few useful sub-counts."""
        return storage_report(settings)

    @app.get("/api/v1/cache", tags=["system"])
    def cache_list() -> dict:
        """Cached analyses (newest first) + status — for the console cache view."""
        return {
            "enabled": settings.cache_enabled,
            "ttl_hours": settings.analysis_ttl_hours,
            "analyses": store.list_analyses(limit=100),
        }

    @app.post("/api/v1/cache/purge", tags=["system"])
    def cache_purge() -> dict:
        """Drop all cached analyses (DB rows + durable JSON files). Delivered
        artifacts are never touched."""
        purged_rows = store.purge_analyses()
        cache_root = settings.cache_dir or (settings.data_dir / "cache")
        purged_files = AnalysisJsonCache(
            cache_root, settings.analysis_ttl_hours
        ).purge()
        return {"purged_analyses": purged_rows, "purged_files": purged_files}

    @app.get("/api/v1/config", tags=["system"])
    def get_config() -> dict:
        """Client-facing configuration. Credentials are reported as ids plus
        file *metadata* (path, presence, last-modified) so a user can see that
        their cookies are wired and fresh — the secret **content** never leaves
        the server (INV-009)."""
        return {
            "credentials": sorted(settings.credentials),
            "credentials_info": _credentials_info(settings),
            "language": {
                "primary": settings.language_primary,
                "secondaries": list(settings.languages_secondaries),
                "vo_first": settings.vo_first,
                "primary_include_subtitles": settings.primary_include_subtitles,
            },
            # ADR 0018: whether artifacts are delivered into the library by
            # default, so a client can show the effective destination before
            # submitting instead of guessing.
            "delivery": {"by_default": settings.delivery_default},
            # ADR 0020. A client that uploads bytes is entitled to know what
            # happens to them: how long they are kept and how much it may send.
            # Without this the policy exists only in the operator's .env, and a
            # caller sending a file to somebody else's machine has to take the
            # retention on trust — the one part of a remote engine that should
            # never be implicit.
            "uploads": {
                "ttl_hours": settings.upload_ttl_hours,
                "expire_from": "last_use",
                "max_bytes": settings.max_upload_bytes,
                "total_bytes": settings.uploads_total_bytes,
            },
        }

    @app.get("/api/v1/folders", tags=["system"])
    def list_folders() -> dict:
        """Existing sub-folders of the delivery root, so a client can offer them
        as destination choices. The empty string denotes the root itself."""
        root = settings.delivery_dir or (settings.data_dir / "delivery")
        folders = [""] + DeliveryStore(root).list_folders()
        return {"folders": folders}

    # --- uploads (ADR 0020) ------------------------------------------------------

    @app.post("/api/v1/uploads", tags=["uploads"], status_code=201)
    async def create_upload(file: UploadFile) -> dict:
        """Store bytes a client supplied, and return the id that references them.

        The one endpoint that lets a caller write to the engine's disk, so the
        limits are enforced here rather than trusted: the size is counted while
        streaming (never read from Content-Length), the quota is checked before
        accepting, and the file only becomes addressable once it is complete.
        The stored path is deliberately absent from the response — the id is
        the address.
        """
        store_root = settings.uploads_dir or (settings.data_dir / "uploads")
        uploads = UploadStore(store_root)
        if settings.uploads_total_bytes:
            used = uploads.total_bytes()
            if used >= settings.uploads_total_bytes:
                raise HTTPException(
                    status_code=507,
                    detail={
                        "code": "upload_quota_exceeded",
                        "message": (
                            f"the upload store holds {used} bytes, at or over the "
                            f"{settings.uploads_total_bytes}-byte quota"
                        ),
                    },
                )
        upload_id = new_id("upl")
        filename = file.filename or "upload"

        try:
            written = await asyncio.to_thread(
                _drain_upload, uploads, upload_id, filename, file.file, settings
            )
        except UploadTooLarge as exc:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "upload_too_large",
                    "message": str(exc),
                    "limit_bytes": exc.limit,
                },
            ) from exc
        record = {
            "id": upload_id,
            "filename": sanitize_filename(filename),
            # The client's declared type is recorded, never trusted: what a
            # file *is* comes from analysis, exactly as for any other source.
            "media_type": file.content_type or "",
            "size_bytes": written["size_bytes"],
            "sha256": written["sha256"],
            "path": written["path"],
        }
        store.register_upload(record)
        return _upload_view(store.get_upload(upload_id))

    @app.get("/api/v1/uploads/{upload_id}", tags=["uploads"])
    def get_upload(upload_id: str) -> dict:
        row = store.get_upload(upload_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"no upload {upload_id}"},
            )
        return _upload_view(row)

    @app.delete("/api/v1/uploads/{upload_id}", tags=["uploads"], status_code=204)
    def delete_upload(upload_id: str) -> Response:
        """Remove an upload before its TTL. Idempotent: deleting an unknown id
        succeeds, since the caller's intent — that it be gone — already holds."""
        row = store.get_upload(upload_id)
        if row is not None:
            store_root = settings.uploads_dir or (settings.data_dir / "uploads")
            UploadStore(store_root).remove(upload_id)
            store.delete_upload(upload_id)
        return Response(status_code=204)

    # --- analyses --------------------------------------------------------------

    @app.post("/api/v1/analyses", tags=["analyses"])
    def create_analysis(body: AnalysisRequest) -> dict:
        _reject_duplicate_source_ids(body.sources)
        try:
            analysis = analysis_service.analyze_sources(list(body.sources))
        except RequestRejected as exc:
            raise HTTPException(status_code=422, detail=exc.result.model_dump())
        return analysis.model_dump(mode="json")

    @app.get("/api/v1/analyses/{analysis_id}", tags=["analyses"])
    def get_analysis(analysis_id: str) -> dict:
        """Fetch a previously produced analysis by id (ADR 0014). A safe read:
        it never re-runs analysis. 404 if unknown, 410 if the record or its
        referenced facts have expired."""
        try:
            analysis = analysis_service.get_analysis(analysis_id)
        except AnalysisNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": codes.ANALYSIS_NOT_FOUND, "message": str(exc)},
            ) from exc
        except AnalysisExpired as exc:
            raise HTTPException(
                status_code=410,
                detail={"code": codes.ANALYSIS_EXPIRED, "message": str(exc)},
            ) from exc
        return analysis.model_dump(mode="json")

    # --- capabilities ----------------------------------------------------------

    @app.post(
        "/api/v1/capabilities",
        response_model=CapabilitiesResponse,
        tags=["capabilities"],
    )
    def resolve_capabilities(body: CapabilitiesRequest) -> CapabilitiesResponse:
        """Resolve, per source, the public capabilities the engine can offer —
        the single feed a dynamic UI renders from (ADR 0013). Analysis stays a
        separate concern; availability is recomputed here against the live
        installation and the effective policy (instance ∩ request constraints).

        Accepts either inline ``sources`` or an ``analysis_id`` (ADR 0014)."""
        sources = _resolve_source_input(
            body.sources, body.analysis_id, analysis_service
        )
        _reject_duplicate_source_ids(sources)
        try:
            analysis = analysis_service.analyze_sources(list(sources))
        except RequestRejected as exc:
            raise HTTPException(status_code=422, detail=exc.result.model_dump())
        overlay = RequestConstraints(
            allow_cloud_providers=(
                body.constraints.allow_cloud_providers if body.constraints else None
            )
        )
        policy = effective_policy(instance_policy_from_settings(settings), overlay)
        return CapabilitiesResponse(
            analysis_id=analysis.analysis_id,
            sources=[
                SourceCapabilities(
                    source_id=entry.source_id,
                    resource_type=entry.resource.resource_type,
                    title=entry.resource.title,
                    suggested_filename=suggest_base_name(entry.resource),
                    capabilities=capability_resolver.resolve(
                        facts_from_analysis(entry), policy
                    ),
                )
                for entry in analysis.sources
            ],
        )

    # --- jobs ------------------------------------------------------------------

    @app.post(
        "/api/v1/jobs", response_model=JobSubmitted, status_code=201, tags=["jobs"]
    )
    def submit_job(request: GenerationRequest, response: Response) -> JobSubmitted:
        # Accept sources XOR analysis_id (ADR 0014); resolve to concrete sources
        # so the pipeline (validation → planning) is unchanged downstream.
        sources = _resolve_source_input(
            request.sources, request.analysis_id, analysis_service
        )
        if request.analysis_id is not None:
            request = request.model_copy(
                update={"sources": sources, "analysis_id": None}
            )
        try:
            result = submit_generation(
                request.model_dump(mode="json", exclude_unset=True),
                request,
                store=store,
                settings=settings,
                providers=providers,
                analysis_service=analysis_service,
            )
        except RequestRejected as exc:
            error_codes = {issue.code for issue in exc.result.errors}
            raise HTTPException(
                status_code=_http_status_for(exc.result.phase, error_codes),
                detail=exc.result.model_dump(),
            )
        if not result.created:
            # Idempotent replay: same key, same body — the existing job.
            response.status_code = 200
        return JobSubmitted(
            job_id=result.job_id, status=result.status, warnings=result.warnings
        )

    @app.get("/api/v1/jobs", tags=["jobs"])
    def list_jobs(
        status: str | None = Query(None), limit: int = Query(200, le=1000)
    ) -> list[dict]:
        rows = store.list_jobs(status=status, limit=limit)
        # Human labels (first artifact's display name + count), one query for
        # the whole page: rows become recognizable without a per-job fetch.
        labels = store.artifact_labels([row["id"] for row in rows])
        views = []
        for row in rows:
            view = _job_view(row)
            view.update(labels.get(row["id"], {}))
            views.append(view)
        return views

    def _step_labels(job_id: str) -> dict[str, dict]:
        """Human context per step, from the plan snapshot.

        A collection step's params carry the member's title and ordinal
        ("3/6 · Trapped by plates…"); the step table deliberately stores only
        execution state, so the presentation join happens here — once, for
        every client — instead of each UI re-deriving titles from slugs.
        """
        path = JobStorage.from_settings(settings, job_id).snapshots / "plan.json"
        try:
            plan = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        labels: dict[str, dict] = {}
        for step in plan.get("steps") or []:
            params = step.get("params") or {}
            context = {
                key: params[key]
                for key in ("item_title", "member_index", "member_total")
                if params.get(key) is not None
            }
            if context:
                labels[step.get("id", "")] = context
        return labels

    @app.get("/api/v1/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str) -> dict:
        row = store.get_job(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        view = _job_view(row)
        view.update(store.artifact_labels([job_id]).get(job_id, {}))
        steps = store.list_steps(job_id)
        labels = _step_labels(job_id)
        for step in steps:
            step.update(labels.get(step["step_id"], {}))
        view["steps"] = steps
        return view

    @app.post("/api/v1/jobs/{job_id}/cancel", tags=["jobs"])
    def cancel_job(job_id: str) -> dict:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        store.request_cancel(job_id)
        return {"job_id": job_id, "cancel_requested": True}

    @app.post(
        "/api/v1/jobs/{job_id}/retry",
        response_model=JobSubmitted,
        status_code=201,
        tags=["jobs"],
    )
    def retry_job(job_id: str) -> JobSubmitted:
        """A retry is a NEW job re-running the same normalized request
        (fresh analysis + plan); terminal jobs are never resurrected."""
        row = store.get_job(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        if row["status"] not in JOB_TERMINAL:
            raise HTTPException(
                status_code=409,
                detail=f"job is '{row['status']}'; only terminal jobs can be retried",
            )
        payload = dict(row["request"])
        # The original key belongs to the original intent; a retried job never
        # carries it (a partially_succeeded original may still hold it).
        payload["execution"] = {
            **payload.get("execution", {}),
            "idempotency_key": None,
        }
        request = GenerationRequest.model_validate(payload)
        try:
            result = submit_generation(
                payload,
                request,
                store=store,
                settings=settings,
                providers=providers,
                analysis_service=analysis_service,
                retry_of=job_id,
            )
        except RequestRejected as exc:
            raise HTTPException(status_code=422, detail=exc.result.model_dump())
        return JobSubmitted(
            job_id=result.job_id, status=result.status, warnings=result.warnings
        )

    @app.get("/api/v1/jobs/{job_id}/events", tags=["jobs"])
    def job_events(job_id: str, after_sequence: int = Query(0, ge=0)) -> list[dict]:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        return store.list_events(job_id, after_sequence=after_sequence)

    @app.get("/api/v1/jobs/{job_id}/events/stream", tags=["jobs"])
    async def job_events_stream(
        job_id: str, request: Request, after_sequence: int = Query(0, ge=0)
    ) -> StreamingResponse:
        """Server-Sent Events over the persisted, replayable event log.
        Supports resuming via `Last-Event-ID` (or `after_sequence`); ends with
        an explicit `stream.end` event once the job is terminal."""
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        last_event_id = request.headers.get("last-event-id", "")
        if last_event_id.isdigit():
            after_sequence = max(after_sequence, int(last_event_id))

        async def generate():
            last = after_sequence
            idle_seconds = 0.0
            while True:
                events = await asyncio.to_thread(store.list_events, job_id, last)
                for event in events:
                    last = event["sequence"]
                    payload = json.dumps(event["data"])
                    yield (
                        f"id: {event['sequence']}\n"
                        f"event: {event['type']}\n"
                        f"data: {payload}\n\n"
                    )
                job = await asyncio.to_thread(store.get_job, job_id)
                if not events and job["status"] in JOB_TERMINAL:
                    yield "event: stream.end\ndata: {}\n\n"
                    return
                if await request.is_disconnected():
                    return
                if events:
                    idle_seconds = 0.0
                else:
                    idle_seconds += 0.5
                    if idle_seconds >= 15.0:
                        idle_seconds = 0.0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/jobs/{job_id}/artifacts", tags=["artifacts"])
    def job_artifacts(job_id: str) -> list[dict]:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        return store.list_artifacts(job_id)

    @app.get("/api/v1/jobs/{job_id}/logs", tags=["jobs"])
    def job_logs(job_id: str, tail: int = Query(400, ge=1, le=5000)) -> dict:
        """Per-step stdout/stderr logs for the admin console. Reads only from the
        job's own logs/ directory; each stream is tail-truncated."""
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        logs_dir = JobStorage(settings.data_dir, job_id).logs
        result: dict[str, dict] = {}
        if logs_dir.is_dir():
            for path in sorted(logs_dir.glob("*.log")):
                # filenames are "<step>.<stream>.log", backend-generated.
                parts = path.name.rsplit(".", 2)
                if len(parts) != 3:
                    continue
                step_id, stream, _ = parts
                try:
                    lines = path.read_text(errors="replace").splitlines()
                except OSError:
                    continue
                entry = result.setdefault(step_id, {})
                entry[stream] = "\n".join(lines[-tail:])
        return {"job_id": job_id, "logs": result}

    # --- artifacts -------------------------------------------------------------

    @app.get("/api/v1/artifacts/{artifact_id}", tags=["artifacts"])
    def get_artifact(artifact_id: str) -> dict:
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact

    @app.get("/api/v1/artifacts/{artifact_id}/content", tags=["artifacts"])
    def artifact_content(artifact_id: str) -> FileResponse:
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        path = (
            JobStorage(settings.data_dir, artifact["job_id"]).artifacts
            / artifact["filename"]
        )
        if not path.is_file():
            raise HTTPException(status_code=410, detail="artifact content is gone")
        return FileResponse(
            path,
            media_type=artifact["media_type"] or "application/octet-stream",
            # The user-facing name (ADR 0017); artifacts registered before the
            # naming engine fall back to their technical name.
            filename=artifact.get("display_filename") or artifact["filename"],
        )

    return app


def _credentials_info(settings) -> list[dict]:
    """Credential *metadata* for the UIs: is the cookie file there, and when
    was it last refreshed. Paths and mtimes are operator-facing facts (the
    console shows every other deployment path already); the file's content is
    the secret and never leaves the server (INV-009). Stat failures degrade to
    ``exists: false`` — a dangling declaration is precisely what this makes
    visible."""
    infos = []
    for cred_id, path in sorted(settings.credentials.items()):
        entry = {
            "id": cred_id,
            "path": str(path),
            "exists": False,
            "size_bytes": None,
            "updated_at": None,
        }
        try:
            stat = os.stat(str(path))
            entry.update(
                exists=True,
                size_bytes=stat.st_size,
                updated_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            )
        except OSError:
            pass
        infos.append(entry)
    return infos


def _job_view(row: dict) -> dict:
    return {
        "job_id": row["id"],
        "status": row["status"],
        "plan_id": row["plan_id"],
        "failure_policy": row["failure_policy"],
        "retry_of": row.get("retry_of", ""),
        "error": row["error"],
        "cancel_requested": row["cancel_requested"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "request": row.get("request"),
    }


# Lazily-built module-level app for `uvicorn content.api.app:app` (PEP 562):
# importing this module in tests has no filesystem side effects.
_app = None


def __getattr__(name: str):
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
