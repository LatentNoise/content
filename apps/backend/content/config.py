"""Runtime settings, read once from the environment.

Every limit that protects the host (timeouts, sizes, concurrency, filesystem
roots) lives here so it is configurable and visible in one place.
"""

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _to_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _to_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class ContentSettings:
    data_dir: Path
    db_path: Path
    delivery_dir: Path | None = None  # None = <data_dir>/delivery
    # ADR 0018: when on, every artifact whose output expresses no contrary
    # intent is also copied into the delivery library under its
    # display_filename. Off in code (the bare engine keeps the V1 behaviour
    # and never doubles disk silently), on in the packaged deployment — the
    # ADR 0010 pattern.
    delivery_default: bool = False
    # How the delivery library is shared (ADR 0018, revisited 2026-09-12).
    # This is a POLICY, never a question about the deployment mode — the same
    # rule as ADR 0030: nothing in the code asks "am I hosted", it asks what
    # the delivery policy is.
    #
    #   shared     one library for everyone. The default, and what a
    #              self-hosted instance keeps: /output is a library a human
    #              organises and a media server reads, so inserting an owner
    #              level would break paths that work today.
    #   per_owner  everything under <owner_id>/. What a hosted instance sets.
    #   off        delivery refused. A hosted instance with no server-side
    #              library at all: the user downloads instead.
    delivery_scope: str = "shared"
    # Storage roots (docs/storage.md). None = derived from data_dir.
    tmp_dir: Path | None = None  # None = <data_dir>/tmp
    cache_dir: Path | None = None  # None = <data_dir>/cache
    cache_enabled: bool = False  # V1: no inter-job cache / reuse (ADR 0009)
    max_concurrent_jobs: int = 2
    # How many members of one collection may execute at once (ADR 0019). A
    # politeness bound toward the provider, not a throughput feature: two
    # concurrent members are two concurrent downloads from the same host.
    # 1 = strictly sequential members.
    collection_member_concurrency: int = 2
    step_timeout_seconds: int = 3600
    analysis_timeout_seconds: int = 120
    analysis_ttl_hours: float = 72.0  # 3 days — URL info is cached this long
    max_artifact_bytes: int = 0  # 0 = unlimited
    # Client uploads (ADR 0020). uploads_dir None = <data_dir>/uploads.
    # max_upload_bytes is enforced WHILE streaming, never from Content-Length,
    # which is a claim; 0 = unlimited and is not a sane default for a network
    # service with no authentication.
    uploads_dir: Path | None = None
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB
    uploads_total_bytes: int = 20 * 1024 * 1024 * 1024  # quota, 0 = unlimited
    upload_ttl_hours: float = 24.0  # counted from LAST reference, not creation
    # Authentication (ADR 0030). `none` keeps the historical self-hosted
    # behaviour exactly: no credential asked, every request owned by the single
    # implicit user. `token` is the hosted contract: no valid credential, no
    # request. The rest of the codebase never branches on this — it always
    # receives an owner id.
    auth_mode: str = "none"
    # Whether this process runs the job worker. The API and the worker are the
    # same image and the same code: a deployment that answers requests sets
    # this to false and stays responsive, while one that does the heavy work
    # sets it to true. `claim_next_queued()` takes a job under BEGIN IMMEDIATE,
    # so several worker processes on one machine share the queue safely.
    worker_enabled: bool = True
    # --- signing in (ADR 0030 decision 4; ADR 0033) -------------------------
    # Where the sign-in link points, and where the cookie is valid. These are
    # deployment facts, not code: the same binary serves a LAN under
    # `content.k3s.lab` and production under `latentnoise.dev`.
    public_base_url: str = ""  # e.g. https://api.latentnoise.dev
    # The cookie's Domain. Empty = host-only, which is correct for a single
    # host. Set it to the PARENT (".latentnoise.dev") so one session is
    # recognised by all four surfaces — that is the whole reason they share a
    # parent.
    session_cookie_domain: str = ""
    session_cookie_name: str = "content_session"
    # Secure is on by default and turning it off is a local-development
    # affordance, never a production one: a session cookie over plain HTTP is
    # readable by anything on the path.
    session_cookie_secure: bool = True
    session_ttl_hours: float = 720.0  # 30 days, slid forward on use
    magic_link_ttl_minutes: float = 15.0
    magic_link_max_per_hour: int = 5
    # Where a sign-in may send the browser afterwards. An open redirect on a
    # sign-in endpoint is how a phishing link borrows your domain, so `next`
    # is checked against this list and nothing else is accepted.
    allowed_redirect_origins: tuple[str, ...] = field(default_factory=tuple)
    # The outbound-email service (ADR 0031). Empty URL = no mail is sent and
    # the link is logged instead, which is what a self-hosted instance and the
    # test suite do.
    mailer_url: str = ""
    mailer_api_key: str = ""
    mailer_timeout_seconds: float = 10.0
    product_name: str = "Content"
    allow_private_networks: bool = False
    allowed_input_roots: tuple[Path, ...] = field(default_factory=tuple)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""  # empty = first installed model (deterministic)
    # Ceiling on the context window the engine asks Ollama for. The window is
    # sized per request from the prompt; this bounds it, because the memory is
    # the daemon's and the engine cannot see how much of it there is. 0 leaves
    # the choice to the daemon entirely (the pre-0.6.8 behaviour). Raising it
    # extends how long a recording can be summarised whole — at the cost of RAM
    # on the machine running Ollama.
    ollama_max_context: int = 32768
    # Speech-to-text (audio.transcribe) model, used when the optional [stt]
    # extra (faster-whisper) is installed. Absent extra = variants unavailable.
    whisper_model: str = "small"
    # --- PDF rendering (document.render_pdf) ---------------------------------
    # Which implementation renders PDFs. "auto" prefers Typst when its binary is
    # healthy and falls back to ReportLab, so a deployment without the binary
    # keeps working instead of losing the output type.
    pdf_renderer: str = "auto"
    typst_binary: str = "typst"
    # A server-side template *name*, never a path and never Typst source: the
    # public contract carries no renderer options (see docs/contract.md).
    pdf_template: str = "default"
    # TrueType font directory or file for PDF output, used when the text needs
    # characters the built-in faces cannot draw (any non-Latin script). Empty =
    # look in the renderer's default font paths. Characters no available font
    # covers fail the step rather than being silently dropped.
    pdf_font: str = ""
    # What to do when the text needs characters no available font can draw:
    # "error" refuses the step, "replace" substitutes a visible placeholder,
    # "warn" renders unchanged. Operator policy, identical for every renderer,
    # and never part of the public ArtifactRequest.
    pdf_missing_glyphs: str = "replace"
    # Browser cross-origin access to the API. Empty (default) = no CORS headers:
    # non-browser clients (curl, SDK, scripts) are always unaffected, and
    # drive-by requests from arbitrary websites stay blocked. Set explicit
    # origins (comma-separated, or "*") to allow a browser JS client.
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    # Cloud LLM summarizers (text.summarize). A key present enables the runner;
    # they are `cloud` so constraints.privacy.allow_cloud_providers can exclude
    # them. Tokens are secrets — never exposed by the API.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Language preferences (client defaults, HomeTube parity). They order audio
    # tracks (VO first, then primary, then secondaries) and seed default
    # subtitles; the request stays concrete (a resolved list of languages).
    language_primary: str = ""
    languages_secondaries: tuple[str, ...] = field(default_factory=tuple)
    vo_first: bool = True
    primary_include_subtitles: bool = True
    # Operator-trusted extra yt-dlp CLI args, appended to every invocation
    # (HomeTube's YTDLP_CUSTOM_ARGS). Unrestricted — the operator controls it.
    ytdlp_extra_args: tuple[str, ...] = field(default_factory=tuple)
    # AGPL §13: users interacting with a *modified* Content over a network must
    # be able to obtain its Corresponding Source. Operators who deploy a fork
    # MUST point this at their own source — leaving it on upstream would tell
    # their users something untrue. Exposed by /api/v1/system and linked in the
    # UIs. Empty = no offer is made (only correct for an unmodified deployment
    # whose users can already find upstream).
    source_url: str = "https://github.com/LatentNoise/content"
    # Instance notifications (content/notifications.py). The release check is
    # opt-in: empty URL = no outbound call and no banner. The forge is not
    # hard-coded because Content lives on Forgejo today and is heading for a
    # public GitHub release; both answer the same `tag_name` shape.
    release_check_url: str = ""
    release_page_url: str = ""  # where "View the release" points (optional)
    release_check_ttl_hours: float = 6.0
    # Age at which the installed yt-dlp is called out as stale (D-20).
    # 0 = off — the default. Age alone cannot tell "stale" from "newest
    # available": yt-dlp's release cadence is irregular, so a freshly built
    # image carrying the pinned, most recent upstream release can already be
    # "35 days old" and the banner would nag every fresh install about a
    # situation nobody can improve. Upstream freshness is the maintainer's
    # loop (the weekly base-image check files an issue); users hear about it
    # through Content releases. Operators who rebuild rarely can opt back in.
    ytdlp_max_age_days: int = 0
    # Server-side credentials: id -> cookies file path. Sources reference these
    # by `auth.credential_id`; the secret content never enters a request.
    credentials: dict[str, Path] = field(default_factory=dict)


def uploads_root(settings: "ContentSettings") -> Path:
    """Where the engine keeps client uploads. Engine-owned, so it is always
    readable back — and deliberately NOT part of `allowed_input_roots`, which
    is the operator's policy for `file` sources (ADR 0020)."""
    return settings.uploads_dir or (settings.data_dir / "uploads")


def _parse_credentials(raw: str | None) -> dict[str, Path]:
    """Parse ``CONTENT_CREDENTIALS`` (``id=path,id2=path2``). Malformed entries
    are skipped so a bad value never prevents startup."""
    credentials: dict[str, Path] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        cred_id, path = entry.split("=", 1)
        cred_id, path = cred_id.strip(), path.strip()
        if cred_id and path:
            credentials[cred_id] = Path(path).expanduser()
    return credentials


def _mask_secret(raw: str) -> str:
    """Never reveal a secret. Report only presence and length (INV-009)."""
    return f"set · {len(raw)} chars" if raw else "unset"


def describe_environment(
    settings: ContentSettings, environ: Mapping[str, str] | None = None
) -> list[dict]:
    """The canonical inventory of the ``CONTENT_*`` environment variables the
    engine reads, with their *effective* value (resolved into ``settings``),
    whether the value came from the environment or a built-in default, a
    category, a one-line description, and secret masking.

    This is the single source of truth behind the admin console's Environment
    view and doubles as living configuration documentation. Secrets are never
    returned — only their presence and length (INV-009).
    """
    env = os.environ if environ is None else environ
    delivery = settings.delivery_dir or settings.data_dir / "delivery"
    tmp = settings.tmp_dir or settings.data_dir / "tmp"
    cache = settings.cache_dir or settings.data_dir / "cache"
    creds = ", ".join(sorted(settings.credentials)) or "—"

    # (name, category, secret, effective value, description)
    specs: list[tuple[str, str, bool, str, str]] = [
        # Storage roots (docs/storage.md)
        (
            "CONTENT_DATA_DIR",
            "storage",
            False,
            str(settings.data_dir),
            "Root holding the database, work, artifacts, tmp and cache.",
        ),
        (
            "CONTENT_DB_PATH",
            "storage",
            False,
            str(settings.db_path),
            "SQLite database file — the source of truth.",
        ),
        (
            "CONTENT_DELIVERY_DIR",
            "storage",
            False,
            str(delivery),
            "Where finished artifacts are delivered for the user.",
        ),
        (
            "CONTENT_DELIVERY_DEFAULT",
            "storage",
            False,
            str(settings.delivery_default).lower(),
            "Deliver every artifact into the library by default (ADR 0018).",
        ),
        (
            "CONTENT_DELIVERY_SCOPE",
            "storage",
            False,
            settings.delivery_scope,
            "How the library is shared: 'shared' (one for everyone), "
            "'per_owner' (a subtree each) or 'off' (no delivery).",
        ),
        (
            "CONTENT_TMP_ROOT",
            "storage",
            False,
            str(tmp),
            "Disposable scratch space for in-flight steps.",
        ),
        (
            "CONTENT_CACHE_ROOT",
            "storage",
            False,
            str(cache),
            "Reusable cache root (analyses, content-addressed artifacts).",
        ),
        # Execution limits
        (
            "CONTENT_CACHE_ENABLED",
            "execution",
            False,
            str(settings.cache_enabled),
            "Enable inter-job reuse of analyses and artifacts.",
        ),
        (
            "CONTENT_MAX_CONCURRENT_JOBS",
            "execution",
            False,
            str(settings.max_concurrent_jobs),
            "How many jobs run at once.",
        ),
        (
            "CONTENT_STEP_TIMEOUT_SECONDS",
            "execution",
            False,
            str(settings.step_timeout_seconds),
            "Hard timeout for a single step.",
        ),
        (
            "CONTENT_ANALYSIS_TIMEOUT_SECONDS",
            "execution",
            False,
            str(settings.analysis_timeout_seconds),
            "Hard timeout for URL analysis.",
        ),
        (
            "CONTENT_ANALYSIS_TTL_HOURS",
            "execution",
            False,
            f"{settings.analysis_ttl_hours:g}",
            "How long analysis results are cached.",
        ),
        (
            "CONTENT_MAX_ARTIFACT_BYTES",
            "execution",
            False,
            str(settings.max_artifact_bytes),
            "Per-artifact size ceiling (0 = unlimited).",
        ),
        # Security
        (
            "CONTENT_AUTH_MODE",
            "security",
            False,
            settings.auth_mode,
            "Identity mode: 'none' (self-hosted, single implicit user) "
            "or 'token' (hosted, credential required).",
        ),
        (
            "CONTENT_WORKER_ENABLED",
            "execution",
            False,
            str(settings.worker_enabled).lower(),
            "Whether this process runs the job worker. False keeps the "
            "process answering requests only; the same image with True does "
            "the work.",
        ),
        (
            "CONTENT_PUBLIC_BASE_URL",
            "security",
            False,
            settings.public_base_url,
            "Public base URL of this engine, used to build sign-in links.",
        ),
        (
            "CONTENT_SESSION_COOKIE_DOMAIN",
            "security",
            False,
            settings.session_cookie_domain,
            "Domain of the session cookie. The PARENT domain makes one "
            "session work across every surface; empty = host-only.",
        ),
        (
            "CONTENT_SESSION_COOKIE_SECURE",
            "security",
            False,
            str(settings.session_cookie_secure).lower(),
            "Send the session cookie over HTTPS only. Off is a local-"
            "development affordance, never a production one.",
        ),
        (
            "CONTENT_SESSION_TTL_HOURS",
            "security",
            False,
            f"{settings.session_ttl_hours:g}",
            "How long a session lives, slid forward while it is used.",
        ),
        (
            "CONTENT_MAGIC_LINK_TTL_MINUTES",
            "security",
            False,
            f"{settings.magic_link_ttl_minutes:g}",
            "How long a sign-in link stays valid.",
        ),
        (
            "CONTENT_MAGIC_LINK_MAX_PER_HOUR",
            "security",
            False,
            str(settings.magic_link_max_per_hour),
            "How many sign-in links one address may ask for per hour.",
        ),
        (
            "CONTENT_ALLOWED_REDIRECT_ORIGINS",
            "security",
            False,
            ",".join(settings.allowed_redirect_origins),
            "Origins a sign-in may redirect to. Everything else is refused.",
        ),
        (
            "CONTENT_MAILER_URL",
            "security",
            False,
            settings.mailer_url,
            "The outbound-email service (ADR 0031). Empty = no mail is sent.",
        ),
        (
            "CONTENT_MAILER_API_KEY",
            "security",
            True,
            _mask_secret(settings.mailer_api_key),
            "Credential this engine presents to the mailer.",
        ),
        (
            "CONTENT_ALLOW_PRIVATE_NETWORKS",
            "security",
            False,
            str(settings.allow_private_networks),
            "Allow sources to resolve to private/loopback addresses.",
        ),
        (
            "CONTENT_CORS_ORIGINS",
            "security",
            False,
            ", ".join(settings.cors_origins)
            or "— (no CORS: browser cross-origin clients blocked; curl/SDK unaffected)",
            "Origins allowed to call the API from a browser (empty = none).",
        ),
        (
            "CONTENT_ALLOWED_INPUT_ROOTS",
            "security",
            False,
            os.pathsep.join(str(p) for p in settings.allowed_input_roots) or "—",
            "Filesystem roots local file sources may read from.",
        ),
        # Providers / processors
        (
            "CONTENT_OLLAMA_URL",
            "providers",
            False,
            settings.ollama_url,
            "Ollama endpoint for local (private) summarization.",
        ),
        (
            "CONTENT_OLLAMA_MODEL",
            "providers",
            False,
            settings.ollama_model or "auto",
            "Ollama model; empty = first installed (deterministic).",
        ),
        (
            "CONTENT_WHISPER_MODEL",
            "providers",
            False,
            settings.whisper_model,
            "Speech-to-text model (needs the optional [stt] extra).",
        ),
        (
            "CONTENT_PDF_RENDERER",
            "providers",
            False,
            settings.pdf_renderer,
            "PDF renderer: auto (prefer Typst) | typst | reportlab.",
        ),
        (
            "CONTENT_TYPST_BINARY",
            "providers",
            False,
            settings.typst_binary,
            "Typst executable used by the typst renderer.",
        ),
        (
            "CONTENT_PDF_TEMPLATE",
            "providers",
            False,
            settings.pdf_template,
            "Server-side PDF template name (never a path or template source).",
        ),
        (
            "CONTENT_PDF_MISSING_GLYPHS",
            "providers",
            False,
            settings.pdf_missing_glyphs,
            "Undrawable characters: error | replace | warn.",
        ),
        (
            "CONTENT_PDF_FONT",
            "providers",
            False,
            settings.pdf_font,
            "TrueType font for non-Latin PDF output (needs the optional [pdf] extra).",
        ),
        (
            "CONTENT_ANTHROPIC_API_KEY",
            "providers",
            True,
            _mask_secret(settings.anthropic_api_key),
            "Enables the Anthropic cloud summarizer (text.summarize).",
        ),
        (
            "CONTENT_ANTHROPIC_MODEL",
            "providers",
            False,
            settings.anthropic_model,
            "Anthropic model used when a key is set.",
        ),
        (
            "CONTENT_OPENAI_API_KEY",
            "providers",
            True,
            _mask_secret(settings.openai_api_key),
            "Enables the OpenAI cloud summarizer (text.summarize).",
        ),
        (
            "CONTENT_OPENAI_MODEL",
            "providers",
            False,
            settings.openai_model,
            "OpenAI model used when a key is set.",
        ),
        (
            "CONTENT_YTDLP_EXTRA_ARGS",
            "providers",
            False,
            " ".join(settings.ytdlp_extra_args) or "—",
            "Operator-trusted extra yt-dlp CLI args, appended to every call.",
        ),
        # Credentials (ids only — never the cookie paths/content)
        (
            "CONTENT_CREDENTIALS",
            "credentials",
            True,
            creds,
            "Server-side credential ids (id=path,…); sources reference the id.",
        ),
        # Language preferences
        (
            "CONTENT_LANGUAGE_PRIMARY",
            "language",
            False,
            settings.language_primary or "—",
            "Primary preferred language.",
        ),
        (
            "CONTENT_LANGUAGES_SECONDARIES",
            "language",
            False,
            ", ".join(settings.languages_secondaries) or "—",
            "Additional preferred languages, in order.",
        ),
        (
            "CONTENT_VO_FIRST",
            "language",
            False,
            str(settings.vo_first),
            "Order the original-version audio track first.",
        ),
        (
            "CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES",
            "language",
            False,
            str(settings.primary_include_subtitles),
            "Include the primary language in the default subtitle selection.",
        ),
        # Legal / compliance
        (
            "CONTENT_SOURCE_URL",
            "legal",
            False,
            settings.source_url or "— (no source offer made)",
            "Corresponding Source for THIS deployment (AGPL §13) — "
            "change it if you deployed a modified Content.",
        ),
        # Notifications
        (
            "CONTENT_RELEASE_CHECK_URL",
            "notifications",
            False,
            settings.release_check_url or "— (no release check)",
            "Release API to poll for a newer version (empty = feature off).",
        ),
        (
            "CONTENT_RELEASE_PAGE_URL",
            "notifications",
            False,
            settings.release_page_url or "—",
            "Human page the release notification links to.",
        ),
        (
            "CONTENT_RELEASE_CHECK_TTL_HOURS",
            "notifications",
            False,
            str(settings.release_check_ttl_hours),
            "How long a release lookup is cached (a page render never calls out).",
        ),
        (
            "CONTENT_YTDLP_MAX_AGE_DAYS",
            "notifications",
            False,
            str(settings.ytdlp_max_age_days),
            "Age at which the installed yt-dlp is flagged as stale (0 = off, "
            "the default — age cannot tell stale from newest-available).",
        ),
    ]
    return [
        {
            "name": name,
            "category": category,
            "secret": secret,
            "is_set": name in env,
            "value": value,
            "description": desc,
        }
        for name, category, secret, value, desc in specs
    ]


def settings_from_env() -> ContentSettings:
    data_dir = Path(os.getenv("CONTENT_DATA_DIR", "./data")).resolve()
    db_path = Path(os.getenv("CONTENT_DB_PATH", str(data_dir / "content.db")))
    delivery_raw = os.getenv("CONTENT_DELIVERY_DIR")
    delivery_dir = (
        Path(delivery_raw).resolve() if delivery_raw else data_dir / "delivery"
    )
    tmp_raw = os.getenv("CONTENT_TMP_ROOT")
    tmp_dir = Path(tmp_raw).resolve() if tmp_raw else data_dir / "tmp"
    cache_raw = os.getenv("CONTENT_CACHE_ROOT")
    cache_dir = Path(cache_raw).resolve() if cache_raw else data_dir / "cache"
    uploads_raw = os.getenv("CONTENT_UPLOADS_ROOT")
    uploads_dir = Path(uploads_raw).resolve() if uploads_raw else data_dir / "uploads"
    delivery_scope_raw = (
        (os.getenv("CONTENT_DELIVERY_SCOPE") or "shared").strip().lower()
    )
    if delivery_scope_raw not in {"shared", "per_owner", "off"}:
        raise ValueError(
            "CONTENT_DELIVERY_SCOPE must be 'shared', 'per_owner' or 'off', "
            f"got {delivery_scope_raw!r}"
        )
    auth_mode_raw = (os.getenv("CONTENT_AUTH_MODE") or "none").strip().lower()
    if auth_mode_raw not in {"none", "token"}:
        raise ValueError(
            f"CONTENT_AUTH_MODE must be 'none' or 'token', got {auth_mode_raw!r}"
        )
    roots = tuple(
        Path(p).resolve()
        for p in os.getenv("CONTENT_ALLOWED_INPUT_ROOTS", "").split(":")
        if p.strip()
    )
    return ContentSettings(
        data_dir=data_dir,
        db_path=db_path,
        delivery_dir=delivery_dir,
        delivery_default=_to_bool(os.getenv("CONTENT_DELIVERY_DEFAULT"), False),
        delivery_scope=delivery_scope_raw,
        tmp_dir=tmp_dir,
        cache_dir=cache_dir,
        cache_enabled=_to_bool(os.getenv("CONTENT_CACHE_ENABLED"), False),
        max_concurrent_jobs=max(
            1, _to_int(os.getenv("CONTENT_MAX_CONCURRENT_JOBS"), 2)
        ),
        collection_member_concurrency=max(
            1, _to_int(os.getenv("CONTENT_COLLECTION_MEMBER_CONCURRENCY"), 2)
        ),
        step_timeout_seconds=_to_int(os.getenv("CONTENT_STEP_TIMEOUT_SECONDS"), 3600),
        analysis_timeout_seconds=_to_int(
            os.getenv("CONTENT_ANALYSIS_TIMEOUT_SECONDS"), 120
        ),
        analysis_ttl_hours=_to_float(os.getenv("CONTENT_ANALYSIS_TTL_HOURS"), 72.0),
        max_artifact_bytes=_to_int(os.getenv("CONTENT_MAX_ARTIFACT_BYTES"), 0),
        uploads_dir=uploads_dir,
        max_upload_bytes=_to_int(
            os.getenv("CONTENT_MAX_UPLOAD_BYTES"), 2 * 1024 * 1024 * 1024
        ),
        uploads_total_bytes=_to_int(
            os.getenv("CONTENT_UPLOADS_TOTAL_BYTES"), 20 * 1024 * 1024 * 1024
        ),
        upload_ttl_hours=_to_float(os.getenv("CONTENT_UPLOAD_TTL_HOURS"), 24.0),
        auth_mode=auth_mode_raw,
        worker_enabled=_to_bool(os.getenv("CONTENT_WORKER_ENABLED"), True),
        public_base_url=(os.getenv("CONTENT_PUBLIC_BASE_URL") or "")
        .strip()
        .rstrip("/"),
        session_cookie_domain=(
            os.getenv("CONTENT_SESSION_COOKIE_DOMAIN") or ""
        ).strip(),
        session_cookie_name=(
            os.getenv("CONTENT_SESSION_COOKIE_NAME") or "content_session"
        ).strip(),
        session_cookie_secure=_to_bool(
            os.getenv("CONTENT_SESSION_COOKIE_SECURE"), True
        ),
        session_ttl_hours=_to_float(os.getenv("CONTENT_SESSION_TTL_HOURS"), 720.0),
        magic_link_ttl_minutes=_to_float(
            os.getenv("CONTENT_MAGIC_LINK_TTL_MINUTES"), 15.0
        ),
        magic_link_max_per_hour=max(
            1, _to_int(os.getenv("CONTENT_MAGIC_LINK_MAX_PER_HOUR"), 5)
        ),
        allowed_redirect_origins=tuple(
            origin.strip().rstrip("/")
            for origin in (os.getenv("CONTENT_ALLOWED_REDIRECT_ORIGINS") or "").split(
                ","
            )
            if origin.strip()
        ),
        mailer_url=(os.getenv("CONTENT_MAILER_URL") or "").strip().rstrip("/"),
        mailer_api_key=(os.getenv("CONTENT_MAILER_API_KEY") or "").strip(),
        mailer_timeout_seconds=_to_float(
            os.getenv("CONTENT_MAILER_TIMEOUT_SECONDS"), 10.0
        ),
        product_name=(os.getenv("CONTENT_PRODUCT_NAME") or "Content").strip(),
        allow_private_networks=_to_bool(
            os.getenv("CONTENT_ALLOW_PRIVATE_NETWORKS"), False
        ),
        allowed_input_roots=roots,
        ollama_url=os.getenv("CONTENT_OLLAMA_URL", "http://localhost:11434"),
        ollama_model=os.getenv("CONTENT_OLLAMA_MODEL", ""),
        ollama_max_context=_to_int(os.getenv("CONTENT_OLLAMA_MAX_CONTEXT"), 32768),
        whisper_model=os.getenv("CONTENT_WHISPER_MODEL", "small"),
        pdf_renderer=os.getenv("CONTENT_PDF_RENDERER", "auto").strip().lower(),
        typst_binary=os.getenv("CONTENT_TYPST_BINARY", "typst"),
        pdf_template=os.getenv("CONTENT_PDF_TEMPLATE", "default"),
        pdf_font=os.getenv("CONTENT_PDF_FONT", ""),
        pdf_missing_glyphs=os.getenv("CONTENT_PDF_MISSING_GLYPHS", "replace"),
        cors_origins=tuple(
            origin.strip()
            for origin in os.getenv("CONTENT_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ),
        anthropic_api_key=os.getenv("CONTENT_ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=os.getenv("CONTENT_ANTHROPIC_MODEL", "claude-sonnet-5").strip(),
        openai_api_key=os.getenv("CONTENT_OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("CONTENT_OPENAI_MODEL", "gpt-4o-mini").strip(),
        credentials=_parse_credentials(os.getenv("CONTENT_CREDENTIALS")),
        language_primary=os.getenv("CONTENT_LANGUAGE_PRIMARY", "").strip(),
        languages_secondaries=tuple(
            lang.strip()
            for lang in os.getenv("CONTENT_LANGUAGES_SECONDARIES", "").split(",")
            if lang.strip()
        ),
        vo_first=_to_bool(os.getenv("CONTENT_VO_FIRST"), True),
        primary_include_subtitles=_to_bool(
            os.getenv("CONTENT_LANGUAGE_PRIMARY_INCLUDED_IN_SUBTITLES"), True
        ),
        ytdlp_extra_args=tuple(shlex.split(os.getenv("CONTENT_YTDLP_EXTRA_ARGS", ""))),
        source_url=os.getenv(
            "CONTENT_SOURCE_URL", "https://github.com/LatentNoise/content"
        ).strip(),
        release_check_url=os.getenv("CONTENT_RELEASE_CHECK_URL", "").strip(),
        release_page_url=os.getenv("CONTENT_RELEASE_PAGE_URL", "").strip(),
        release_check_ttl_hours=_to_float(
            os.getenv("CONTENT_RELEASE_CHECK_TTL_HOURS"), 6.0
        ),
        ytdlp_max_age_days=_to_int(os.getenv("CONTENT_YTDLP_MAX_AGE_DAYS"), 0),
    )
